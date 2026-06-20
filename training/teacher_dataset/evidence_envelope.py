"""Write immutable final teacher gate evidence envelope and sidecar hash."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from position_store_config import REPORT_DIR, ROOT

from .evidence_canonical import (
    EVIDENCE_SCHEMA_VERSION,
    audit_payload_sha256,
    build_canonical_audit_payload,
    diff_legacy_bundle_envelope,
)
from .promotion_gates import sha256_file


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.stem}.", suffix=".partial")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tmp_path.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def write_sha256_sidecar(target_file: Path, *, sidecar_path: Path | None = None) -> Path:
    sidecar = sidecar_path or Path(str(target_file) + ".sha256")
    digest = sha256_file(target_file)
    line = f"{digest}  {target_file.name}\n"
    _atomic_write_text(sidecar, line)
    return sidecar


def build_final_evidence_envelope(
    legacy_bundle: dict[str, Any],
    *,
    audit_payload: dict[str, Any] | None = None,
    post_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit_payload = audit_payload or build_canonical_audit_payload(legacy_bundle)
    payload_hash = audit_payload.get("audit_payload_sha256") or audit_payload_sha256(audit_payload)
    if payload_hash != audit_payload_sha256({k: v for k, v in audit_payload.items() if k != "audit_payload_sha256"}):
        raise ValueError("audit_payload_sha256 field mismatch")

    envelope: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "audit_timestamp": legacy_bundle.get("audit_timestamp"),
        "audited_candidate": legacy_bundle.get("candidate_identity"),
        "audit_payload": audit_payload,
        "audit_payload_sha256": payload_hash,
        "aggregates": audit_payload["aggregates"],
        "promotion_allowed": False,
        "legacy_bundle_reference": {
            "path": "training/data/position_store_reports/"
            "gate_evidence_bundle_teacher_dataset_candidate_v9_20260620T101843Z.json",
            "original_audit_completion_hash": legacy_bundle.get("bundle_sha256"),
            "current_legacy_file_sha256": None,
            "hash_drift_explanation": diff_legacy_bundle_envelope(legacy_bundle),
        },
        "post_audit": post_audit or {},
    }
    return envelope


def finalize_evidence_envelope(
    legacy_bundle_path: Path,
    *,
    final_path: Path | None = None,
    reports_dir: Path = REPORT_DIR,
    post_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    legacy_bundle_path = legacy_bundle_path.resolve()
    legacy = json.loads(legacy_bundle_path.read_text(encoding="utf-8"))
    stamp = legacy.get("audit_timestamp") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = legacy.get("candidate_identity") or "teacher_dataset_candidate_v9"
    final_path = final_path or (
        reports_dir / f"gate_evidence_bundle_{candidate}_{stamp}.final.json"
    )
    legacy_on_disk = sha256_file(legacy_bundle_path)
    envelope = build_final_evidence_envelope(legacy, post_audit=post_audit)
    envelope["legacy_bundle_reference"]["current_legacy_file_sha256"] = legacy_on_disk

    text = json.dumps(envelope, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(final_path, text)
    sidecar = write_sha256_sidecar(final_path)
    file_hash = sha256_file(final_path)

    return {
        "final_envelope_path": str(final_path.relative_to(ROOT)).replace("\\", "/"),
        "final_bundle_file_sha256": file_hash,
        "sha256_sidecar_path": str(sidecar.relative_to(ROOT)).replace("\\", "/"),
        "audit_payload_sha256": envelope["audit_payload_sha256"],
        "aggregates": envelope["aggregates"],
        "legacy_original_audit_completion_hash": legacy.get("bundle_sha256"),
        "legacy_current_file_sha256": legacy_on_disk,
    }


def validate_final_envelope(path: Path, *, sidecar_path: Path | None = None) -> dict[str, Any]:
    path = path.resolve()
    sidecar = sidecar_path or Path(str(path) + ".sha256")
    if not sidecar.is_file():
        raise FileNotFoundError(f"missing sidecar: {sidecar}")
    expected_line = sidecar.read_text(encoding="utf-8").strip().split()
    if len(expected_line) < 2 or expected_line[1] != path.name:
        raise ValueError("sidecar filename mismatch")
    file_hash = sha256_file(path)
    if expected_line[0] != file_hash:
        raise ValueError("sidecar hash mismatch")

    envelope = json.loads(path.read_text(encoding="utf-8"))
    payload = envelope.get("audit_payload") or {}
    payload_copy = {k: v for k, v in payload.items() if k != "audit_payload_sha256"}
    recomputed = audit_payload_sha256(payload_copy)
    if recomputed != envelope.get("audit_payload_sha256"):
        raise ValueError("audit_payload_sha256 mismatch")
    if envelope.get("promotion_allowed") is not False:
        raise ValueError("promotion_allowed must be false")

    return {
        "valid": True,
        "final_bundle_file_sha256": file_hash,
        "audit_payload_sha256": recomputed,
        "aggregates": envelope.get("aggregates"),
    }
