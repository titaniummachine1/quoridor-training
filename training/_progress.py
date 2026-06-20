import sqlite3
from pathlib import Path

db = Path("training/data/canonical/position_store_v2.db")
conn = sqlite3.connect(str(db))
print("=== production stats ===")
for row in conn.execute(
    "SELECT (SELECT COUNT(*) FROM positions), (SELECT COUNT(*) FROM labels WHERE source LIKE 'friend_selfplay:%'), "
    "(SELECT COUNT(*) FROM games), (SELECT COUNT(*) FROM edges)"
).fetchone():
    pass
pos, friend_labels, games, edges = conn.execute(
    "SELECT (SELECT COUNT(*) FROM positions), (SELECT COUNT(*) FROM labels WHERE source LIKE 'friend_selfplay:%'), "
    "(SELECT COUNT(*) FROM games), (SELECT COUNT(*) FROM edges)"
).fetchone()
print(f"positions={pos:,}  friend_labels={friend_labels:,}  games={games}  edges={edges:,}")
print(f"db_bytes={db.stat().st_size:,}")
print("\n=== friend shard imports ===")
rows = conn.execute(
    "SELECT import_id, status, record_count, accepted_count, rejected_count, source_path "
    "FROM imports WHERE source_path LIKE '%iter_%' ORDER BY import_id"
).fetchall()
completed = sum(1 for r in rows if r[1] == "completed")
running = sum(1 for r in rows if r[1] == "running")
print(f"total={len(rows)}  completed={completed}  running={running}")
for r in rows:
    path = r[5].replace("\\", "/")
    iter_name = path.split("/")[-2] if "iter_" in path else "?"
    print(f"  {iter_name}: {r[1]} seen={r[2]} acc={r[3]} rej={r[4]}")
conn.close()
