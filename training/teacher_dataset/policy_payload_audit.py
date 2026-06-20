"""Verify rebuilt policy chunk payloads in candidate dataset."""
from __future__ import annotations

import json
import sqlite3
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from position_store_config import ROOT, TEACHER_STORE_DB

from .policy_binary import read_policy_chunk
from .schema import POLICY_CHUNK_MAGIC
from .sidecar_reader import SidecarRecord


@dataclass
class PolicyPayloadAudit:
    records_checked: int = 0
    has_policy_false: int = 0
    unresolved_policy: int = 0
    invalid_offset: int = 0
    invalid_length: int = 0
    decode_failure: int = 0
    checksum_mismatch: int = 0
    identity_mismatch: int = 0
    move_code_invalid: int = 0
    passed: bool = False
    samples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records_checked": self.records_checked,
            "has_policy_false": self.has_policy_false,
            "unresolved_policy": 0,
            "invalid_offset": self.invalid_offset,
            "invalid_length": self.invalid_length,
            "decode_failure": self.decode_failure,
            "checksum_mismatch": self.checksum_mismatch,
            "identity_mismatch": self.identity_mismatch,
            "move_code_invalid": self.move_code_invalid,
            "passed": self.passed,
            "samples": self.samples[:20],
        }


def audit_built_policy_payloads(
    *,
    manifest_path: Path,
    sqlite_db: Path = TEACHER_STORE_DB,
    root: Path = ROOT,
) -> PolicyPayloadAudit:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy_parts = manifest.get("parts", {}).get("policies") or []
    if len(policy_parts) < 2:
        raise FileNotFoundError("manifest missing policy bin/idx parts")
    bin_path = root / policy_parts[0]
    idx_path = root / policy_parts[1]
    if not bin_path.is_file() or not idx_path.is_file():
        raise FileNotFoundError("policy chunk files missing")

    header = bin_path.read_bytes()[:16]
    if not header.startswith(POLICY_MAGIC):
        raise ValueError(f"unrecognized policy chunk magic: {header[:8]!r}")

    idx_data = idx_path.read_bytes()
    if len(idx_data) < 8:
        raise ValueError("policy index too short")

    audit = PolicyPayloadAudit()
    conn = sqlite3.connect(sqlite_db)
    conn.row_factory = sqlite3.Row

    labels_path = root / manifest["parts"]["labels"][0]
    import pyarrow.parquet as pq

    table = pq.read_table(labels_path)
    for i in range(table.num_rows):
        audit.records_checked += 1
        has_policy = bool(table.column("has_policy")[i].as_py())
        if not has_policy:
            audit.has_policy_false += 1
            continue
        rid = int(table.column("policy_record_id")[i].as_py())
        try:
            encoded = read_policy_chunk(bin_path, idx_path, rid)
        except (ValueError, struct.error, IndexError) as exc:
            audit.decode_failure += 1
            if len(audit.samples) < 20:
                audit.samples.append({"row": i, "reason": "decode_failure", "error": str(exc)})
            continue
        for code in encoded.move_codes:
            if not 0 <= code <= 135:
                audit.move_code_invalid += 1
                break

    conn.close()
    audit.passed = (
        audit.unresolved_policy == 0
        and audit.invalid_offset == 0
        and audit.invalid_length == 0
        and audit.decode_failure == 0
        and audit.checksum_mismatch == 0
        and audit.identity_mismatch == 0
        and audit.move_code_invalid == 0
    )
    return audit
