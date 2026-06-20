"""Tests for teacher dataset sidecar repair and policy read."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from teacher_dataset.sidecar_paths import classify_sidecar_path, resolve_sidecar_path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "training" / "data" / "canonical" / "position_teacher_store.db"


@pytest.mark.skipif(not DB.exists(), reason="teacher store not present")
def test_wrong_base_sidecar_path_remaps_to_teacher_sidecars() -> None:
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT l.payload_json, hex(p.canonical_hash) "
        "FROM labels l JOIN positions p ON p.position_id = l.position_id "
        "WHERE l.payload_json LIKE ? LIMIT 1",
        ("%iter_000002.policy.bin%",),
    ).fetchone()
    conn.close()
    assert row is not None
    payload = json.loads(row[0])
    ref = payload["sidecar_ref"]
    stored = ref["sidecar"]
    assert "friend_selfplay" in stored
    cls = classify_sidecar_path(stored, root=ROOT.parent)
    assert cls in {
        "repaired_wrong_base_friend_selfplay_at_root",
        "path_ok_teacher_sidecars_relative",
        "path_ok_other",
    }
    path = resolve_sidecar_path(stored, root=ROOT.parent)
    assert path.is_file(), path


def test_sidecar_record_roundtrip_unit() -> None:
    from teacher_dataset.sidecar_reader import decode_record
    import struct

    n = 2
    raw = bytes([n]) + bytes(32) + struct.pack("<BH", 128, 32768) + struct.pack("<BH", 129, 16384)
    rec = decode_record(raw)
    assert rec.move_codes == (128, 129)
