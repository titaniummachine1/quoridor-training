"""CLI handlers for teacher dataset commands."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from position_store_config import REPORT_DIR, TEACHER_STORE_DB

from .audit_policies import audit_teacher_policies, diagnose_sidecar_root_cause, write_policy_audit_report
from .build import build_teacher_dataset
from .catalog import benchmark_readers, build_teacher_catalog
from .config import TEACHER_CATALOG_DB, TEACHER_DATASET_CANDIDATE_DIR, TEACHER_DATASET_CANDIDATE_MANIFEST
from .position_parity import audit_friend_position_parity, write_parity_report
from .freeze_reference import mark_sqlite_reference
from .reconcile import reconcile_teacher_counts


def cmd_verify_teacher_policies(args) -> int:
    result = audit_teacher_policies(
        args.teacher_db,
        verify_payloads=not args.path_only,
        limit=args.limit,
    )
    diagnosis = diagnose_sidecar_root_cause(args.teacher_db)
    report = {"diagnosis": diagnosis, "audit": result.to_dict()}
    out = write_policy_audit_report(result, out_dir=args.reports)
    report["report_path"] = str(out)
    print(json.dumps(report, indent=2))
    return 0 if result.passed else 1


def cmd_freeze_teacher_reference(args) -> int:
    manifest = mark_sqlite_reference(args.teacher_db)
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_build_teacher_dataset(args) -> int:
    manifest = build_teacher_dataset(
        output_dir=args.output,
        sqlite_db=args.teacher_db,
        compression=args.compression,
    )
    if args.catalog:
        cat = build_teacher_catalog(args.catalog, manifest_path=args.output / "manifest.json")
        manifest["catalog"] = cat
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_audit_position_parity(args) -> int:
    report = audit_friend_position_parity(teacher_db=args.teacher_db, limit=args.limit)
    path = write_parity_report(report, out_dir=args.reports)
    out = report.to_dict()
    out["report_path"] = str(path)
    print(json.dumps(out, indent=2))
    return 0 if report.passed else 1


def cmd_audit_teacher_dataset(args) -> int:
    manifest_path = args.output / "manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"error": "manifest missing — run build-teacher-dataset first"}, indent=2))
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy = audit_teacher_policies(args.teacher_db, verify_payloads=True)
    print(json.dumps({"manifest": manifest, "sqlite_policy_audit": policy.to_dict()}, indent=2))
    return 0 if policy.passed else 1


def cmd_stats_teacher_dataset(args) -> int:
    manifest_path = args.output / "manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"error": "manifest missing"}, indent=2))
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stats = {"manifest": manifest}
    if args.catalog.exists():
        stats["read_benchmark"] = benchmark_readers(args.catalog)
    print(json.dumps(stats, indent=2))
    return 0


def cmd_benchmark_teacher_readers(args) -> int:
    if not args.catalog.exists():
        build_teacher_catalog(args.catalog)
    print(json.dumps(benchmark_readers(args.catalog), indent=2))
    return 0


def cmd_reconcile_teacher_source(args) -> int:
    print(json.dumps(reconcile_teacher_counts(args.teacher_db), indent=2))
    return 0


def cmd_verify_candidate(args) -> int:
    """Read-only post-build verification: manifest gates, no partial files, row counts.

    Does NOT promote. Run --promote step explicitly after this passes.
    """
    output_dir = getattr(args, "output", TEACHER_DATASET_CANDIDATE_DIR)
    manifest_path = output_dir / "manifest.json"
    schema_path = output_dir / "schema.json"
    issues: list[str] = []

    if not manifest_path.exists():
        print(json.dumps({"error": "manifest.json missing — build has not completed or was interrupted"}, indent=2))
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if not schema_path.exists():
        issues.append("schema.json missing")

    partial_files = [str(p) for p in output_dir.rglob("*.partial")]
    if partial_files:
        issues.append(f"partial files remain: {partial_files}")

    counts = manifest.get("counts", {})
    gate_status = {
        "promotion_allowed": manifest.get("promotion_allowed", False),
        "engine_parity_verified": manifest.get("engine_parity_verified", False),
        "cross_language_position_parity": manifest.get("cross_language_position_parity", False),
    }
    unresolved = manifest.get("policy_resolution", {}).get("unresolved", 0)
    if unresolved:
        issues.append(f"unresolved_policies={unresolved}")

    result = {
        "candidate_dir": str(output_dir),
        "manifest_exists": True,
        "schema_exists": schema_path.exists(),
        "partial_files": partial_files,
        "schema_version": manifest.get("schema_version"),
        "created_at": manifest.get("created_at"),
        "counts": counts,
        "gate_status": gate_status,
        "policy_resolution": manifest.get("policy_resolution", {}),
        "issues": issues,
        "ready_for_promotion": len(issues) == 0 and gate_status["engine_parity_verified"],
    }
    print(json.dumps(result, indent=2))
    return 0 if not issues else 1
