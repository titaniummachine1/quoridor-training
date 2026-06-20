"""Diagnose and verify teacher policy sidecar references."""
from __future__ import annotations

import json
import sqlite3
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium_training.store.config import REPORT_DIR, ROOT, TEACHER_STORE_DB

from .config import DATASET_REPORTS_DIR
from .schema import (
    NO_POLICY_IN_SOURCE,
    POLICY_QUARANTINED,
    POLICY_RECOVERED,
    POLICY_REJECTED,
)
from .policy_recovery import recover_policy_record
from .sidecar_paths import classify_sidecar_path, resolve_sidecar_path


@dataclass
class PolicyAuditResult:
    breakdown: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    sidecar_refs: int = 0
    labels_without_sidecar_ref: int = 0
    unresolved: int = 0
    sample_failures: list[dict[str, Any]] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "breakdown": self.breakdown,
            "status_counts": self.status_counts,
            "sidecar_refs": self.sidecar_refs,
            "labels_without_sidecar_ref": self.labels_without_sidecar_ref,
            "unresolved_policy_references": self.unresolved,
            "passed": self.passed,
            "sample_failures": self.sample_failures[:20],
        }


def _parse_sidecar_ref(payload: dict[str, Any]) -> dict[str, Any] | None:
    ref = payload.get("sidecar_ref") or payload.get("sidecar")
    if not ref:
        return None
    if isinstance(ref, str):
        return {"sidecar": ref}
    return ref if isinstance(ref, dict) else None


def audit_teacher_policies(
    db_path: Path = TEACHER_STORE_DB,
    *,
    root: Path = ROOT,
    verify_payloads: bool = True,
    limit: int | None = None,
) -> PolicyAuditResult:
    result = PolicyAuditResult()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = (
        "SELECT l.label_id, l.payload_json, p.canonical_hash, p.packed_state "
        "FROM labels l JOIN positions p ON p.position_id = l.position_id"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    for row in conn.execute(query):
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            result.breakdown["missing_because_invalid_payload_json"] = (
                result.breakdown.get("missing_because_invalid_payload_json", 0) + 1
            )
            result.status_counts[POLICY_REJECTED] = result.status_counts.get(POLICY_REJECTED, 0) + 1
            result.unresolved += 1
            continue

        ref = _parse_sidecar_ref(payload)
        if ref is None:
            result.labels_without_sidecar_ref += 1
            result.status_counts[NO_POLICY_IN_SOURCE] = result.status_counts.get(NO_POLICY_IN_SOURCE, 0) + 1
            continue

        result.sidecar_refs += 1
        stored = str(ref.get("sidecar") or ref.get("path") or "")
        cls = classify_sidecar_path(stored, root=root)
        result.breakdown[cls] = result.breakdown.get(cls, 0) + 1

        if not verify_payloads:
            if cls.startswith("path_ok") or cls.startswith("repaired_"):
                result.status_counts[POLICY_RECOVERED] = result.status_counts.get(POLICY_RECOVERED, 0) + 1
            else:
                result.unresolved += 1
            continue

        try:
            path = resolve_sidecar_path(stored, root=root)
        except ValueError:
            result.unresolved += 1
            result.status_counts[POLICY_REJECTED] = result.status_counts.get(POLICY_REJECTED, 0) + 1
            continue

        if not path.is_file():
            result.breakdown["missing_because_file_absent"] = result.breakdown.get("missing_because_file_absent", 0) + 1
            result.unresolved += 1
            if len(result.sample_failures) < 20:
                result.sample_failures.append(
                    {"stored": stored, "reason": "file_absent", "label_id": row["label_id"]}
                )
            continue

        policy_hash = payload.get("policy_hash")
        canonical = bytes(row["canonical_hash"])
        record = recover_policy_record(
            stored,
            canonical_hash=canonical,
            packed_state=bytes(row["packed_state"]),
            policy_hash=str(policy_hash) if policy_hash else None,
            root=root,
        )
        if record is None:
            result.breakdown["missing_because_policy_not_in_sidecar_scan"] = (
                result.breakdown.get("missing_because_policy_not_in_sidecar_scan", 0) + 1
            )
            result.unresolved += 1
            continue

        expected_len = int(ref.get("policy_len", len(record.move_codes)))
        if len(record.move_codes) != expected_len:
            result.breakdown["missing_because_invalid_length"] = (
                result.breakdown.get("missing_because_invalid_length", 0) + 1
            )
            result.unresolved += 1
            continue

        result.status_counts[POLICY_RECOVERED] = result.status_counts.get(POLICY_RECOVERED, 0) + 1

    conn.close()
    result.passed = result.unresolved == 0
    return result


def write_policy_audit_report(result: PolicyAuditResult, *, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or DATASET_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"policy_audit_{stamp}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return path


def diagnose_sidecar_root_cause(db_path: Path = TEACHER_STORE_DB, *, root: Path = ROOT) -> dict[str, Any]:
    """Fast root-cause summary using SQL sampling — not a full-table scan."""
    conn = sqlite3.connect(db_path)
    sample = conn.execute(
        "SELECT payload_json FROM labels WHERE payload_json LIKE '%sidecar_ref%' LIMIT 5"
    ).fetchall()
    samples: list[str] = []
    for (raw,) in sample:
        payload = json.loads(raw)
        ref = _parse_sidecar_ref(payload)
        if ref:
            samples.append(str(ref.get("sidecar") or ""))
    c_prefix = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE payload_json LIKE '%\"sidecar\": \"C:%'"
    ).fetchone()[0]
    rel_prefix = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE payload_json LIKE '%teacher_sidecars/friend_selfplay%'"
    ).fetchone()[0]
    total_refs = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE payload_json LIKE '%sidecar%'"
    ).fetchone()[0]
    conn.close()
    on_disk = len(list((root / "training/data/canonical/teacher_sidecars/friend_selfplay").glob("*.policy.bin.gz")))
    return {
        "root_cause": (
            "Rust importer v0.1.0 stored sidecar paths as {repo_root}/friend_selfplay/*.policy.bin.gz "
            "while files were written to training/data/canonical/teacher_sidecars/friend_selfplay/. "
            "Audit used ROOT/stored_path without remapping, producing ~2M false sidecar_missing."
        ),
        "sidecar_ref_labels": int(total_refs),
        "stored_path_C_drive_prefix": int(c_prefix),
        "stored_path_teacher_sidecars_relative": int(rel_prefix),
        "sample_stored_paths": samples,
        "on_disk_sidecar_files": on_disk,
        "repair": "Resolve via training/data/canonical/teacher_sidecars/friend_selfplay/{filename}",
    }
