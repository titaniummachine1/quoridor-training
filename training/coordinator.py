#!/usr/bin/env python3
"""Localhost coordinator — single writer for manifest matchups + games DB.

Parallel match workers POST upserts here instead of fighting over manifest.json,
.ingested_offset sidecars, or sqlite from many processes.

  POST /api/matchup   upsert cumulative W/L for a pairing
  GET  /api/matchup   lookup prior a_wins / b_wins
  POST /api/game      insert one game into all_games.db (SQLite only)
  POST /api/claim-pairing  atomically pick next game for a free slot
  POST /api/release-remote free remote slot after crash/skip
  GET  /api/scoreboard
  GET  /health
"""

from __future__ import annotations

import argparse
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from datagen import DB_PATH, insert_single_game, validate_game
from manifest import format_scoreboard, load_manifest, lookup_prior_wins, save_manifest, update_matchup
from swiss_tournament import MAX_REMOTE_PARALLEL, pick_one_pairing, pairing_game_entry

ROOT = Path(__file__).resolve().parent.parent

_lock = threading.Lock()
_active_remotes: set[str] = set()
DEFAULT_PORT = 8765


def _claim_pairing() -> dict | None:
    global _active_remotes
    manifest = load_manifest()
    allow_remote = len(_active_remotes) < MAX_REMOTE_PARALLEL
    pairing = pick_one_pairing(manifest, allow_remote=allow_remote)
    if pairing is None:
        return None
    game_id = uuid.uuid4().hex[:8]
    entry = pairing_game_entry(pairing, game_id, ROOT / "training" / "data")
    if pairing.kind == "remote":
        _active_remotes.add(game_id)
        entry["release_remote"] = True
    return entry


def _release_remote(game_id: str | None = None) -> None:
    global _active_remotes
    if game_id:
        _active_remotes.discard(game_id)
    else:
        if _active_remotes:
            _active_remotes.pop()


def _record_game_done() -> None:
    manifest = load_manifest()
    t = manifest.setdefault("tournament", {})
    t["mode"] = "random-pool"
    t["games"] = int(t.get("games", 0)) + 1
    save_manifest(manifest)


class CoordinatorHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        return

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            self._send_json(200, {"ok": True, "pool": True, "version": 2})
            return

        if path == "/api/pool-status":
            from swiss_tournament import eligible_pairings
            with _lock:
                pool = eligible_pairings(load_manifest())
                local_n = sum(1 for p in pool if p.kind == "local")
                remote_n = sum(1 for p in pool if p.kind == "remote")
            self._send_json(200, {
                "pairings": len(pool),
                "local_pairings": local_n,
                "remote_pairings": remote_n,
                "remote_in_flight": len(_active_remotes),
            })
            return

        if path == "/api/matchup":
            q = parse_qs(parsed.query)
            def one(key: str) -> str | None:
                v = q.get(key)
                return v[0] if v else None

            with _lock:
                a_w, b_w = lookup_prior_wins(
                    one("engine_a") or "",
                    one("engine_b") or "",
                    one("tc_a"),
                    one("tc_b"),
                )
            self._send_json(200, {"a_wins": a_w, "b_wins": b_w})
            return

        if path == "/api/scoreboard":
            with _lock:
                text = format_scoreboard(load_manifest())
            self._send_json(200, {"text": text})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        body = self._read_json()

        if path == "/api/matchup":
            required = ("engine_a", "engine_b", "a_wins", "b_wins")
            if any(k not in body for k in required):
                self._send_json(400, {"error": f"need {required}"})
                return
            with _lock:
                entry = update_matchup(
                    body["engine_a"],
                    body["engine_b"],
                    int(body["a_wins"]),
                    int(body["b_wins"]),
                    body.get("tc_a"),
                    body.get("tc_b"),
                    games_file=body.get("games_file"),
                    source=body.get("source"),
                )
            self._send_json(200, entry)
            return

        if path == "/api/claim-pairing":
            with _lock:
                entry = _claim_pairing()
            if entry is None:
                self._send_json(503, {"error": "no pairing available"})
                return
            self._send_json(200, entry)
            return

        if path == "/api/release-remote":
            game_id = body.get("game_id")
            with _lock:
                _release_remote(game_id)
            self._send_json(200, {"ok": True})
            return

        if path == "/api/game":
            moves = body.get("moves")
            result = body.get("result")
            if not isinstance(moves, list) or result not in ("W", "B"):
                self._send_json(400, {"error": "need moves[] and result W|B"})
                return
            outcome = 1 if result == "W" else -1
            err = validate_game(moves, outcome)
            if err:
                self._send_json(400, {"error": err})
                return
            tag = body.get("tag") or body.get("source_tag") or ""

            with _lock:
                try:
                    gid = insert_single_game(moves, outcome, DB_PATH, tag)
                except ValueError as e:
                    self._send_json(400, {"error": str(e)})
                    return
                if body.get("release_remote"):
                    _release_remote(body.get("game_id"))
                _record_game_done()
            self._send_json(200, {"ok": True, "game_id": gid})
            return

        self._send_json(404, {"error": "not found"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    save_manifest(load_manifest())

    server = ThreadingHTTPServer((args.host, args.port), CoordinatorHandler)
    print(f"coordinator listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
