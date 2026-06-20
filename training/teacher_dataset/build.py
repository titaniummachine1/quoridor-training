"""Build immutable Parquet teacher dataset from SQLite reference + repaired policies."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from position_store_config import ROOT, TEACHER_SIDECARS, TEACHER_STORE_DB

from .audit_policies import audit_teacher_policies
from .config import (
    LABELS_DIR,
    OBSERVATIONS_DIR,
    POLICIES_DIR,
    POSITIONS_DIR,
    REJECTS_DIR,
    TEACHER_DATASET_DIR,
    TEACHER_DATASET_MANIFEST,
    TEACHER_DATASET_SCHEMA,
)
from .policy_binary import EncodedPolicy, PolicyChunkWriter
from .jsonl_policy_index import build_jsonl_policy_index
from .schema import (
    LABEL_TYPE_TO_TARGET_KIND,
    TEACHER_DATASET_SCHEMA_VERSION,
    TARGET_OTHER,
)


def _position_key(canonical_hash: bytes, packed_state: bytes) -> bytes:
    return hashlib.blake2b(canonical_hash + packed_state, digest_size=16).digest()


def build_teacher_dataset(
    output_dir: Path = TEACHER_DATASET_DIR,
    *,
    sqlite_db: Path = TEACHER_STORE_DB,
    root: Path = ROOT,
    compression: str = "zstd",
    batch_size: int = 100_000,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    for d in (output_dir, POSITIONS_DIR, LABELS_DIR, OBSERVATIONS_DIR, POLICIES_DIR, REJECTS_DIR, output_dir / "reports"):
        d.mkdir(parents=True, exist_ok=True)

    policy_audit = audit_teacher_policies(sqlite_db, root=root, verify_payloads=False)
    jsonl_index = build_jsonl_policy_index()

    conn = sqlite3.connect(sqlite_db)
    conn.row_factory = sqlite3.Row

    pos_rows: list[dict[str, Any]] = []
    pos_id_to_key: dict[int, bytes] = {}
    for row in conn.execute(
        "SELECT position_id, canonical_hash, packed_state, side_to_move, total_visits, source_flags "
        "FROM positions ORDER BY canonical_hash, packed_state"
    ):
        canonical = bytes(row["canonical_hash"])
        packed = bytes(row["packed_state"])
        key = _position_key(canonical, packed)
        pos_id_to_key[int(row["position_id"])] = key
        pos_rows.append(
            {
                "position_key": key,
                "canonical_hash": canonical,
                "packed_state": packed,
                "side_to_move": int(row["side_to_move"]),
                "source_flags": int(row["source_flags"] or 0),
                "total_observations": int(row["total_visits"] or 0),
            }
        )

    positions_path = POSITIONS_DIR / "part-00000.parquet"
    pq.write_table(
        pa.Table.from_pylist(pos_rows),
        positions_path,
        compression=compression,
    )

    policy_dedup: dict[bytes, int] = {}
    policy_writer = PolicyChunkWriter(chunk_id=0)
    label_rows: list[dict[str, Any]] = []
    obs_rows: list[dict[str, Any]] = []

    for row in conn.execute(
        "SELECT l.label_id, l.position_id, l.label_type, l.value, l.best_move_u8, l.source, l.payload_json, "
        "p.canonical_hash AS pos_canonical "
        "FROM labels l JOIN positions p ON p.position_id = l.position_id ORDER BY l.label_id"
    ):
        payload = json.loads(row["payload_json"] or "{}")
        pos_key = pos_id_to_key[int(row["position_id"])]
        canonical = bytes(row["pos_canonical"])
        target_kind = LABEL_TYPE_TO_TARGET_KIND.get(str(row["label_type"]), TARGET_OTHER)
        obs_count = int(payload.get("observation_count") or 1)
        policy_record_id = None
        ref = payload.get("sidecar_ref")
        policy_hash = payload.get("policy_hash")
        if policy_hash and str(row["source"] or "").startswith("friend_selfplay:"):
            key = (canonical, str(policy_hash))
            record = jsonl_index.get(key)
            if record is None:
                raise RuntimeError(f"JSONL policy missing for {key} label_id={row['label_id']}")
            encoded = EncodedPolicy.from_sparse(list(record.move_codes), list(record.policy_values))
            if encoded.content_hash not in policy_dedup:
                policy_dedup[encoded.content_hash] = policy_writer.add(encoded)
            policy_record_id = policy_dedup[encoded.content_hash]
        elif ref and isinstance(ref, dict) and ref.get("sidecar"):
            pass  # non-friend sidecar refs not yet migrated
        elif not ref and not policy_hash:
            pass  # NO_POLICY_IN_SOURCE

        value = row["value"]
        value_i16 = int(round(float(value) * 100)) if value is not None else None
        label_rows.append(
            {
                "position_key": pos_key,
                "label_set_id": hashlib.blake2b(
                    f"{row['label_type']}:{row['source']}:{value}:{payload.get('policy_hash')}".encode(),
                    digest_size=8,
                ).digest(),
                "target_kind": target_kind,
                "value_i16": value_i16,
                "best_move_u8": int(row["best_move_u8"]) if row["best_move_u8"] is not None else None,
                "policy_record_id": policy_record_id,
                "observation_count": obs_count,
                "source_cohort": str(row["source"] or ""),
            }
        )

    for row in conn.execute(
        "SELECT position_id, source_cohort, visit_count, p0_wins, p1_wins, draws "
        "FROM observations ORDER BY position_id, source_cohort"
    ):
        pos_key = pos_id_to_key.get(int(row["position_id"]))
        if pos_key is None:
            continue
        obs_rows.append(
            {
                "position_key": pos_key,
                "source_cohort": str(row["source_cohort"] or ""),
                "observation_count": int(row["visit_count"] or 0),
                "p0_win_count": int(row["p0_wins"] or 0),
                "draw_count": int(row["draws"] or 0),
                "p1_win_count": int(row["p1_wins"] or 0),
            }
        )

    conn.close()

    labels_path = LABELS_DIR / "part-00000.parquet"
    obs_path = OBSERVATIONS_DIR / "part-00000.parquet"
    pq.write_table(pa.Table.from_pylist(label_rows), labels_path, compression=compression)
    pq.write_table(pa.Table.from_pylist(obs_rows), obs_path, compression=compression)

    bin_bytes, idx_bytes = policy_writer.finalize()
    bin_partial = POLICIES_DIR / "policy-00000.bin.partial"
    idx_partial = POLICIES_DIR / "policy-00000.idx.partial"
    bin_ready = POLICIES_DIR / "policy-00000.bin"
    idx_ready = POLICIES_DIR / "policy-00000.idx"
    bin_partial.write_bytes(bin_bytes)
    idx_partial.write_bytes(idx_bytes)
    bin_partial.replace(bin_ready)
    idx_partial.replace(idx_ready)

    elapsed = time.perf_counter() - t0
    manifest = {
        "schema_version": TEACHER_DATASET_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sqlite": str(sqlite_db),
        "immutable": True,
        "compression": compression,
        "counts": {
            "positions": len(pos_rows),
            "labels": len(label_rows),
            "observations": len(obs_rows),
            "unique_policies": len(policy_dedup),
            "policy_source_records": policy_audit.sidecar_refs,
        },
        "policy_status": policy_audit.status_counts,
        "parts": {
            "positions": [str(positions_path.relative_to(root)).replace("\\", "/")],
            "labels": [str(labels_path.relative_to(root)).replace("\\", "/")],
            "observations": [str(obs_path.relative_to(root)).replace("\\", "/")],
            "policies": [
                str(bin_ready.relative_to(root)).replace("\\", "/"),
                str(idx_ready.relative_to(root)).replace("\\", "/"),
            ],
        },
        "bytes": {
            "positions": positions_path.stat().st_size,
            "labels": labels_path.stat().st_size,
            "observations": obs_path.stat().st_size,
            "policy_bin": bin_ready.stat().st_size,
            "policy_idx": idx_ready.stat().st_size,
        },
        "build_seconds": elapsed,
        "manifest_hash": "",
    }
    manifest["manifest_hash"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    TEACHER_DATASET_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    TEACHER_DATASET_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    schema_doc = {
        "TEACHER_DATASET_SCHEMA_VERSION": TEACHER_DATASET_SCHEMA_VERSION,
        "columns": {
            "positions": list(pos_rows[0].keys()) if pos_rows else [],
            "labels": list(label_rows[0].keys()) if label_rows else [],
            "observations": list(obs_rows[0].keys()) if obs_rows else [],
        },
    }
    TEACHER_DATASET_SCHEMA.write_text(json.dumps(schema_doc, indent=2), encoding="utf-8")
    return manifest
