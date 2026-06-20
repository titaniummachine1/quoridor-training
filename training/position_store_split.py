"""Split recovery: preserve combined artifact, restore game store, populate teacher store."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from position_store_config import (
    BACKUP_DIR,
    COMBINED_PARTIAL_FRIEND_ARTIFACT,
    GAME_STORE_DB,
    LEGACY_COMBINED_DB,
    MIGRATION_ARTIFACT_DIR,
    REPORT_DIR,
    ROOT,
    TEACHER_SIDECARS,
    TEACHER_STORE_DB,
)
from position_store_friend import discover_friend_shards, import_friend_shards
from position_store_lib import db_summary, semantic_checksum, sha256_file
from position_store_teacher import (
    audit_game_store,
    audit_teacher_store,
    import_teacher_sources,
    init_game_store,
    init_teacher_store,
    strip_pathless_artifacts_from_game_store,
    verify_codec_parity,
)

PRE_FRIEND_BACKUP = BACKUP_DIR / "position_store_v2_pre_friend_20260619T165936Z.db"
PRE_FRIEND_META = BACKUP_DIR / "position_store_v2_pre_friend_20260619T165936Z.json"


@dataclass
class SplitMigrationResult:
    run_id: str
    combined_artifact: str
    game_store: str
    teacher_store: str
    backup_verified: bool
    game_summary: dict[str, Any] = field(default_factory=dict)
    teacher_summary: dict[str, Any] = field(default_factory=dict)
    strip_report: dict[str, int] = field(default_factory=dict)
    friend_import: dict[str, Any] = field(default_factory=dict)
    teacher_import: dict[str, Any] = field(default_factory=dict)
    game_audit: dict[str, Any] = field(default_factory=dict)
    teacher_audit: dict[str, Any] = field(default_factory=dict)
    codec_parity: dict[str, Any] = field(default_factory=dict)
    sidecar_bytes: int = 0


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def preserve_combined_db() -> Path:
    MIGRATION_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    src = LEGACY_COMBINED_DB
    dst = COMBINED_PARTIAL_FRIEND_ARTIFACT
    if dst.exists():
        return dst
    if not src.exists():
        raise FileNotFoundError(f"combined database missing: {src}")
    try:
        shutil.move(str(src), str(dst))
    except (PermissionError, OSError):
        shutil.copy2(src, dst)
    return dst


def restore_game_store_from_backup(*, backup_path: Path = PRE_FRIEND_BACKUP) -> dict[str, Any]:
    if not backup_path.exists():
        raise FileNotFoundError(f"pre-friend backup missing: {backup_path}")
    GAME_STORE_DB.parent.mkdir(parents=True, exist_ok=True)
    if GAME_STORE_DB.exists():
        GAME_STORE_DB.unlink()
    shutil.copy2(backup_path, GAME_STORE_DB)
    digest = sha256_file(GAME_STORE_DB)
    meta: dict[str, Any] = {}
    if PRE_FRIEND_META.exists():
        meta = json.loads(PRE_FRIEND_META.read_text(encoding="utf-8"))
    expected_sha = meta.get("backup_sha256")
    expected_summary = meta.get("summary") or {}
    expected_checksum = meta.get("semantic_checksum") or {}
    summary = db_summary(GAME_STORE_DB)
    checksum = semantic_checksum(GAME_STORE_DB)
    verified = expected_sha == digest and summary == expected_summary and checksum == expected_checksum
    return {
        "backup_path": str(backup_path),
        "game_store": str(GAME_STORE_DB),
        "sha256": digest,
        "expected_sha256": expected_sha,
        "summary": summary,
        "expected_summary": expected_summary,
        "semantic_checksum": checksum,
        "expected_semantic_checksum": expected_checksum,
        "verified": verified,
    }


def run_split_migration(
    *,
    skip_friend_import: bool = False,
    run_id: str | None = None,
) -> SplitMigrationResult:
    run = run_id or utc_stamp()
    combined = preserve_combined_db()
    restore = restore_game_store_from_backup()
    if not restore["verified"]:
        raise RuntimeError("game store restore verification failed — aborting split migration")
    init_game_store(GAME_STORE_DB)
    strip = strip_pathless_artifacts_from_game_store(GAME_STORE_DB)
    init_teacher_store(TEACHER_STORE_DB)
    teacher_imp = import_teacher_sources(TEACHER_STORE_DB, run_id=run)
    friend_result: dict[str, Any] = {"skipped": True}
    if not skip_friend_import:
        friend = import_friend_shards(
            db_path=TEACHER_STORE_DB,
            dry_run=False,
            run_id=run,
            skip_backup=True,
        )
        friend_result = {
            "totals": friend.totals,
            "before": friend.before,
            "after": friend.after,
            "aggregated_labels": sum(r.get("aggregated_labels", 0) for r in friend.per_shard),
            "new_labels": sum(r.get("new_labels", 0) for r in friend.per_shard),
            "sidecar_bytes": sum(r.get("sidecar_bytes", 0) for r in friend.per_shard),
        }
    sidecar_bytes = sum(p.stat().st_size for p in TEACHER_SIDECARS.rglob("*") if p.is_file())
    game_audit = audit_game_store(GAME_STORE_DB)
    teacher_audit = audit_teacher_store(TEACHER_STORE_DB)
    codec = verify_codec_parity(GAME_STORE_DB, TEACHER_STORE_DB)
    result = SplitMigrationResult(
        run_id=run,
        combined_artifact=str(combined),
        game_store=str(GAME_STORE_DB),
        teacher_store=str(TEACHER_STORE_DB),
        backup_verified=bool(restore["verified"]),
        game_summary=db_summary(GAME_STORE_DB),
        teacher_summary=db_summary(TEACHER_STORE_DB),
        strip_report=strip,
        friend_import=friend_result,
        teacher_import={"per_source": teacher_imp.per_source, "totals": teacher_imp.totals},
        game_audit=game_audit,
        teacher_audit=teacher_audit,
        codec_parity=codec,
        sidecar_bytes=sidecar_bytes,
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"split_migration_{run}.json"
    out.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    return result
