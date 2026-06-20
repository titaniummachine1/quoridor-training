import sqlite3
from pathlib import Path

db = Path("training/data/canonical/position_store_v2.db")
c = sqlite3.connect(str(db))
r = c.execute(
    "SELECT "
    "(SELECT COALESCE(SUM(LENGTH(packed_state)),0) FROM positions), "
    "(SELECT COALESCE(SUM(LENGTH(payload_json)),0) FROM labels), "
    "(SELECT COALESCE(SUM(LENGTH(payload_json)),0) FROM labels WHERE source LIKE 'friend%'), "
    "(SELECT COUNT(*) FROM labels WHERE source LIKE 'friend%'), "
    "(SELECT COUNT(*) FROM labels), "
    "(SELECT COUNT(*) FROM positions), "
    "(SELECT COUNT(*) FROM observations)"
).fetchone()
print("packed_state_bytes", r[0], f"({r[0]/1e6:.1f} MB)")
print("all_label_payload", r[1], f"({r[1]/1e6:.1f} MB)")
print("friend_label_payload", r[2], f"({r[2]/1e6:.1f} MB)")
print("friend_label_rows", r[3], "total_label_rows", r[4], "positions", r[5], "observations", r[6])
rows = c.execute(
    "SELECT status, record_count FROM imports WHERE source_path LIKE '%iter_%'"
).fetchall()
print("shards completed", sum(1 for s,_ in rows if s=="completed"), "of", len(rows))
print("records completed", sum(rc for s,rc in rows if s=="completed"))
c.close()
base = Path("KaAiData/ANOTHER TRAINING DAT ASTUFF SUPER USEFULL/selfplay_iters_000001_000020")
src = sum(p.stat().st_size for p in base.glob("iter_*/shard_000.jsonl"))
print("source_jsonl_total", src, f"({src/1e9:.2f} GB)")
print("db_size", db.stat().st_size, f"({db.stat().st_size/1e9:.2f} GB)")
