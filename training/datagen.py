"""Training data generation for HalfPW retrain.

Generates (features, target) records from self-play games using the
current engine.  Storage is SQLite with binary-packed fields:

  Stored per record (all in `records` table):
    src_id   INTEGER  -- FK into sources(name); avoids repeating long strings
    ply      INTEGER  -- for analysis / min-ply filtering
    turn     INTEGER  -- 0 or 1 (side to move)
    outcome  INTEGER  -- +1 (P0 wins) or -1 (P1 wins)
    pawn0    INTEGER  -- P0 cell index 0..80
    pawn1    INTEGER  -- P1 cell index 0..80
    wl0      INTEGER  -- walls left for P0 (0..10)
    wl1      INTEGER  -- walls left for P1 (0..10)
    d0_field BLOB     -- 81 uint8: BFS distance from each cell to P0's goal row
    d1_field BLOB     -- 81 uint8: BFS distance from each cell to P1's goal row
    hw       BLOB     -- 8 bytes: 64-bit bitmap, bit i = horizontal wall at slot i
    vw       BLOB     -- 8 bytes: 64-bit bitmap, bit i = vertical   wall at slot i

  Derived at load time (not stored):
    d0               = d0_field[pawn0]
    d1               = d1_field[pawn1]
    corridor_width0  = count(d0_field[cell] == d0)
    corridor_width1  = count(d1_field[cell] == d1)
    delta0[cell]     = d0_field[cell] - d0   (clamped 0..255)
    delta1[cell]     = d1_field[cell] - d1

  DROPPED (unused by training or derivable):
    eval     -- engine centipawn score (not used in training loop)
    delta0/1 -- fully derivable from d0_field + d0
    cw0/1    -- corridor widths, derivable from d0_field + d0

Usage:
    python training/datagen.py --games 500 --time 0.2

Options:
    --games N           Self-play games (default 200)
    --time S            Seconds per move (default 0.1)
    --engine E          Engine flag (default titanium-v15)
    --out PATH          Output DB (default training/data/all_games.db)
    --min-ply N         Skip plies before N (default 4)
    --max-ply N         Skip plies after N (default 150)
    --sample-rate R     Sampling probability per position (default 1.0)
    --openings book     Book-weighted openings (default random)
    --from-file PATH    Ingest GAME/RESULT lines from file (no self-play)
    --incremental PATH  Ingest only new bytes from PATH (byte-offset sidecar)
    --tag NAME          Source tag stored in sources table
    --migrate-jsonl P   One-time migration: JSONL -> DB (then exit)
    --rebuild-db        Rebuild DB from scratch using existing records (v1->v2)
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import random
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
BIN     = ROOT / "engine" / "target" / "release" / "titanium.exe"
WEIGHTS = ROOT / "engine" / "src" / "acev13" / "net_weights.bin"
DB_PATH = ROOT / "training" / "data" / "all_games.db"

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA page_size    = 8192;

CREATE TABLE IF NOT EXISTS sources (
    id   INTEGER PRIMARY KEY,
    name TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS records (
    id       INTEGER PRIMARY KEY,
    src_id   INTEGER NOT NULL REFERENCES sources(id),
    ply      INTEGER NOT NULL,
    turn     INTEGER NOT NULL,
    outcome  INTEGER NOT NULL,
    pawn0    INTEGER NOT NULL,
    pawn1    INTEGER NOT NULL,
    wl0      INTEGER NOT NULL,
    wl1      INTEGER NOT NULL,
    d0_field BLOB    NOT NULL,
    d1_field BLOB    NOT NULL,
    hw       BLOB    NOT NULL,
    vw       BLOB    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_src ON records(src_id);
"""

# ── SQLite helpers ────────────────────────────────────────────────────────────

def open_db(path: Path, write: bool = False) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    if write:
        conn.executescript(SCHEMA)
    conn.execute("PRAGMA cache_size = -32768")   # ~32 MB page cache
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _get_or_create_src(conn: sqlite3.Connection, name: str | None) -> int:
    name = name or ""
    row = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO sources(name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def pack_blob(lst) -> bytes:
    """Pack a list of uint8 values as raw bytes."""
    return bytes(int(v) & 0xFF for v in lst)


def pack_wall_bitmap(lst) -> bytes:
    """Pack 64 binary (0/1) values as an 8-byte (64-bit) little-endian bitmap."""
    n = 0
    for i, v in enumerate(lst):
        if v:
            n |= 1 << i
    return n.to_bytes(8, "little")


def unpack_blob(blob: bytes) -> list:
    return list(blob)


def unpack_wall_bitmap(blob: bytes) -> list:
    """Expand 8-byte bitmap back to 64 binary values."""
    n = int.from_bytes(blob, "little")
    return [(n >> i) & 1 for i in range(64)]


def insert_records(conn: sqlite3.Connection, records: list, src_id: int):
    rows = []
    for r in records:
        d0f = r.get("d0_field", [])
        d1f = r.get("d1_field", [])
        rows.append((
            src_id,
            r.get("ply", 0),
            r.get("turn", 0),
            r.get("outcome", 0),
            r.get("pawn0", 0),
            r.get("pawn1", 0),
            r.get("wl0", 0),
            r.get("wl1", 0),
            pack_blob(d0f),
            pack_blob(d1f),
            pack_wall_bitmap(r.get("hw", [])),
            pack_wall_bitmap(r.get("vw", [])),
        ))
    conn.executemany(
        "INSERT INTO records "
        "(src_id,ply,turn,outcome,pawn0,pawn1,wl0,wl1,d0_field,d1_field,hw,vw) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


def load_records_from_db(path: Path) -> list:
    """Load all records as dicts, deriving d0/d1/corridor_width/delta on the fly."""
    conn = open_db(path)
    conn.row_factory = sqlite3.Row
    # Join sources to restore the string tag
    cur = conn.execute(
        "SELECT r.src_id, s.name as src, r.ply, r.turn, r.outcome, "
        "r.pawn0, r.pawn1, r.wl0, r.wl1, "
        "r.d0_field, r.d1_field, r.hw, r.vw "
        "FROM records r JOIN sources s ON s.id = r.src_id"
    )
    out = []
    for row in cur:
        d0f = unpack_blob(row["d0_field"])
        d1f = unpack_blob(row["d1_field"])
        p0  = row["pawn0"]
        p1  = row["pawn1"]
        d0  = d0f[p0]
        d1  = d1f[p1]
        out.append({
            "_src":            row["src"],
            "ply":             row["ply"],
            "turn":            row["turn"],
            "outcome":         row["outcome"],
            "pawn0":           p0,
            "pawn1":           p1,
            "wl0":             row["wl0"],
            "wl1":             row["wl1"],
            "d0":              d0,
            "d1":              d1,
            "d0_field":        d0f,
            "d1_field":        d1f,
            "corridor_width0": sum(1 for v in d0f if v == d0),
            "corridor_width1": sum(1 for v in d1f if v == d1),
            "hw":              unpack_wall_bitmap(row["hw"]),
            "vw":              unpack_wall_bitmap(row["vw"]),
        })
    conn.close()
    return out

# ── Engine helpers ────────────────────────────────────────────────────────────

def run_match(engine, games, time_s, openings):
    cmd = [
        str(BIN), "match",
        "--a", engine, "--b", engine,
        "--games", str(games),
        "--time", str(time_s),
        "--dump-games",
    ]
    if openings == "book":
        cmd += ["--openings", "book"]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="replace").splitlines()


def eval_batch(all_move_lists):
    stdin_text = "\n".join(" ".join(m) if m else "" for m in all_move_lists) + "\n"
    result = subprocess.run(
        [str(BIN), "eval-batch"],
        input=stdin_text.encode("utf-8"),
        capture_output=True, check=True,
    )
    lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def games_to_records(games, min_ply, max_ply, sample_rate):
    entries = []
    for move_list, outcome in games:
        for ply in range(min_ply, min(max_ply + 1, len(move_list) + 1)):
            if sample_rate < 1.0 and random.random() > sample_rate:
                continue
            entries.append((ply, move_list[:ply], outcome))

    if not entries:
        return []

    evals = eval_batch([e[1] for e in entries])
    records = []
    for (ply, _, outcome), rec in zip(entries, evals):
        rec["outcome"] = outcome
        rec["ply"] = ply
        records.append(rec)
    return records

# ── Incremental ingest ────────────────────────────────────────────────────────

def offset_path_for(src: Path) -> Path:
    return src.with_suffix(src.suffix + ".ingested_offset")


def ingest_incremental(
    src_path: Path,
    out_path: Path,
    min_ply: int = 4,
    max_ply: int = 150,
    sample_rate: float = 1.0,
    tag: str | None = None,
) -> int:
    src_path = Path(src_path)
    out_path = Path(out_path)

    if not src_path.exists():
        return 0

    off_path = offset_path_for(src_path)
    if off_path.exists():
        offset = int(off_path.read_text(encoding="utf-8").strip() or "0")
    else:
        offset = src_path.stat().st_size  # assume prior full ingest

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

    records = games_to_records(games, min_ply, max_ply, sample_rate)
    conn = open_db(out_path, write=True)
    src_id = _get_or_create_src(conn, tag)
    insert_records(conn, records, src_id)
    conn.close()

    off_path.write_text(str(new_offset), encoding="utf-8")
    return len(records)

# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_jsonl_to_db(jsonl_path: Path, db_path: Path, tag: str | None = None):
    """One-time migration from a JSONL file."""
    jsonl_path = Path(jsonl_path)
    lines = [l for l in jsonl_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    records = [json.loads(l) for l in lines]
    conn = open_db(db_path, write=True)
    src_id = _get_or_create_src(conn, tag or str(jsonl_path.name))
    insert_records(conn, records, src_id)
    conn.close()
    sz_before = jsonl_path.stat().st_size
    sz_after  = db_path.stat().st_size
    print(f"Migrated {len(records)} records from {jsonl_path.name}")
    print(f"  {sz_before//1024} KB JSONL -> {sz_after//1024} KB SQLite  ({sz_before/max(sz_after,1):.1f}x smaller)")


def rebuild_db(old_db: Path, new_db: Path):
    """Migrate an existing v1 DB (with wide schema) to the compact v2 layout."""
    old_conn = sqlite3.connect(str(old_db))
    old_conn.row_factory = sqlite3.Row
    rows = old_conn.execute(
        "SELECT src, ply, turn, outcome, pawn0, pawn1, wl0, wl1, "
        "d0_field, d1_field, hw, vw FROM records"
    ).fetchall()
    old_conn.close()

    new_conn = open_db(new_db, write=True)
    src_cache: dict[str, int] = {}

    def get_src(name):
        if name not in src_cache:
            src_cache[name] = _get_or_create_src(new_conn, name)
        return src_cache[name]

    batch = []
    for row in rows:
        d0f_bytes = bytes(row["d0_field"])
        d1f_bytes = bytes(row["d1_field"])
        hw_raw    = bytes(row["hw"])
        vw_raw    = bytes(row["vw"])

        # hw/vw may be 64 bytes (old) or 8 bytes (new bitmap) — handle both
        if len(hw_raw) == 64:
            hw_blob = pack_wall_bitmap(hw_raw)
            vw_blob = pack_wall_bitmap(vw_raw)
        else:
            hw_blob = hw_raw
            vw_blob = vw_raw

        batch.append((
            get_src(row["src"] or ""),
            row["ply"],
            row["turn"],
            row["outcome"],
            row["pawn0"],
            row["pawn1"],
            row["wl0"],
            row["wl1"],
            d0f_bytes,
            d1f_bytes,
            hw_blob,
            vw_blob,
        ))

    new_conn.executemany(
        "INSERT INTO records "
        "(src_id,ply,turn,outcome,pawn0,pawn1,wl0,wl1,d0_field,d1_field,hw,vw) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        batch,
    )
    new_conn.commit()
    new_conn.close()

    sz_old = old_db.stat().st_size
    sz_new = new_db.stat().st_size
    print(f"Rebuilt {len(batch)} records: {old_db.name} -> {new_db.name}")
    print(f"  {sz_old//1024} KB -> {sz_new//1024} KB  ({sz_old/max(sz_new,1):.2f}x smaller)")

# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_dump_games(lines):
    games = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("GAME "):
            moves = line.split()[1:]
            result_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if not result_line.startswith("RESULT "):
                i += 2
                continue
            r = result_line.split()[1]
            if r not in ("W", "B"):
                i += 2
                continue
            games.append((moves, 1 if r == "W" else -1))
            i += 2
        else:
            i += 1
    return games

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games",         type=int,   default=200)
    ap.add_argument("--time",          type=float, default=0.1)
    ap.add_argument("--engine",        default="titanium-v15")
    ap.add_argument("--out",           default=str(DB_PATH))
    ap.add_argument("--min-ply",       type=int,   default=4)
    ap.add_argument("--max-ply",       type=int,   default=150)
    ap.add_argument("--sample-rate",   type=float, default=1.0)
    ap.add_argument("--openings",      default="random", choices=["random", "book"])
    ap.add_argument("--from-file",     default=None, metavar="PATH")
    ap.add_argument("--incremental",   default=None, metavar="PATH")
    ap.add_argument("--tag",           default=None)
    ap.add_argument("--migrate-jsonl", default=None, metavar="PATH")
    ap.add_argument("--rebuild-db",    action="store_true",
                    help="Compact existing DB from wide v1 schema to lean v2.")
    ap.add_argument("--append",        action="store_true")  # compat shim
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.migrate_jsonl:
        migrate_jsonl_to_db(Path(args.migrate_jsonl), out_path, args.tag)
        sys.exit(0)

    if args.rebuild_db:
        import tempfile, shutil
        tmp = out_path.with_suffix(".v2_tmp.db")
        rebuild_db(out_path, tmp)
        bak = out_path.with_suffix(".v1.bak.db")
        shutil.move(str(out_path), str(bak))
        shutil.move(str(tmp), str(out_path))
        print(f"Backup kept at {bak.name}")
        sys.exit(0)

    if args.incremental:
        n = ingest_incremental(
            Path(args.incremental), out_path,
            args.min_ply, args.max_ply, args.sample_rate, tag=args.tag,
        )
        if n:
            print(f"Incremental: +{n} records -> {out_path.name}")
        sys.exit(0)

    if args.from_file:
        src = Path(args.from_file)
        if not src.exists():
            print(f"ERROR: --from-file not found: {src}"); sys.exit(1)
        games = parse_dump_games(src.read_text(encoding="utf-8").splitlines())
        if not games:
            print("No games found in file."); sys.exit(1)
        print(f"Ingesting {len(games)} games from {src} ...")
        records = games_to_records(games, args.min_ply, args.max_ply, args.sample_rate)
    else:
        print(f"Generating {args.games} games @ {args.time}s/move with {args.engine}...")
        try:
            lines = run_match(args.engine, args.games, args.time, args.openings)
        except subprocess.CalledProcessError:
            print("ERROR: titanium match --dump-games not yet supported."); sys.exit(1)
        games = parse_dump_games(lines)
        if not games:
            print("No games parsed."); sys.exit(1)
        print(f"  {len(games)} games; running eval-batch...")
        records = games_to_records(games, args.min_ply, args.max_ply, args.sample_rate)

    conn = open_db(out_path, write=True)
    src_id = _get_or_create_src(conn, args.tag)
    insert_records(conn, records, src_id)
    conn.close()
    print(f"Done: {len(records)} records -> {out_path}")


if __name__ == "__main__":
    main()
