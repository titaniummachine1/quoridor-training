"""CLI handlers for teacher dataset commands."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from position_store_config import REPORT_DIR, TEACHER_STORE_DB

from .audit_policies import audit_teacher_policies, diagnose_sidecar_root_cause, write_policy_audit_report
from .build import build_teacher_dataset
from .catalog import benchmark_readers, build_teacher_catalog
from .config import TEACHER_CATALOG_DB, TEACHER_DATASET_DIR, TEACHER_DATASET_MANIFEST
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
        cat = build_teacher_catalog(args.catalog)
        manifest["catalog"] = cat
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_audit_teacher_dataset(args) -> int:
    if not TEACHER_DATASET_MANIFEST.exists():
        print(json.dumps({"error": "manifest missing — run build-teacher-dataset first"}, indent=2))
        return 1
    manifest = json.loads(TEACHER_DATASET_MANIFEST.read_text(encoding="utf-8"))
    policy = audit_teacher_policies(args.teacher_db, verify_payloads=True)
    print(json.dumps({"manifest": manifest, "sqlite_policy_audit": policy.to_dict()}, indent=2))
    return 0 if policy.passed else 1


def cmd_stats_teacher_dataset(args) -> int:
    if not TEACHER_DATASET_MANIFEST.exists():
        print(json.dumps({"error": "manifest missing"}, indent=2))
        return 1
    manifest = json.loads(TEACHER_DATASET_MANIFEST.read_text(encoding="utf-8"))
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
