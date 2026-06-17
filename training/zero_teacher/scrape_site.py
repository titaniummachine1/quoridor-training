#!/usr/bin/env python3
"""One-shot scrape of https://quoridor-zero.ink assets + API samples."""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zero_teacher.paths import SCRAPE_DIR  # noqa: E402

BASE = "https://quoridor-zero.ink"
MODEL = "resume-188/model_000159"
SETTINGS = {"visits": 400, "batchSize": 16, "cpuct": 2.5, "threads": 2}
START = {
    "currentPlayer": 0,
    "player0Cell": 36,
    "player1Cell": 76,
    "player0Walls": 10,
    "player1Walls": 10,
    "horizontalWalls": [],
    "verticalWalls": [],
}
OUT = SCRAPE_DIR


def get(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read(), r.headers.get("Content-Type", "")


def post(path: str, payload: dict | None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def scrape_assets() -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    html_bytes, _ = get(BASE + "/")
    (OUT / "index.html").write_bytes(html_bytes)
    saved.append("index.html")
    html = html_bytes.decode("utf-8", errors="replace")
    for rel in re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', html):
        url = rel if rel.startswith("http") else BASE + rel
        data, _ = get(url)
        fname = rel.split("/")[-1]
        (OUT / fname).write_bytes(data)
        saved.append(fname)
    return saved


def scrape_apis() -> dict:
    samples: dict = {}
    samples["models"] = post("/api/models", None)
    samples["position_start"] = post("/api/position", {"state": START})
    samples["policy_start"] = post(
        "/api/analysis/policy", {"state": START, "modelId": MODEL}
    )
    samples["search_start"] = post(
        "/api/analysis/search",
        {"state": START, "modelId": MODEL, "settings": SETTINGS},
    )
    samples["bot_move_start"] = post(
        "/api/bot-move",
        {"state": START, "modelId": MODEL, "settings": SETTINGS},
    )

    state = dict(START)
    opening: list[dict] = []
    for _ in range(6):
        r = post(
            "/api/bot-move",
            {"state": state, "modelId": MODEL, "settings": SETTINGS},
        )
        opening.append(r)
        state = r["stateAfter"]
        if state.get("winner") is not None:
            break
    samples["bot_opening_plies"] = opening
    samples["position_mid"] = post("/api/position", {"state": state})
    samples["policy_mid"] = post(
        "/api/analysis/policy", {"state": state, "modelId": MODEL}
    )
    samples["search_mid"] = post(
        "/api/analysis/search",
        {"state": state, "modelId": MODEL, "settings": SETTINGS},
    )

    req = urllib.request.Request(
        BASE + "/api/analysis/continuous",
        data=json.dumps(
            {"state": state, "modelId": MODEL, "settings": SETTINGS}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    chunks: list[dict] = []
    with urllib.request.urlopen(req, timeout=90) as r:
        buf = ""
        while len(chunks) < 12:
            line = r.readline().decode("utf-8", errors="replace")
            if not line:
                break
            buf += line
            while "\n" in buf:
                part, buf = buf.split("\n", 1)
                part = part.strip()
                if not part:
                    continue
                try:
                    chunks.append(json.loads(part))
                except json.JSONDecodeError:
                    chunks.append({"raw": part[:500]})
    samples["continuous_mid_chunks"] = chunks
    return samples


def main() -> int:
    assets = scrape_assets()
    apis = scrape_apis()
    (OUT / "api_samples.json").write_text(json.dumps(apis, indent=2), encoding="utf-8")
    meta = {
        "base": BASE,
        "scraped_assets": assets,
        "default_model": MODEL,
        "default_settings": SETTINGS,
        "api_endpoints": [
            "GET  /api/models",
            "POST /api/position",
            "POST /api/analysis/policy",
            "POST /api/analysis/search",
            "POST /api/analysis/continuous  (NDJSON stream)",
            "POST /api/bot-move",
        ],
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"scrape ok -> {OUT}")
    print(f"assets: {', '.join(assets)}")
    print(f"api_samples.json: {(OUT / 'api_samples.json').stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
