#!/usr/bin/env python3
"""HTTP client for https://quoridor-zero.ink AlphaZero MCTS teacher API.

Attention / search-budget distillation only — not HalfPW eval per node.
See training/zero_teacher/REFERENCE.md.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator

DEFAULT_BASE = "https://quoridor-zero.ink"
DEFAULT_MODEL = "resume-188/model_000159"

START_STATE = {
    "currentPlayer": 0,
    "player0Cell": 36,
    "player1Cell": 76,
    "player0Walls": 10,
    "player1Walls": 10,
    "horizontalWalls": [],
    "verticalWalls": [],
}


@dataclass
class ZeroSettings:
    visits: int = 400
    batch_size: int = 16
    cpuct: float = 2.5
    threads: int = 2

    def as_dict(self) -> dict:
        return {
            "visits": self.visits,
            "batchSize": self.batch_size,
            "cpuct": self.cpuct,
            "threads": self.threads,
        }


class ZeroTeacherClient:
    def __init__(
        self,
        base: str = DEFAULT_BASE,
        model_id: str = DEFAULT_MODEL,
        timeout_sec: float = 90.0,
    ):
        self.base = base.rstrip("/")
        self.model_id = model_id
        self.timeout_sec = timeout_sec

    def _request(self, path: str, payload: dict | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path,
            data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method="POST" if body else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"{path} HTTP {e.code}: {detail[:500]}") from e

    def models(self) -> dict:
        return self._request("/api/models")

    def position(self, state: dict) -> dict:
        return self._request("/api/position", {"state": _compact_state(state)})

    def policy(self, state: dict) -> dict:
        return self._request(
            "/api/analysis/policy",
            {"state": _compact_state(state), "modelId": self.model_id},
        )

    def search(self, state: dict, settings: ZeroSettings | None = None) -> dict:
        return self._request(
            "/api/analysis/search",
            {
                "state": _compact_state(state),
                "modelId": self.model_id,
                "settings": (settings or ZeroSettings()).as_dict(),
            },
        )

    def bot_move(self, state: dict, settings: ZeroSettings | None = None) -> dict:
        return self._request(
            "/api/bot-move",
            {
                "state": _compact_state(state),
                "modelId": self.model_id,
                "settings": (settings or ZeroSettings()).as_dict(),
            },
        )

    def continuous(
        self,
        state: dict,
        settings: ZeroSettings | None = None,
        *,
        max_chunks: int | None = None,
    ) -> Iterator[dict]:
        payload = json.dumps(
            {
                "state": _compact_state(state),
                "modelId": self.model_id,
                "settings": (settings or ZeroSettings()).as_dict(),
            }
        ).encode()
        req = urllib.request.Request(
            self.base + "/api/analysis/continuous",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as r:
            buf = ""
            n = 0
            while True:
                line = r.readline().decode("utf-8", errors="replace")
                if not line:
                    break
                buf += line
                while "\n" in buf:
                    part, buf = buf.split("\n", 1)
                    part = part.strip()
                    if not part:
                        continue
                    yield json.loads(part)
                    n += 1
                    if max_chunks is not None and n >= max_chunks:
                        return


def _compact_state(state: dict) -> dict:
    return {
        "currentPlayer": int(state["currentPlayer"]),
        "player0Cell": int(state["player0Cell"]),
        "player1Cell": int(state["player1Cell"]),
        "player0Walls": int(state["player0Walls"]),
        "player1Walls": int(state["player1Walls"]),
        "horizontalWalls": [
            {"x": int(w["x"]), "y": int(w["y"])} for w in state.get("horizontalWalls", [])
        ],
        "verticalWalls": [
            {"x": int(w["x"]), "y": int(w["y"])} for w in state.get("verticalWalls", [])
        ],
    }


def apply_zero_move(state: dict, move: dict) -> dict:
    s = _compact_state(state)
    if move["kind"] == "pawn":
        target = int(move["target"])
        if s["currentPlayer"] == 0:
            s["player0Cell"] = target
        else:
            s["player1Cell"] = target
    else:
        wall = {"x": int(move["x"]), "y": int(move["y"])}
        if move["orientation"] == "vertical":
            s["verticalWalls"].append(wall)
        else:
            s["horizontalWalls"].append(wall)
        key = "player0Walls" if s["currentPlayer"] == 0 else "player1Walls"
        s[key] -= 1
    s["currentPlayer"] ^= 1
    return s


def ace_to_zero_move(ace: int) -> dict:
    if ace < 100:
        r, c = divmod(ace, 9)
        target = (8 - r) * 9 + c
        return {"kind": "pawn", "target": target, "orientation": "", "x": -1, "y": -1}
    if ace < 200:
        slot = ace - 100
        r, c = divmod(slot, 8)
        return {"kind": "wall", "target": -1, "orientation": "horizontal", "x": c, "y": 7 - r}
    slot = ace - 200
    r, c = divmod(slot, 8)
    return {"kind": "wall", "target": -1, "orientation": "vertical", "x": c, "y": 7 - r}


def ace_moves_to_zero_state(moves: list[str]) -> dict:
    from move_codec import algebraic_to_ace

    state = dict(START_STATE)
    for text in moves:
        state = apply_zero_move(state, ace_to_zero_move(algebraic_to_ace(text)))
    return state


def search_budget_features(search: dict, *, top_k: int = 8) -> dict:
    moves = list(search.get("moves") or [])
    moves.sort(key=lambda m: float(m.get("visitFraction", 0.0)), reverse=True)
    top = moves[:top_k]
    if not moves:
        return {
            "root_value": float(search.get("rootValue", 0.0)),
            "total_visits": int(search.get("totalVisits", 0)),
            "top_visit_fraction": 0.0,
            "visit_entropy": 0.0,
            "prior_visit_gap": 0.0,
            "top_moves": [],
        }

    vf = [max(0.0, float(m.get("visitFraction", 0.0))) for m in moves]
    s_vf = sum(vf) or 1.0
    entropy = 0.0
    for p in vf:
        if p > 0:
            q = p / s_vf
            entropy -= q * math.log(q)
    top_visit = float(top[0].get("visitFraction", 0.0))
    top_prior = float(top[0].get("prior", 0.0))
    return {
        "root_value": float(search.get("rootValue", 0.0)),
        "total_visits": int(search.get("totalVisits", 0)),
        "top_visit_fraction": top_visit,
        "visit_entropy": entropy,
        "prior_visit_gap": top_visit - top_prior,
        "top_moves": [
            {
                "move": m.get("move"),
                "prior": float(m.get("prior", 0.0)),
                "visits": int(m.get("visits", 0)),
                "visit_fraction": float(m.get("visitFraction", 0.0)),
                "q": float(m.get("q", 0.0)),
            }
            for m in top
        ],
    }


def search_pressure_from_zero(features: dict) -> float:
    """Map MCTS visit attention to [-1,+1] leaf budget target (sidecar only)."""
    concentration = min(1.0, float(features["top_visit_fraction"]) * 1.25)
    expansion = min(1.0, max(0.0, float(features["prior_visit_gap"])) * 4.0)
    volatility = min(1.0, float(features["visit_entropy"]) / 3.0)
    instability = 0.45 * (1.0 - concentration) + 0.35 * expansion + 0.20 * volatility
    return 2.0 * instability - 1.0
