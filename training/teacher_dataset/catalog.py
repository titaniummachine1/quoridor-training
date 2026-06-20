"""Read-only DuckDB catalog over immutable teacher Parquet parts."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import duckdb

from position_store_config import ROOT

from .config import TEACHER_CATALOG_DB, TEACHER_DATASET_CANDIDATE_MANIFEST
from .schema import CATALOG_SCHEMA_VERSION


def build_teacher_catalog(
    catalog_path: Path = TEACHER_CATALOG_DB,
    *,
    manifest_path: Path = TEACHER_DATASET_CANDIDATE_MANIFEST,
    root: Path = ROOT,
) -> dict[str, Any]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"dataset manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if catalog_path.exists():
        catalog_path.unlink()

    con = duckdb.connect(str(catalog_path))
    con.execute(
        """
        CREATE TABLE dataset_versions (
            schema_version INTEGER,
            manifest_hash VARCHAR,
            created_at VARCHAR,
            source_sqlite VARCHAR,
            immutable BOOLEAN
        )
        """
    )
    con.execute(
        "INSERT INTO dataset_versions VALUES (?, ?, ?, ?, ?)",
        [
            manifest.get("schema_version"),
            manifest.get("manifest_hash"),
            manifest.get("created_at"),
            manifest.get("source_sqlite"),
            True,
        ],
    )
    con.execute(
        """
        CREATE TABLE semantic_checksums (
            manifest_hash VARCHAR,
            position_count BIGINT,
            label_count BIGINT,
            observation_count BIGINT,
            unique_policies BIGINT
        )
        """
    )
    counts = manifest.get("counts") or {}
    con.execute(
        "INSERT INTO semantic_checksums VALUES (?, ?, ?, ?, ?)",
        [
            manifest.get("manifest_hash"),
            counts.get("positions"),
            counts.get("labels"),
            counts.get("observations"),
            counts.get("unique_policies"),
        ],
    )

    pos_glob = str((root / manifest["parts"]["positions"][0]).as_posix())
    labels_glob = str((root / manifest["parts"]["labels"][0]).as_posix())
    obs_glob = str((root / manifest["parts"]["observations"][0]).as_posix())

    con.execute(f"CREATE VIEW teacher_positions AS SELECT * FROM read_parquet('{pos_glob}')")
    con.execute(f"CREATE VIEW teacher_labels AS SELECT * FROM read_parquet('{labels_glob}')")
    con.execute(f"CREATE VIEW teacher_observations AS SELECT * FROM read_parquet('{obs_glob}')")
    con.execute(
        """
        CREATE VIEW teacher_training_rows AS
        SELECT
            l.position_key,
            l.label_set_id,
            l.target_kind,
            l.value_i16,
            l.best_move_u8,
            l.policy_record_id,
            l.observation_count,
            l.source_cohort,
            p.canonical_hash,
            p.packed_state,
            p.side_to_move
        FROM teacher_labels l
        JOIN teacher_positions p USING (position_key)
        """
    )
    con.execute(
        "CREATE TABLE catalog_metadata(key VARCHAR PRIMARY KEY, value VARCHAR)"
    )
    con.execute(
        "INSERT INTO catalog_metadata VALUES ('catalog_schema_version', ?)",
        [str(CATALOG_SCHEMA_VERSION)],
    )
    con.execute(
        "INSERT INTO catalog_metadata VALUES ('read_only', 'true')"
    )
    con.close()

    return {
        "catalog_path": str(catalog_path),
        "manifest_hash": manifest.get("manifest_hash"),
        "catalog_bytes": catalog_path.stat().st_size,
        "views": ["teacher_positions", "teacher_labels", "teacher_observations", "teacher_training_rows"],
    }


def benchmark_readers(catalog_path: Path = TEACHER_CATALOG_DB) -> dict[str, Any]:
    import time

    con = duckdb.connect(str(catalog_path), read_only=True)
    t0 = time.perf_counter()
    n_pos = con.execute("SELECT COUNT(*) FROM teacher_positions").fetchone()[0]
    t_pos = time.perf_counter() - t0

    t0 = time.perf_counter()
    n_labels = con.execute("SELECT COUNT(*) FROM teacher_labels").fetchone()[0]
    t_labels = time.perf_counter() - t0

    t0 = time.perf_counter()
    n_val = con.execute(
        "SELECT COUNT(*) FROM teacher_training_rows WHERE policy_record_id IS NULL"
    ).fetchone()[0]
    t_val = time.perf_counter() - t0

    t0 = time.perf_counter()
    n_pol = con.execute(
        "SELECT COUNT(*) FROM teacher_training_rows WHERE policy_record_id IS NOT NULL"
    ).fetchone()[0]
    t_pol = time.perf_counter() - t0
    con.close()

    return {
        "positions": int(n_pos),
        "labels": int(n_labels),
        "value_only_rows": int(n_val),
        "value_plus_policy_rows": int(n_pol),
        "value_only_scan_sec": t_pos + t_labels + t_val,
        "value_plus_policy_scan_sec": t_pos + t_labels + t_pol,
    }
