"""Training data pipeline for HalfPW retrain.

STORAGE MODEL
─────────────
Store only game sequences (moves + outcome) — not position snapshots.
Everything the trainer needs is derived on-demand at training time by
replaying each game through the engine's `eval-batch` command.

  games table: src_id, outcome, moves (TEXT, space-separated algebraic)

Savings vs per-position snapshots:
  ~180 bytes/game  vs  ~15 KB/game (56 positions × 281 bytes each)  →  ~88× smaller.

PIPELINE
─────────
  1. Self-match generates GAME/RESULT lines → appended to a .games file.
  2. ingest_incremental() reads new bytes, stores raw move sequences in DB.
     No eval-batch here — ingest is instant.
  3. At training time, expand_games() calls eval-batch once per epoch to
     materialise position features for all sampled plies.

Usage:
    python training/datagen.py --games 500 --time 0.2
    python training/datagen.py --incremental training/data/match.games --tag my-match
    python training/datagen.py --from-file training/data/match.games
    python training/datagen.py --migrate-games training/data/*.games

Options:
    --games N           Self-play games (default 200)
    --time S            Seconds per move (default 0.1)
    --engine E          Engine flag (default titanium-v15)
    --out PATH          Output DB (default training/data/all_games.db)
    --min-ply N         Skip positions before this ply (default 4)
    --max-ply N         Skip positions after this ply (default 150)
    --sample-rate R     Fraction of plies to sample per game (default 1.0)
    --openings book|random
    --from-file PATH    Ingest GAME/RESULT lines from file, then exit
    --incremental PATH  Ingest only new bytes (byte-offset sidecar), then exit
    --tag NAME          Source label stored in sources table
    --migrate-games P [P ...]  One-shot: load .games files into DB, then exit
    --stats             Print DB statistics, then exit
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import random
import time
from contextlib import contextmanager
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
BIN     = ROOT / "engine" / "target" / "release" / "titanium.exe"
DB_PATH = ROOT / "training" / "data" / "all_games.db"
EVAL_BATCH_LOCK = ROOT / "training" / "data" / "eval_batch.lock"
EVAL_BATCH_LOCK_TIMEOUT_SEC = float(os.environ.get("NNUE_EVAL_BATCH_LOCK_SEC", "300"))
EVAL_BATCH_TIMEOUT_SEC = float(os.environ.get("NNUE_EVAL_BATCH_TIMEOUT_SEC", "180"))


@contextmanager
def _eval_batch_lock():
    """One eval-batch titanium at a time — avoids 8th process crashing beside 7 game slots."""
    EVAL_BATCH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + EVAL_BATCH_LOCK_TIMEOUT_SEC
    fd = None
    while time.time() < deadline:
        try:
            fd = open(EVAL_BATCH_LOCK, "x")
            fd.write(str(os.getpid()))
            fd.flush()
            break
        except FileExistsError:
            time.sleep(2.0)
    else:
        raise TimeoutError(
            f"eval-batch lock busy after {EVAL_BATCH_LOCK_TIMEOUT_SEC:.0f}s "
            f"(7 game slots may be saturating CPU — will retry next game)"
        )
    try:
        yield
    finally:
        if fd is not None:
            fd.close()
        EVAL_BATCH_LOCK.unlink(missing_ok=True)

from field_planes import (
    CHOKE_P0,
    CHOKE_P1,
    CONTESTED,
    CORRIDOR_DELTA_P0,
    CORRIDOR_DELTA_P1,
    GOAL_INV_P0,
    GOAL_INV_P1,
    PATH_CROSS_P0,
    PATH_CROSS_P1,
    PAWN_FWD_P0,
    PAWN_FWD_P1,
    rec_field,
)
from engine_identity import assert_engine_ready

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA page_size    = 8192;

CREATE TABLE IF NOT EXISTS sources (
    id   INTEGER PRIMARY KEY,
    name TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS games (
    id      INTEGER PRIMARY KEY,
    src_id  INTEGER NOT NULL REFERENCES sources(id),
    outcome INTEGER NOT NULL,
    moves   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_games_src ON games(src_id);
"""

# ── DB helpers ────────────────────────────────────────────────────────────────

def open_db(path: Path, write: bool = False) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    if write:
        conn.executescript(SCHEMA)
    conn.execute("PRAGMA cache_size = -32768")  # ~32 MB page cache
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _get_or_create_src(conn: sqlite3.Connection, name: str) -> int:
    name = name or ""
    row = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO sources(name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def insert_games(conn: sqlite3.Connection, games: list, src_id: int):
    """Persist a list of (move_list, outcome) tuples into the games table."""
    conn.executemany(
        "INSERT INTO games(src_id, outcome, moves) VALUES (?, ?, ?)",
        [(src_id, outcome, " ".join(moves)) for moves, outcome in games],
    )
    conn.commit()


MIN_PLIES_DB = 8


def validate_game(moves: list[str], outcome: int) -> str | None:
    """Return error string if game must not be stored (partial / polluted)."""
    if not isinstance(moves, list) or len(moves) < MIN_PLIES_DB:
        n = len(moves) if isinstance(moves, list) else 0
        return f"too few plies ({n} < {MIN_PLIES_DB})"
    if outcome not in (1, -1):
        return "outcome must be +1 or -1"
    for m in moves:
        if not isinstance(m, str) or not m.strip():
            return "invalid move token"
    return None


def insert_single_game(
    moves: list[str],
    outcome: int,
    out_path: Path | None = None,
    tag: str | None = None,
) -> int:
    """Insert one game row; returns games.id. Raises ValueError if invalid."""
    err = validate_game(moves, outcome)
    if err:
        raise ValueError(err)
    out_path = Path(out_path or DB_PATH)
    conn = open_db(out_path, write=True)
    src_id = _get_or_create_src(conn, tag or "")
    cur = conn.execute(
        "INSERT INTO games(src_id, outcome, moves) VALUES (?, ?, ?)",
        (src_id, outcome, " ".join(moves)),
    )
    conn.commit()
    gid = int(cur.lastrowid)
    conn.close()
    return gid


def load_games_from_db(path: Path) -> list[tuple[list[str], int, str]]:
    """Return [(moves: list[str], outcome: int, src: str), ...] for all games."""
    conn = open_db(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT g.moves, g.outcome, s.name "
        "FROM games g JOIN sources s ON s.id = g.src_id"
    ).fetchall()
    conn.close()
    return [(row["moves"].split(), row["outcome"], row["name"]) for row in rows]


def load_games_by_ids(path: Path, ids: list[int]) -> list[tuple[list[str], int, str]]:
    """Load specific games by SQLite row id (for per-game incremental training)."""
    if not ids:
        return []
    conn = open_db(path)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT g.id, g.moves, g.outcome, s.name "
        f"FROM games g JOIN sources s ON s.id = g.src_id "
        f"WHERE g.id IN ({placeholders}) ORDER BY g.id",
        ids,
    ).fetchall()
    conn.close()
    return [(row["moves"].split(), row["outcome"], row["name"]) for row in rows]


def max_game_id(path: Path | None = None) -> int:
    path = Path(path or DB_PATH)
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM games").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def untrained_game_ids(path: Path, after_id: int) -> list[int]:
    """Row ids in games table strictly greater than after_id."""
    path = Path(path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT id FROM games WHERE id > ? ORDER BY id",
            (after_id,),
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


def db_stats(path: Path) -> dict:
    conn = open_db(path)
    n_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    srcs = conn.execute(
        "SELECT s.name, COUNT(*) FROM games g JOIN sources s ON s.id=g.src_id "
        "GROUP BY g.src_id ORDER BY COUNT(*) DESC"
    ).fetchall()
    conn.close()
    sz = path.stat().st_size if path.exists() else 0
    return {"games": n_games, "size_kb": sz // 1024, "sources": list(srcs)}

# ── Engine helpers ────────────────────────────────────────────────────────────

def run_match(engine: str, games: int, time_s: float, openings: str) -> list[str]:
    assert_engine_ready(parity=False)
    cmd = [str(BIN), "match", "--a", engine, "--b", engine,
           "--games", str(games), "--time", str(time_s), "--dump-games"]
    if openings == "book":
        cmd += ["--openings", "book"]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="replace").splitlines()


def eval_batch(all_move_lists: list[list[str]]) -> list[dict]:
    """Run all move sequences through `titanium eval-batch`; returns one JSON dict per position."""
    assert_engine_ready(parity=False)
    stdin_text = "\n".join(" ".join(m) if m else "" for m in all_move_lists) + "\n"
    with _eval_batch_lock():
        result = subprocess.run(
            [str(BIN), "eval-batch"],
            input=stdin_text.encode("utf-8"),
            capture_output=True,
            check=True,
            timeout=EVAL_BATCH_TIMEOUT_SEC,
        )
    return [json.loads(l) for l in result.stdout.decode("utf-8", errors="replace").splitlines() if l.strip()]


def expand_games(
    games: list[tuple[list[str], int, str]],
    min_ply: int = 4,
    max_ply: int = 150,
    sample_rate: float = 1.0,
) -> list[dict]:
    """Expand game sequences into per-position training records via eval-batch.

    Call this at the start of each training epoch — it is the only place
    eval-batch is needed.  All positions from all games are batched into a
    single subprocess invocation.

    Returns a list of record dicts with the same keys that QuoridorDataset
    expects (d0, d1, goal_inv_p0_field, pawn_fwd_p0_field, …, hw, vw,
    pawn0, pawn1, wl0, wl1, corridor_width0, corridor_width1, turn, outcome, ply, _src).
    """
    entries = []
    for moves, outcome, src in games:
        for ply in range(min_ply, min(max_ply + 1, len(moves) + 1)):
            if sample_rate < 1.0 and random.random() > sample_rate:
                continue
            entries.append((moves[:ply], outcome, src))

    if not entries:
        return []

    evals = eval_batch([e[0] for e in entries])

    records = []
    for (move_prefix, outcome, src), rec in zip(entries, evals):
        ply = len(move_prefix)
        gi0 = rec_field(rec, GOAL_INV_P0)
        gi1 = rec_field(rec, GOAL_INV_P1)
        p0  = rec.get("pawn0", 0)
        p1  = rec.get("pawn1", 0)
        d0  = gi0[p0] if gi0 and p0 < len(gi0) else rec.get("d0", 0)
        d1  = gi1[p1] if gi1 and p1 < len(gi1) else rec.get("d1", 0)
        if "legal_wall_count" not in rec:
            raise RuntimeError(
                f"eval-batch missing legal_wall_count at ply {ply} — rebuild native titanium"
            )
        records.append({
            "_src":            src,
            "ply":             ply,
            "turn":            rec.get("turn", 0),
            "outcome":         outcome,
            "pawn0":           p0,
            "pawn1":           p1,
            "wl0":             rec.get("wl0", 0),
            "wl1":             rec.get("wl1", 0),
            "d0":              d0,
            "d1":              d1,
            "legal_wall_count": int(rec["legal_wall_count"]),
            GOAL_INV_P0:       gi0,
            GOAL_INV_P1:       gi1,
            PAWN_FWD_P0:       rec_field(rec, PAWN_FWD_P0),
            PAWN_FWD_P1:       rec_field(rec, PAWN_FWD_P1),
            CORRIDOR_DELTA_P0: rec_field(rec, CORRIDOR_DELTA_P0),
            CORRIDOR_DELTA_P1: rec_field(rec, CORRIDOR_DELTA_P1),
            PATH_CROSS_P0:     rec_field(rec, PATH_CROSS_P0),
            PATH_CROSS_P1:     rec_field(rec, PATH_CROSS_P1),
            CHOKE_P0:          rec_field(rec, CHOKE_P0),
            CHOKE_P1:          rec_field(rec, CHOKE_P1),
            CONTESTED:         rec_field(rec, CONTESTED),
            "corridor_width0": sum(1 for v in gi0 if v == d0),
            "corridor_width1": sum(1 for v in gi1 if v == d1),
            "hw":              rec.get("hw", []),
            "vw":              rec.get("vw", []),
        })
    return records

# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_dump_games(lines: list[str]) -> list[tuple[list[str], int]]:
    """Parse GAME/RESULT lines into [(move_list, outcome)] tuples."""
    out = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("GAME "):
            moves = line.split()[1:]
            res_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if res_line.startswith("RESULT "):
                r = res_line.split()[1]
                if r in ("W", "B"):
                    out.append((moves, 1 if r == "W" else -1))
            i += 2
        else:
            i += 1
    return out

# ── Incremental ingest ────────────────────────────────────────────────────────

def offset_path_for(src: Path) -> Path:
    return src.with_suffix(src.suffix + ".ingested_offset")


def ingest_incremental(
    src_path: Path,
    out_path: Path,
    tag: str | None = None,
) -> int:
    """Append only new GAME/RESULT pairs from src_path into the games table.

    Tracks a byte offset sidecar so calling after every game is safe — only
    the new bytes are read, no duplicate games are stored.  No eval-batch is
    run here; expansion happens at training time.
    """
    src_path = Path(src_path)
    if not src_path.exists():
        return 0

    off_path = offset_path_for(src_path)
    if off_path.exists():
        offset = int(off_path.read_text(encoding="utf-8").strip() or "0")
    else:
        offset = src_path.stat().st_size  # assume already ingested

    with open(src_path, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        chunk = f.read()
        new_offset = f.tell()

    if not chunk.strip():
        return 0

    games = parse_dump_games(chunk.splitlines())
    if not games:
        off_path.write_text(str(new_offset), encoding="utf-8")
        return 0

    conn = open_db(out_path, write=True)
    src_id = _get_or_create_src(conn, tag or "")
    insert_games(conn, games, src_id)
    conn.close()

    off_path.write_text(str(new_offset), encoding="utf-8")
    return len(games)

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games",         type=int,   default=200)
    ap.add_argument("--time",          type=float, default=0.1)
    ap.add_argument("--engine",        default="titanium-v15")
    ap.add_argument("--out",           default=str(DB_PATH))
    ap.add_argument("--min-ply",       type=int,   default=4)
    ap.add_argument("--max-ply",       type=int,   default=150)
    ap.add_argument("--sample-rate",   type=float, default=1.0)
    ap.add_argument("--openings",      default="random", choices=["random", "book"])
    ap.add_argument("--from-file",     default=None,  metavar="PATH",
                    help="Ingest GAME/RESULT lines from a .games file, then exit.")
    ap.add_argument("--incremental",   default=None,  metavar="PATH",
                    help="Ingest only new bytes from PATH (byte-offset sidecar), then exit.")
    ap.add_argument("--tag",           default=None,
                    help="Source label stored in the sources table.")
    ap.add_argument("--migrate-games", nargs="+",   metavar="PATH",
                    help="Load one or more .games files into DB, then exit.")
    ap.add_argument("--stats",         action="store_true",
                    help="Print DB statistics, then exit.")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── stats ──
    if args.stats:
        if out_path.exists():
            s = db_stats(out_path)
            print(f"{s['games']} games  |  {s['size_kb']} KB  |  {out_path.name}")
            for name, n in s["sources"]:
                print(f"  {n:>6}  {name}")
        else:
            print(f"DB not found: {out_path}")
        sys.exit(0)

    # ── migrate one or more .games files ──
    if args.migrate_games:
        conn = open_db(out_path, write=True)
        total = 0
        for p in args.migrate_games:
            src = Path(p)
            if not src.exists():
                print(f"  SKIP (not found): {src}"); continue
            games = parse_dump_games(src.read_text(encoding="utf-8").splitlines())
            src_id = _get_or_create_src(conn, args.tag or src.name)
            insert_games(conn, games, src_id)
            print(f"  +{len(games)} games from {src.name}")
            total += len(games)
        conn.close()
        s = db_stats(out_path)
        print(f"Done: {total} games added  |  DB total: {s['games']} games  {s['size_kb']} KB")
        sys.exit(0)

    # ── per-game incremental ingest (called after each self-match game) ──
    if args.incremental:
        n = ingest_incremental(Path(args.incremental), out_path, tag=args.tag)
        if n:
            print(f"Incremental: +{n} games -> {out_path.name}")
        sys.exit(0)

    # ── bulk ingest from file ──
    if args.from_file:
        src = Path(args.from_file)
        if not src.exists():
            print(f"ERROR: not found: {src}"); sys.exit(1)
        games = parse_dump_games(src.read_text(encoding="utf-8").splitlines())
        if not games:
            print("No games found."); sys.exit(1)
        conn = open_db(out_path, write=True)
        src_id = _get_or_create_src(conn, args.tag or src.name)
        insert_games(conn, games, src_id)
        conn.close()
        print(f"Ingested {len(games)} games -> {out_path.name}")
        sys.exit(0)

    # ── run self-play match ──
    print(f"Generating {args.games} games @ {args.time}s/move with {args.engine}...")
    try:
        lines = run_match(args.engine, args.games, args.time, args.openings)
    except subprocess.CalledProcessError:
        print("ERROR: titanium match --dump-games not yet supported."); sys.exit(1)
    games = parse_dump_games(lines)
    if not games:
        print("No games parsed."); sys.exit(1)
    conn = open_db(out_path, write=True)
    src_id = _get_or_create_src(conn, args.tag or args.engine)
    insert_games(conn, games, src_id)
    conn.close()
    print(f"Stored {len(games)} games -> {out_path.name}  (expand at training time)")


if __name__ == "__main__":
    main()
