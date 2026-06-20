"""Freeze SQLite teacher store as correctness reference."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium_training.store.config import BACKUP_DIR, ROOT, TEACHER_STORE_DB
from titanium_training.store.lib import db_summary
from titanium_training.store.teacher import teacher_semantic_checksum

from .config import TEACHER_REFERENCE_DIR
from .schema import TEACHER_DATASET_SCHEMA_VERSION


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def table_sizes(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    sizes: dict[str, int] = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        row = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
        sizes[name] = int(row[0]) if row else 0
    conn.close()
    return sizes


def mark_sqlite_reference(db_path: Path = TEACHER_STORE_DB, *, copy_to: Path | None = None) -> dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    TEACHER_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    ref_copy = copy_to or (BACKUP_DIR / f"position_teacher_store_reference_{stamp}.db")
    if not ref_copy.exists() or ref_copy.resolve() != db_path.resolve():
        shutil.copy2(db_path, ref_copy)

    ref_link = TEACHER_REFERENCE_DIR / "position_teacher_store.db"
    if not ref_link.exists():
        shutil.copy2(db_path, ref_link)

    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("store_role", "teacher_sqlite_correctness_reference"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("training_active", "false"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("reference_frozen_at", now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("teacher_dataset_schema_target", str(TEACHER_DATASET_SCHEMA_VERSION)),
    )
    conn.commit()
    conn.close()

    manifest = {
        "frozen_at": now,
        "source_db": str(db_path),
        "reference_copy": str(ref_copy),
        "reference_mirror": str(ref_link),
        "sha256": sha256_file(ref_copy),
        "bytes": ref_copy.stat().st_size,
        "counts": db_summary(db_path),
        "table_sizes": table_sizes(db_path),
        "semantic_checksum": teacher_semantic_checksum(db_path),
        "store_role": "teacher_sqlite_correctness_reference",
        "training_active": False,
    }
    manifest_path = TEACHER_REFERENCE_DIR / "reference_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
