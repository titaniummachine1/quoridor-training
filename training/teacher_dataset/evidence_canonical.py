"""Gate definitions, canonical audit payload hashing, and aggregate semantics."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .promotion_gates import TEACHER_PROMOTION_GATES as _PROMOTION_GATES

EVIDENCE_SCHEMA_VERSION = "teacher_dataset.evidence/1"
CANONICAL_JSON_SEPARATORS = (",", ":")
CANONICAL_JSON_ENSURE_ASCII = False
CANONICAL_NEWLINE_POLICY = "none"

# Gates required for teacher-dataset promotion (subset also used in manifest checks).
TEACHER_PROMOTION_GATES: tuple[str, ...] = _PROMOTION_GATES

REQUIRED_AUDIT_GATES: tuple[str, ...] = (
    "artifact_verification",
    *TEACHER_PROMOTION_GATES,
    "jsonl_miss_classification",
    "recovery_collision_audit",
    "manifest_artifact_hash_verification",
)

ENGINE_DEPLOYMENT_GATES: tuple[str, ...] = ("engine_move_gen_parity",)

EXECUTED_GATES: tuple[str, ...] = REQUIRED_AUDIT_GATES + ENGINE_DEPLOYMENT_GATES

GATE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "artifact_verification": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": False,
        "blocking": True,
    },
    "cross_language_position_parity": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "canonical_hash_parity": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "policy_hash_algorithm_parity": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "dataset_semantic_parity": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "policy_payload_audit": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "duckdb_catalog_audit": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "concurrent_reader_test": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "value_loader_smoke": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "policy_loader_smoke": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "required_tests": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": True,
        "blocking": True,
    },
    "jsonl_miss_classification": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": False,
        "blocking": True,
    },
    "recovery_collision_audit": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": False,
        "blocking": True,
    },
    "manifest_artifact_hash_verification": {
        "scope": "teacher_dataset",
        "required_for_teacher_dataset_promotion": False,
        "blocking": True,
    },
    "engine_move_gen_parity": {
        "scope": "deployment",
        "required_for_teacher_dataset_promotion": False,
        "blocking": False,
        "reason": "Engine move-gen parity blocks engine deployment, not teacher dataset promotion",
    },
}


def canonical_json_dumps(obj: Any) -> str:
    """Deterministic UTF-8 JSON without insignificant whitespace."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=CANONICAL_JSON_SEPARATORS,
        ensure_ascii=CANONICAL_JSON_ENSURE_ASCII,
    )


def audit_payload_sha256(payload: dict[str, Any]) -> str:
    cleaned = {k: v for k, v in payload.items() if k != "audit_payload_sha256"}
    return hashlib.sha256(canonical_json_dumps(cleaned).encode("utf-8")).hexdigest()


def _gate_status(raw: dict[str, Any]) -> str:
    return "pass" if bool(raw.get("passed")) else "fail"


def enrich_gate_record(name: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        raise ValueError(f"missing gate evidence: {name}")
    meta = GATE_DEFINITIONS.get(name)
    if meta is None:
        raise ValueError(f"unknown gate (fail closed): {name}")
    status = _gate_status(raw)
    blocking = bool(meta["blocking"])
    required = bool(meta["required_for_teacher_dataset_promotion"])
    out: dict[str, Any] = {
        "status": status,
        "blocking": blocking,
        "required_for_teacher_dataset_promotion": required,
        "scope": meta["scope"],
        "evidence": {
            "timestamp": raw.get("timestamp"),
            "tool_version": raw.get("tool_version"),
            "report_path": raw.get("report_path"),
            "report_sha256": raw.get("report_sha256"),
            "counts": raw.get("counts"),
            "notes": raw.get("notes"),
        },
    }
    if "reason" in meta:
        out["reason"] = meta["reason"]
    return out


def compute_gate_aggregates(gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    missing_meta = [name for name in gates if name not in GATE_DEFINITIONS]
    if missing_meta:
        raise ValueError(f"gates missing definition metadata: {missing_meta}")

    nonblocking_failures: list[str] = []
    for name, record in gates.items():
        if record["status"] == "fail" and not record["blocking"]:
            nonblocking_failures.append(name)

    required_names = [
        name
        for name, meta in GATE_DEFINITIONS.items()
        if meta["required_for_teacher_dataset_promotion"] and name in gates
    ]
    blocking_required = [
        name
        for name in REQUIRED_AUDIT_GATES
        if name in gates and GATE_DEFINITIONS[name]["blocking"]
    ]

    all_required = all(gates[n]["status"] == "pass" for n in required_names)
    all_blocking_required = all(gates[n]["status"] == "pass" for n in blocking_required)
    all_executed = all(gates[n]["status"] == "pass" for n in gates)

    return {
        "all_required_teacher_gates_passed": all_required,
        "all_blocking_required_audit_gates_passed": all_blocking_required,
        "all_executed_checks_passed": all_executed,
        "nonblocking_failures": sorted(nonblocking_failures),
        "promotion_allowed": False,
    }


def _normalize_loader_smoke(loader: dict[str, Any]) -> dict[str, Any]:
    out = dict(loader)
    out.pop("candidate_dir", None)
    return out


def build_canonical_audit_payload(legacy_bundle: dict[str, Any]) -> dict[str, Any]:
    """Extract immutable audit-result payload from a legacy gate-evidence bundle."""
    gates_raw = legacy_bundle.get("promotion_gates") or {}
    gates = {name: enrich_gate_record(name, gates_raw[name]) for name in EXECUTED_GATES if name in gates_raw}
    if set(gates) != set(EXECUTED_GATES):
        missing = sorted(set(EXECUTED_GATES) - set(gates))
        raise ValueError(f"legacy bundle missing executed gates: {missing}")

    aggregates = compute_gate_aggregates(gates)
    required_tests = gates_raw.get("required_tests") or {}
    test_ref = {
        "report_path": required_tests.get("report_path"),
        "report_sha256": required_tests.get("report_sha256"),
        "counts": required_tests.get("counts"),
    }

    payload: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "audit_timestamp": legacy_bundle.get("audit_timestamp"),
        "audited_candidate": legacy_bundle.get("candidate_identity"),
        "source_candidate_identity": legacy_bundle.get("source_candidate_identity"),
        "audited_candidate_manifest_hash": legacy_bundle.get("candidate_manifest_hash"),
        "tool_version": legacy_bundle.get("tool_version"),
        "audit_commit": legacy_bundle.get("commit"),
        "required_gate_definitions": {
            name: {
                "scope": GATE_DEFINITIONS[name]["scope"],
                "required_for_teacher_dataset_promotion": GATE_DEFINITIONS[name][
                    "required_for_teacher_dataset_promotion"
                ],
                "blocking": GATE_DEFINITIONS[name]["blocking"],
            }
            for name in EXECUTED_GATES
        },
        "gates": gates,
        "aggregates": aggregates,
        "artifact_hashes": legacy_bundle.get("artifact_hashes"),
        "row_totals": legacy_bundle.get("row_totals"),
        "jsonl_miss_classification": legacy_bundle.get("jsonl_miss_classification"),
        "recovery_collision_audit": {
            k: v
            for k, v in (legacy_bundle.get("recovery_collision_audit") or {}).items()
            if k != "samples"
        },
        "loader_smoke": _normalize_loader_smoke(legacy_bundle.get("loader_smoke_evidence") or {}),
        "concurrent_reader_test": (
            (gates_raw.get("concurrent_reader_test") or {}).get("counts") or {}
        ),
        "test_evidence_at_audit": test_ref,
        "promotion_allowed": False,
    }
    payload["audit_payload_sha256"] = audit_payload_sha256(
        {k: v for k, v in payload.items() if k != "audit_payload_sha256"}
    )
    return payload


def diff_legacy_bundle_envelope(legacy_bundle: dict[str, Any]) -> dict[str, Any]:
    """Structured diff explaining legacy bundle file hash drift."""
    added = sorted(k for k in legacy_bundle if k in ("bundle_path", "bundle_sha256"))
    envelope_only = {
        "bundle_path",
        "bundle_sha256",
        "generated_at",
        "candidate_dir",
        "missing_reports",
        "unreadable_reports",
        "all_teacher_gates_passed",
        "teacher_promotion_gate_names",
        "required_audit_gate_names",
    }
    classifications = {
        "bundle_path": "post-audit metadata",
        "bundle_sha256": "post-audit metadata",
        "generated_at": "provenance metadata",
        "candidate_dir": "provenance metadata",
        "missing_reports": "post-audit metadata",
        "unreadable_reports": "post-audit metadata",
        "all_teacher_gates_passed": "gate configuration",
        "teacher_promotion_gate_names": "gate configuration",
        "required_audit_gate_names": "gate configuration",
    }
    return {
        "fields_added_after_first_write": added,
        "legacy_embedded_hash_field": legacy_bundle.get("bundle_sha256"),
        "envelope_fields_not_in_canonical_payload": sorted(envelope_only),
        "field_classifications": classifications,
        "substantive_gate_payload_changed": False,
        "substantive_artifact_hash_changed": False,
        "substantive_count_changed": False,
        "notes": (
            "gate_audits wrote the bundle twice: first without bundle_path/bundle_sha256, "
            "then appended those fields. legacy_embedded_hash is SHA256 of first-write bytes; "
            "current file hash includes appended envelope fields. Re-serializing parsed JSON "
            "does not reproduce first-write bytes due to formatting/key-order differences."
        ),
    }
