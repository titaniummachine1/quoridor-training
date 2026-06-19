"""Fail-closed guards for deprecated training-data paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from position_store_config import (
    CANONICAL_DB,
    CANONICAL_EXPORT_COMMAND,
    LEGACY_SMOKE_DBS,
    LEGACY_TRAINING_SOURCES,
)


class LegacyTrainingSourceError(RuntimeError):
    """Raised when active code tries to use a deprecated training-data source."""


def _norm(path: Path | str) -> Path:
    return Path(path).resolve()


def is_legacy_training_source(path: Path | str) -> bool:
    p = _norm(path)
    for legacy in LEGACY_TRAINING_SOURCES:
        if p == _norm(legacy):
            return True
    return False


def is_smoke_database(path: Path | str) -> bool:
    p = _norm(path)
    for smoke in LEGACY_SMOKE_DBS:
        if p == _norm(smoke):
            return True
    if "smoke" in p.name.lower() and p.suffix in {".db", ".bin"}:
        return True
    return False


def assert_canonical_training_db(path: Path | str, *, context: str = "training") -> Path:
    """Require the canonical production database for active training reads."""
    if os.environ.get("TI_ALLOW_LEGACY_TRAINING") == "1":
        return _norm(path)
    p = _norm(path)
    if is_legacy_training_source(p):
        raise LegacyTrainingSourceError(
            f"{context}: {p} is a legacy migration source.\n"
            f"Use the canonical position store: {CANONICAL_DB}\n"
            f"Export labeled rows with:\n  {CANONICAL_EXPORT_COMMAND}"
        )
    if is_smoke_database(p):
        raise LegacyTrainingSourceError(
            f"{context}: {p} is a smoke/test database, not production.\n"
            f"Use: {CANONICAL_DB}"
        )
    return p


def assert_not_legacy_write(path: Path | str, *, context: str = "write") -> Path:
    """Block accidental writes that would recreate ambiguous legacy stores."""
    if os.environ.get("TI_ALLOW_LEGACY_GAME_DB") == "1":
        return _norm(path)
    p = _norm(path)
    if is_legacy_training_source(p) and p.suffix == ".db":
        raise LegacyTrainingSourceError(
            f"{context}: writing to legacy game DB {p} is disabled.\n"
            "Self-play games must land in binary shards "
            f"(TI_POSITION_STORE_SHARD_INBOX) and be ingested via:\n"
            "  python training/position_store.py ingest-shards <inbox>\n"
            "For one-off legacy ingest only, set TI_ALLOW_LEGACY_GAME_DB=1."
        )
    return p


def guard_main(module_name: str) -> None:
    """Optional entry guard for legacy-only modules."""
    if os.environ.get("TI_LEGACY_IMPORT_OK") == "1":
        return
    print(
        f"WARNING: {module_name} is a LEGACY IMPORT / collection tool.\n"
        f"Canonical source of truth: {CANONICAL_DB}\n"
        f"See training/CANONICAL_DATASTORE.md",
        file=sys.stderr,
    )
