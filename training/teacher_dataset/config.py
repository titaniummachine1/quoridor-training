"""Paths and defaults for immutable teacher dataset layout."""
from __future__ import annotations

from pathlib import Path

from position_store_config import BACKUP_DIR, FRIEND_CORPUS_DIR, REPORT_DIR, ROOT, TEACHER_SIDECARS, TEACHER_STORE_DB

TEACHER_DATASET_DIR = ROOT / "training" / "data" / "teacher_dataset"
TEACHER_CATALOG_DB = ROOT / "training" / "data" / "canonical" / "teacher_catalog.duckdb"
TEACHER_DATASET_MANIFEST = TEACHER_DATASET_DIR / "manifest.json"
TEACHER_DATASET_SCHEMA = TEACHER_DATASET_DIR / "schema.json"
TEACHER_REFERENCE_DIR = ROOT / "training" / "data" / "canonical" / "teacher_sqlite_reference"

POSITIONS_DIR = TEACHER_DATASET_DIR / "positions"
LABELS_DIR = TEACHER_DATASET_DIR / "labels"
OBSERVATIONS_DIR = TEACHER_DATASET_DIR / "observations"
POLICIES_DIR = TEACHER_DATASET_DIR / "policies"
REJECTS_DIR = TEACHER_DATASET_DIR / "rejects"
DATASET_REPORTS_DIR = TEACHER_DATASET_DIR / "reports"

FRIEND_SIDECAR_GLOB = TEACHER_SIDECARS / "friend_selfplay" / "iter_*.policy.bin.gz"

# Legacy wrong path prefix written by Rust importer v0.1.0 (rel_root=repo root, not sidecar_dir)
LEGACY_WRONG_SIDECAR_PREFIX = "friend_selfplay/"
