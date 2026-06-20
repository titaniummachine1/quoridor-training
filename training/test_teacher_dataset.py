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


def test_friend_state_uses_current_player_field() -> None:
    from teacher_dataset.friend_state import parse_friend_state

    state = parse_friend_state(
        {
            "state": {
                "player0Cell": 4,
                "player1Cell": 76,
                "player0Walls": 10,
                "player1Walls": 10,
                "horizontalWalls": 0,
                "verticalWalls": 0,
                "currentPlayer": 1,
            }
        }
    )
    assert state.side_to_move == 1
    assert state.packed_state()[5] == 1


def test_policy_chunk_writer_readback() -> None:
    from teacher_dataset.policy_binary import EncodedPolicy, PolicyChunkWriter, read_policy_chunk
    import tempfile

    writer = PolicyChunkWriter(chunk_id=0)
    enc = EncodedPolicy.from_sparse([128, 129], [0.5, 0.5])
    rid = writer.add(enc)
    bin_bytes, idx_bytes = writer.finalize()
    with tempfile.TemporaryDirectory() as tmp:
        bin_path = Path(tmp) / "policy.bin"
        idx_path = Path(tmp) / "policy.idx"
        bin_path.write_bytes(bin_bytes)
        idx_path.write_bytes(idx_bytes)
        back = read_policy_chunk(bin_path, idx_path, rid)
    assert back.move_codes == enc.move_codes


def test_golden_vector_packed_hash_roundtrip() -> None:
    import json
    from teacher_dataset.canonical_identity import canonical_hash_from_packed, verify_stored_canonical
    from teacher_dataset.friend_state import parse_friend_state

    vectors = json.loads((Path(__file__).resolve().parent / "fixtures" / "position_golden_vectors.json").read_text())
    for vec in vectors:
        if "state" not in vec:
            continue
        packed = parse_friend_state({"state": vec["state"]}).packed_state()
        assert len(packed) == 24
        stored = canonical_hash_from_packed(packed)
        assert verify_stored_canonical(packed, stored)


def test_policy_lookup_requires_packed_identity_not_hash_only() -> None:
    from teacher_dataset.jsonl_policy_index import build_jsonl_policy_index
    from teacher_dataset.policy_lookup import PolicyLookupStats, lookup_teacher_policy
    from teacher_dataset.sidecar_policy_index import build_sidecar_policy_index

    sidecar_index, _ = build_sidecar_policy_index()
    _jc, jsonl_by_packed = build_jsonl_policy_index()
    stats = PolicyLookupStats()
    fake_canonical = b"\x01" * 32
    fake_packed = b"\x02" * 24
    result = lookup_teacher_policy(
        canonical_hash=fake_canonical,
        packed_state=fake_packed,
        policy_hash="deadbeef",
        sidecar_ref=None,
        source="friend_selfplay:iter_000001",
        label_id=1,
        sidecar_index=sidecar_index,
        jsonl_by_packed=jsonl_by_packed,
        stats=stats,
    )
    assert result is None
    assert stats.unresolved == 1
