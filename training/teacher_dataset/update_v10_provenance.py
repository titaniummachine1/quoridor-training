"""Update tracked v10 provenance to reference final immutable evidence envelope."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from titanium_training.store.config import ROOT

from .promotion_gates import sha256_file


def update_v10_provenance(
    *,
    final_envelope_rel: str,
    audit_payload_sha256: str,
    final_bundle_file_sha256: str,
    sha256_sidecar_rel: str,
    test_evidence_rel: str = "training/data/position_store_reports/teacher_dataset_test_evidence.json",
    test_evidence_sha256: str = "4f6fc09f35e95494d934a065451160688204d899818efc9b2eade30a4b1af785",
    legacy_bundle_rel: str = (
        "training/data/position_store_reports/"
        "gate_evidence_bundle_teacher_dataset_candidate_v9_20260620T101843Z.json"
    ),
    legacy_original_audit_completion_hash: str = (
        "91432f4892677ea5c17fd9804cbd9582f994a850af3e4f94492b19c25ba58a62"
    ),
    legacy_current_file_sha256: str | None = None,
    aggregates: dict | None = None,
    tracked_out: Path | None = None,
) -> Path:
    v10 = ROOT / "training" / "data" / "teacher_dataset_candidate_v10"
    v9 = ROOT / "training" / "data" / "teacher_dataset_candidate_v9"
    artifact_rels = [
        "positions/part-00000.parquet",
        "labels/part-00000.parquet",
        "observations/part-00000.parquet",
        "policies/policy-00000.bin",
        "policies/policy-00000.idx",
    ]
    if legacy_current_file_sha256 is None and (ROOT / legacy_bundle_rel).is_file():
        legacy_current_file_sha256 = sha256_file(ROOT / legacy_bundle_rel)

    payload = {
        "record_type": "teacher_dataset_candidate_provenance",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_candidate": "teacher_dataset_candidate_v9",
        "source_audit_timestamp": "20260620T101843Z",
        "evidence": {
            "final_envelope_path": final_envelope_rel.replace("\\", "/"),
            "audit_payload_sha256": audit_payload_sha256,
            "final_bundle_file_sha256": final_bundle_file_sha256,
            "final_bundle_sha256_sidecar": sha256_sidecar_rel.replace("\\", "/"),
        },
        "legacy_evidence_bundle": {
            "path": legacy_bundle_rel.replace("\\", "/"),
            "original_audit_completion_hash": legacy_original_audit_completion_hash,
            "meaning_original_audit_completion_hash": (
                "SHA256 of legacy bundle file bytes after first atomic write, before "
                "bundle_path and bundle_sha256 fields were appended"
            ),
            "current_legacy_file_sha256": legacy_current_file_sha256,
            "meaning_current_legacy_file_sha256": (
                "SHA256 of the preserved legacy bundle file including appended envelope fields"
            ),
        },
        "test_evidence": {
            "path": test_evidence_rel.replace("\\", "/"),
            "sha256": test_evidence_sha256,
        },
        "target_candidate": "teacher_dataset_candidate_v10",
        "target_candidate_local_path": str(v10).replace("\\", "/"),
        "v10_manifest_path": "training/data/teacher_dataset_candidate_v10/manifest.json",
        "v10_manifest_sha256": json.loads((v10 / "manifest.json").read_text(encoding="utf-8")).get("manifest_hash"),
        "promotion_allowed": False,
        "aggregates": aggregates or {},
        "artifact_hashes_v9": {
            f"training/data/teacher_dataset_candidate_v9/{rel}": sha256_file(v9 / rel)
            for rel in artifact_rels
        },
        "finalization_notes": (
            "First finalize attempt briefly wrote promotion_allowed=true inside .partial; "
            "that directory was deleted before publication. Current v10 was atomically "
            "published with promotion_allowed=false. Final gate evidence uses "
            "audit_payload_sha256 for immutable audit results and final_bundle_file_sha256 "
            "for the exact final envelope bytes."
        ),
    }

    tracked = tracked_out or (
        Path(__file__).resolve().parent / "candidate_provenance" / "teacher_dataset_v10.json"
    )
    tracked.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=tracked.parent, prefix=f"{tracked.stem}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(tracked)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return tracked
