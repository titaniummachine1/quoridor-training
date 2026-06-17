#!/usr/bin/env python3
"""Collect MCTS attention labels from quoridor-zero.ink (50–400 visit rollouts).

Trains search-budget / attention distillation — NOT per-node eval, NOT main WDL.

    python -m training.zero_teacher.collect_budget --from-db --limit 100 --visits 400
    python -m training.zero_teacher.collect_budget --visits 50 --bot-plies 40
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training"))

from datagen import DB_PATH, load_games_from_db  # noqa: E402
from move_codec import pack_moves  # noqa: E402
from zero_teacher.client import (  # noqa: E402
    START_STATE,
    ZeroSettings,
    ZeroTeacherClient,
    ace_moves_to_zero_state,
    apply_zero_move,
    search_budget_features,
    search_pressure_from_zero,
)
from zero_teacher.paths import DEFAULT_LABELS  # noqa: E402


def existing_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = row.get("moves_bin")
        if key:
            keys.add(str(key))
    return keys


def sample_db_prefixes(
    *,
    limit: int,
    min_ply: int,
    max_ply: int,
    seed: int,
    skip: set[str],
) -> list[tuple[list[str], int, str, str]]:
    rng = random.Random(seed)
    games = load_games_from_db(DB_PATH)
    candidates: list[tuple[list[str], int, str, str]] = []
    for moves, outcome, src in games:
        hi = min(max_ply, len(moves))
        for ply in range(min_ply, hi + 1):
            prefix = moves[:ply]
            key = base64.b64encode(pack_moves(prefix)).decode("ascii")
            if key not in skip:
                candidates.append((prefix, outcome, src, key))
    rng.shuffle(candidates)
    return candidates[:limit]


def _row(
    *,
    key: str,
    moves: list[str],
    outcome: int,
    src: str,
    settings: ZeroSettings,
    feat: dict,
    chunks: list[dict],
    pressure: float,
) -> dict:
    return {
        "schema": "zero-search-budget-v1",
        "teacher": "quoridor-zero.ink",
        "moves_bin": key,
        "moves": moves,
        "outcome": outcome,
        "src": src,
        "ply": len(moves),
        "settings": settings.as_dict(),
        "search": {
            "root_value": feat["root_value"],
            "total_visits": feat["total_visits"],
            "top_visit_fraction": feat["top_visit_fraction"],
            "visit_entropy": feat["visit_entropy"],
            "prior_visit_gap": feat["prior_visit_gap"],
            "top_moves": feat["top_moves"],
        },
        "stream_last": chunks[-1] if chunks else None,
        "search_pressure": pressure,
    }


def collect_from_db(client: ZeroTeacherClient, args, out_path: Path) -> int:
    skip = existing_keys(out_path)
    prefixes = sample_db_prefixes(
        limit=args.limit,
        min_ply=args.min_ply,
        max_ply=args.max_ply,
        seed=args.seed,
        skip=skip,
    )
    settings = ZeroSettings(visits=args.visits, threads=args.threads)
    written = 0
    with out_path.open("a", encoding="utf-8") as f:
        for i, (moves, outcome, src, key) in enumerate(prefixes, 1):
            try:
                state = ace_moves_to_zero_state(moves)
                client.position(state)
                search = client.search(state, settings)
                chunks = list(
                    client.continuous(state, settings, max_chunks=args.stream_chunks)
                )
            except Exception as e:
                print(f"skip {i}/{len(prefixes)} ply={len(moves)}: {e}", file=sys.stderr)
                continue
            feat = search_budget_features(search, top_k=args.top_k)
            pressure = search_pressure_from_zero(feat)
            f.write(
                json.dumps(
                    _row(
                        key=key,
                        moves=moves,
                        outcome=outcome,
                        src=src,
                        settings=settings,
                        feat=feat,
                        chunks=chunks,
                        pressure=pressure,
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )
            f.flush()
            written += 1
            print(
                f"{written:4d} ply={len(moves):3d} pressure={pressure:+.3f} "
                f"topVF={feat['top_visit_fraction']:.1%} visits={feat['total_visits']}",
                flush=True,
            )
    return written


def collect_from_bot(client: ZeroTeacherClient, args, out_path: Path) -> int:
    settings = ZeroSettings(visits=args.visits, threads=args.threads)
    state = dict(START_STATE)
    moves: list[str] = []
    written = 0
    with out_path.open("a", encoding="utf-8") as f:
        for _ in range(1, args.bot_plies + 1):
            search = client.search(state, settings)
            chunks = list(
                client.continuous(state, settings, max_chunks=args.stream_chunks)
            )
            feat = search_budget_features(search, top_k=args.top_k)
            pressure = search_pressure_from_zero(feat)
            key = base64.b64encode(pack_moves(moves)).decode("ascii")
            f.write(
                json.dumps(
                    _row(
                        key=key,
                        moves=list(moves),
                        outcome=0,
                        src="zero-bot",
                        settings=settings,
                        feat=feat,
                        chunks=chunks,
                        pressure=pressure,
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )
            f.flush()
            written += 1
            print(
                f"{written:4d} ply={len(moves):3d} pressure={pressure:+.3f} "
                f"topVF={feat['top_visit_fraction']:.1%}",
                flush=True,
            )
            bot = client.bot_move(state, settings)
            state = apply_zero_move(state, bot["move"])
            moves.append(_zero_move_text(bot["move"]))
            if state.get("winner") is not None:
                break
    return written


def _zero_move_text(move: dict) -> str:
    if move["kind"] == "pawn":
        cell = int(move["target"])
        col, row = cell % 9, cell // 9
        return f"{chr(ord('a') + col)}{row + 1}"
    ori = "h" if move["orientation"] == "horizontal" else "v"
    return f"{chr(ord('a') + int(move['x']))}{int(move['y']) + 1}{ori}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_LABELS))
    ap.add_argument("--base", default="https://quoridor-zero.ink")
    ap.add_argument("--model", default="resume-188/model_000159")
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--min-ply", type=int, default=4)
    ap.add_argument("--max-ply", type=int, default=80)
    ap.add_argument("--visits", type=int, default=400, help="MCTS rollouts (50 fast, 400 default)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--stream-chunks", type=int, default=8)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--bot-plies", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    client = ZeroTeacherClient(base=args.base, model_id=args.model)
    n = collect_from_db(client, args, out_path) if args.from_db else collect_from_bot(
        client, args, out_path
    )
    print(f"wrote {n} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
