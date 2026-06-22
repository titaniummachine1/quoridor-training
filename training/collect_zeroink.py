#!/usr/bin/env python3
"""
Zero.ink self-play game collector.

Plays the zero.ink AlphaZero bot against itself using the fast
/api/analysis/policy endpoint (network-only, no MCTS overhead).

Each position is labelled with:
  - value:   win-probability for the current player (0..1)
  - policy:  [{move, prior}] — full distribution over legal moves
              (attention labels for future LMR policy-head training)
  - entropy: policy entropy reported by the server

Output: append-only JSONL at training/data/zeroink_games/games_<date>.jsonl
        One line = one board position.

Checkpoint: training/data/zeroink_games/collector_checkpoint.json
            Written atomically after every game; safe to kill and restart.

Constraints:
  - Serial requests only (one in-flight at a time).
  - Minimum REQUEST_DELAY seconds between calls.
  - Bounded exponential backoff on failure.
  - Never touches training/data/teacher_dataset/.
  - Never feeds records into the running trainer.
"""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE    = "https://quoridor-zero.ink"
MODEL_ID    = "resume-188/model_000159"
POLICY_URL  = f"{API_BASE}/api/analysis/policy"

OUT_DIR         = Path(__file__).parent / "data" / "zeroink_games"
CHECKPOINT_FILE = OUT_DIR / "collector_checkpoint.json"

REQUEST_DELAY    = 2.5         # seconds between every API call (conservative)
RETRY_BACKOFF    = [10, 30, 90]  # seconds; attempt 1, 2, 3 before giving up
REQUEST_TIMEOUT  = 45          # seconds per HTTP request

MAX_GAMES         = 10_000     # hard session cap (caller stops earlier)
MAX_MOVES_PER_GAME = 200       # safety cut-off; records as draw if reached

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def initial_state() -> dict:
    """Starting position in zero.ink API format."""
    return {
        "currentPlayer": 0,
        "player0Cell":   4,   # row0 col4 — P0 goal is row 8
        "player1Cell":   76,  # row8 col4 — P1 goal is row 0
        "player0Walls":  10,
        "player1Walls":  10,
        "horizontalWalls": [],
        "verticalWalls":   [],
    }


def is_terminal(state: dict) -> tuple[bool, int | None]:
    """(done, winner_index | None).  winner=None means draw / cutoff."""
    if state["player0Cell"] // 9 == 8:
        return True, 0
    if state["player1Cell"] // 9 == 0:
        return True, 1
    return False, None


def apply_move(state: dict, move: dict) -> dict:
    """Return a new state with *move* applied (zero.ink move object)."""
    s = {
        **state,
        "horizontalWalls": list(state["horizontalWalls"]),
        "verticalWalls":   list(state["verticalWalls"]),
    }
    p   = state["currentPlayer"]
    kind = (move.get("kind") or move.get("type") or "").lower()
    if kind == "pawn":
        s[f"player{p}Cell"] = move["target"]
    else:
        # orientation from analysis endpoint: "horizontal" or "vertical"
        ori = (move.get("orientation") or "").lower()
        s[f"player{p}Walls"] -= 1
        slot = {"x": int(move["x"]), "y": int(move["y"])}
        if ori.startswith("h"):
            s["horizontalWalls"].append(slot)
        else:
            s["verticalWalls"].append(slot)
    s["currentPlayer"] = 1 - p
    return s


def sample_move(moves: list[dict]) -> dict:
    """Temperature-1 sampling from prior distribution (adds game diversity)."""
    priors = [float(m.get("prior", 0.0)) for m in moves]
    total  = sum(priors)
    if total <= 0.0:
        return random.choice(moves)
    r, acc = random.random() * total, 0.0
    for m, p in zip(moves, priors):
        acc += p
        if acc >= r:
            return m
    return moves[-1]


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------

def call_policy(state: dict, session: requests.Session) -> tuple[dict | None, str | None]:
    """POST /api/analysis/policy with retry.  Returns (data, error_str)."""
    payload = {"state": state, "modelId": MODEL_ID}
    delays  = [0] + RETRY_BACKOFF
    for attempt, wait in enumerate(delays):
        if wait:
            print(f"    [retry {attempt}] waiting {wait}s ...", flush=True)
            time.sleep(wait)
        try:
            resp = session.post(POLICY_URL, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", RETRY_BACKOFF[-1]))
                print(f"    [rate-limited] Retry-After={retry_after}s", flush=True)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json(), None
        except Exception as exc:
            if attempt >= len(RETRY_BACKOFF):
                return None, str(exc)
            print(f"    [err attempt {attempt}] {exc}", flush=True)
    return None, "max retries exceeded"


# ---------------------------------------------------------------------------
# Checkpoint (atomic write)
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "games_completed": 0,
        "total_positions": 0,
        "total_failures":  0,
        "output_file":     None,
    }


def save_checkpoint(ckpt: dict) -> None:
    tmp = CHECKPOINT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(ckpt, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CHECKPOINT_FILE)


# ---------------------------------------------------------------------------
# Game loop
# ---------------------------------------------------------------------------

def play_game(
    game_id: str,
    out_fh,
    session: requests.Session,
) -> tuple[int, int]:
    """Play one game; write positions to *out_fh*.
    Returns (positions_written, api_failures)."""

    state            = initial_state()
    staged: list[dict] = []
    failures         = 0

    for move_num in range(MAX_MOVES_PER_GAME):
        done, winner = is_terminal(state)
        if done:
            break

        result, err = call_policy(state, session)
        time.sleep(REQUEST_DELAY)

        if err or result is None:
            failures += 1
            print(f"  [game {game_id} move {move_num}] policy error: {err}", flush=True)
            return 0, failures  # abandon; write nothing for partial game

        moves = result.get("moves") or []
        if not moves:
            failures += 1
            print(f"  [game {game_id} move {move_num}] empty move list", flush=True)
            return 0, failures

        value   = result.get("value")    # win-prob for currentPlayer (0..1)
        entropy = result.get("entropy")

        chosen_entry = sample_move(moves)
        chosen_move  = chosen_entry.get("move")
        if chosen_move is None:
            chosen_move = chosen_entry

        staged.append({
            "game_id":  game_id,
            "move_num": move_num,
            "state":    dict(state),
            "value":    value,
            "entropy":  entropy,
            # Full policy distribution — attention labels for LMR head
            "policy": [
                {"move": m.get("move", m), "prior": float(m.get("prior", 0.0))}
                for m in moves
            ],
            "move_chosen": chosen_move,
        })

        try:
            state = apply_move(state, chosen_move)
        except Exception as exc:
            failures += 1
            print(f"  [game {game_id} move {move_num}] apply_move: {exc}", flush=True)
            return 0, failures

    done, winner = is_terminal(state)
    if move_num + 1 >= MAX_MOVES_PER_GAME and not done:
        winner = None  # draw / cutoff

    ts      = datetime.now(timezone.utc).isoformat()
    outcome = {"winner": winner, "total_moves": len(staged)}

    for pos in staged:
        pos["game_outcome"] = outcome
        pos["ts"]           = ts
        out_fh.write(json.dumps(pos, separators=(",", ":")) + "\n")

    out_fh.flush()
    return len(staged), failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ckpt       = load_checkpoint()
    games_done = ckpt["games_completed"]
    total_pos  = ckpt["total_positions"]
    total_fail = ckpt["total_failures"]

    today    = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = Path(ckpt["output_file"]) if ckpt.get("output_file") else (OUT_DIR / f"games_{today}.jsonl")
    ckpt["output_file"] = str(out_path)

    print("=" * 60, flush=True)
    print("Zero.ink self-play collector", flush=True)
    print(f"  model   : {MODEL_ID}", flush=True)
    print(f"  output  : {out_path}", flush=True)
    print(f"  delay   : {REQUEST_DELAY}s/request  backoff {RETRY_BACKOFF}", flush=True)
    print(f"  resume  : games={games_done}  positions={total_pos}  failures={total_fail}", flush=True)
    print("=" * 60, flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "titanium-collector/1.0 (research; serial)"})

    with open(out_path, "a", encoding="utf-8") as out_fh:
        while games_done < MAX_GAMES:
            game_id = f"g{games_done + 1:06d}"
            t0      = time.perf_counter()

            pos_written, fails = play_game(game_id, out_fh, session)
            elapsed            = time.perf_counter() - t0

            games_done += 1
            total_pos  += pos_written
            total_fail += fails

            ckpt["games_completed"] = games_done
            ckpt["total_positions"] = total_pos
            ckpt["total_failures"]  = total_fail
            save_checkpoint(ckpt)

            status = "OK" if pos_written > 0 else "SKIP"
            print(
                f"[{status}] {game_id}  moves={pos_written:3d}  "
                f"elapsed={elapsed:5.0f}s  "
                f"total_games={games_done}  total_pos={total_pos}  "
                f"failures={total_fail}",
                flush=True,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
