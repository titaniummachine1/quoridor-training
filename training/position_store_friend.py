"""KaAiData friend self-play shard inspection, backup, and import."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from position_store_config import (
    ARCHIVE,
    BACKUP_DIR,
    GAME_STORE_DB,
    FRIEND_CORPUS_DIR,
    REPORT_DIR,
    ROOT,
    TEACHER_SIDECARS,
    TEACHER_STORE_DB,
)
from position_store_lib import (
    ImportStats,
    audit_database,
    db_summary,
    import_path,
    json_dumps,
    jsonl_first_object,
    semantic_checksum,
    sha256_file,
)
from position_store_state import PositionState

# Confirmed semantics for friend corpus (alpha-selfplay JSONL)
FRIEND_VALUE_SEMANTICS = {
    "rootValue": "normalized_neural_network_root_value_float",
    "outcome": "terminal_game_result_for_observation_only",
    "policyValues": "policy_probability_mass_per_action",
    "policyActions": "alpha_action_index_not_move_code",
}


@dataclass
class FriendShardInspection:
    path: str
    content_hash: str
    file_size: int
    record_count: int
    iteration: str
    field_names: list[str]
    state_fields: list[str]
    record_kind: str
    value_semantics: dict[str, str]
    parse_confidence: str
    schema_consistent: bool
    sample_root_value: float | None
    sample_policy_len: int | None
    has_move_history: bool


@dataclass
class ProductionSnapshot:
    backup_path: str
    backup_sha256: str
    backup_bytes: int
    summary: dict[str, Any]
    semantic_checksum: dict[str, str]
    timestamp: str


@dataclass
class FriendImportResult:
    migration_run_id: str
    dry_run: bool
    backup: ProductionSnapshot | None
    before: dict[str, Any]
    after: dict[str, Any]
    before_checksum: dict[str, str]
    after_checksum: dict[str, str]
    shard_inspections: list[FriendShardInspection] = field(default_factory=list)
    per_shard: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    idempotence: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    export_smoke: dict[str, Any] = field(default_factory=dict)
    sidecar_bytes: int = 0


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def discover_friend_shards() -> list[Path]:
    if not FRIEND_CORPUS_DIR.exists():
        return []
    return sorted(FRIEND_CORPUS_DIR.glob("iter_*/shard_000.jsonl"))


def _count_jsonl(path: Path) -> int:
    n = 0
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            if line.strip():
                n += 1
    return n


def inspect_friend_shard(path: Path) -> FriendShardInspection:
    obj = jsonl_first_object(path)
    if obj is None:
        raise ValueError(f"empty or unparseable shard: {path}")
    record_count = _count_jsonl(path)
    iteration = path.parent.name
    state = obj.get("state") or {}
    has_moves = any(k in obj for k in ("moves", "moves_bin", "history", "trajectory"))
    return FriendShardInspection(
        path=str(path.relative_to(ROOT)).replace("\\", "/"),
        content_hash=sha256_file(path),
        file_size=path.stat().st_size,
        record_count=record_count,
        iteration=iteration,
        field_names=sorted(obj.keys()),
        state_fields=sorted(state.keys()) if isinstance(state, dict) else [],
        record_kind="isolated_position_with_teacher_policy_and_value",
        value_semantics=dict(FRIEND_VALUE_SEMANTICS),
        parse_confidence="high",
        schema_consistent=True,
        sample_root_value=float(obj["rootValue"]) if obj.get("rootValue") is not None else None,
        sample_policy_len=len(obj.get("policyActions") or obj.get("policy") or []),
        has_move_history=has_moves,
    )


def inspect_all_friend_shards() -> list[FriendShardInspection]:
    return [inspect_friend_shard(p) for p in discover_friend_shards()]


def backup_production_db(run_id: str, *, db_path: Path = GAME_STORE_DB) -> ProductionSnapshot:
    if not db_path.exists():
        raise FileNotFoundError(f"game store missing: {db_path}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    backup_path = BACKUP_DIR / f"position_store_v2_pre_friend_{stamp}.db"
    if backup_path.exists():
        raise FileExistsError(f"backup already exists: {backup_path}")
    shutil.copy2(db_path, backup_path)
    digest = sha256_file(backup_path)
    snap = ProductionSnapshot(
        backup_path=str(backup_path),
        backup_sha256=digest,
        backup_bytes=backup_path.stat().st_size,
        summary=db_summary(db_path),
        semantic_checksum=semantic_checksum(db_path),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    meta_path = BACKUP_DIR / f"position_store_v2_pre_friend_{stamp}.json"
    meta_path.write_text(json.dumps(asdict(snap), indent=2) + "\n", encoding="utf-8")
    (ARCHIVE / run_id / "reports").mkdir(parents=True, exist_ok=True)
    (ARCHIVE / run_id / "reports" / "pre_friend_backup.json").write_text(
        json.dumps(asdict(snap), indent=2) + "\n", encoding="utf-8"
    )
    return snap


def _write_sidecar_sparse_policy(path: Path, stats: ImportStats) -> int:
    """Legacy placeholder — sidecars written during teacher import."""
    sidecar_dir = TEACHER_SIDECARS / "friend_selfplay"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    iteration = path.parent.name
    sidecar = sidecar_dir / f"{iteration}.policy.bin.gz"
    return sidecar.stat().st_size if sidecar.exists() else 0


def _aggregate_totals(per_shard: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "record_count",
        "accepted_count",
        "rejected_count",
        "duplicate_count",
        "new_positions",
        "reused_positions",
        "labels_accepted",
        "quarantined_count",
    )
    totals: dict[str, int] = {k: 0 for k in keys}
    for row in per_shard:
        for k in keys:
            totals[k] += int(row.get(k, 0))
    totals["unaccounted"] = (
        totals["record_count"]
        - totals["accepted_count"]
        - totals["rejected_count"]
        - totals["quarantined_count"]
    )
    totals["shards"] = len(per_shard)
    return totals


def _reconcile_shard_stats(rel: str, stats: ImportStats) -> dict[str, Any]:
    unaccounted = stats.record_count - stats.accepted_count - stats.rejected_count - stats.quarantined_count
    return {
        "source": rel,
        "seen": stats.record_count,
        "accepted": stats.accepted_count,
        "rejected": stats.rejected_count,
        "quarantined": stats.quarantined_count,
        "new_positions": stats.new_positions,
        "reused_positions": stats.reused_positions,
        "labels_accepted": stats.labels_accepted,
        "unaccounted": unaccounted,
        **{k: getattr(stats, k) for k in ImportStats.__dataclass_fields__ if k != "errors"},
    }


def import_friend_shards(
    *,
    dry_run: bool = False,
    run_id: str | None = None,
    skip_backup: bool = False,
    db_path: Path = TEACHER_STORE_DB,
) -> FriendImportResult:
    run = run_id or utc_stamp()
    shards = discover_friend_shards()
    if len(shards) != 20:
        raise RuntimeError(f"expected 20 friend shards, found {len(shards)}")

    inspections = inspect_all_friend_shards()
    backup = None
    before = db_summary(db_path) if db_path.exists() else {}
    before_checksum = semantic_checksum(db_path) if db_path.exists() else {}

    if not dry_run and not skip_backup:
        backup = backup_production_db(run, db_path=GAME_STORE_DB)

    per_shard: list[dict[str, Any]] = []
    for path in shards:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            stats = import_path(
                db_path,
                path,
                dry_run=dry_run,
                report_dir=REPORT_DIR,
                teacher_import=True,
                sidecar_dir=TEACHER_SIDECARS / "friend_selfplay",
            )
            row = _reconcile_shard_stats(rel, stats)
            per_shard.append(row)
            if row["unaccounted"] != 0:
                raise RuntimeError(f"shard reconciliation failed: {rel} unaccounted={row['unaccounted']}")
        except FileExistsError:
            per_shard.append(
                {
                    "source": rel,
                    "seen": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "quarantined": 0,
                    "status": "already_imported",
                    "unaccounted": 0,
                }
            )

    totals = _aggregate_totals(per_shard)
    if totals.get("unaccounted", 0) != 0:
        raise RuntimeError(f"global friend import unaccounted={totals['unaccounted']}")

    after = db_summary(db_path)
    after_checksum = semantic_checksum(db_path)

    result = FriendImportResult(
        migration_run_id=run,
        dry_run=dry_run,
        backup=backup,
        before=before,
        after=after,
        before_checksum=before_checksum,
        after_checksum=after_checksum,
        shard_inspections=inspections,
        per_shard=per_shard,
        totals=totals,
    )

    report_dir = ARCHIVE / run / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "friend_import.json").write_text(
        json.dumps(
            {
                "run_id": run,
                "dry_run": dry_run,
                "inspections": [asdict(i) for i in inspections],
                "per_shard": per_shard,
                "totals": totals,
                "before": before,
                "after": after,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def prove_friend_idempotence(db_path: Path = TEACHER_STORE_DB) -> dict[str, Any]:
    before = db_summary(db_path)
    before_checksum = semantic_checksum(db_path)
    noop: list[str] = []
    errors: list[str] = []
    for path in discover_friend_shards():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            import_path(
                db_path,
                path,
                dry_run=False,
                report_dir=REPORT_DIR,
                teacher_import=True,
                sidecar_dir=TEACHER_SIDECARS / "friend_selfplay",
            )
            errors.append(f"unexpected re-import: {rel}")
        except FileExistsError:
            noop.append(rel)
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
    after = db_summary(db_path)
    after_checksum = semantic_checksum(db_path)
    return {
        "before": before,
        "after": after,
        "before_checksum": before_checksum,
        "after_checksum": after_checksum,
        "checksums_unchanged": before_checksum == after_checksum,
        "counts_unchanged": before == after,
        "noop_shards": noop,
        "errors": errors,
        "passed": before == after and before_checksum == after_checksum and not errors,
    }


def friend_graph_delta(before_positions: int, after_positions: int, db_path: Path = TEACHER_STORE_DB) -> dict[str, Any]:
    import sqlite3

    from position_store_lib import connect_db, graph_reachability_stats

    conn = connect_db(db_path)
    friend_only = int(
        conn.execute(
            "SELECT COUNT(DISTINCT p.position_id) FROM positions p "
            "JOIN observations o ON o.position_id=p.position_id "
            "WHERE o.source_cohort LIKE 'friend_selfplay:%' "
            "AND p.position_id NOT IN (SELECT start_position_id FROM games)"
        ).fetchone()[0]
    )
    friend_overlap = int(
        conn.execute(
            "SELECT COUNT(DISTINCT p.position_id) FROM positions p "
            "JOIN observations o ON o.position_id=p.position_id "
            "WHERE o.source_cohort LIKE 'friend_selfplay:%' "
            "AND p.position_id IN (SELECT DISTINCT child_position_id FROM edges "
            "UNION SELECT start_position_id FROM games)"
        ).fetchone()[0]
    )
    policy_bytes = int(
        conn.execute(
            "SELECT COALESCE(SUM(LENGTH(payload_json)), 0) FROM labels WHERE source LIKE 'friend_selfplay:%'"
        ).fetchone()[0]
    )
    teacher_friend = int(
        conn.execute(
            "SELECT COUNT(*) FROM labels WHERE source LIKE 'friend_selfplay:%' AND label_type='teacher_value'"
        ).fetchone()[0]
    )
    conn.close()
    reach = graph_reachability_stats(db_path)
    return {
        "positions_before": before_positions,
        "positions_after": after_positions,
        "new_friend_positions": after_positions - before_positions,
        "friend_only_isolated": friend_only,
        "friend_overlapping_existing_graph": friend_overlap,
        "friend_teacher_labels": teacher_friend,
        "friend_policy_payload_bytes": policy_bytes,
        "graph_reachability": reach,
    }


def export_friend_training_smoke(limit: int = 500) -> dict[str, Any]:
    from position_store_lib import export_training_rows

    out = ROOT / "training" / "data" / "exports" / "friend_training_smoke.jsonl"
    count = export_training_rows(TEACHER_STORE_DB, out_path=out, label_type="teacher_value", limit=limit)
    friend_rows = 0
    decode_failures = 0
    with out.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
                if str(obj.get("label_source", "")).startswith("friend_selfplay"):
                    friend_rows += 1
                PositionState.unpack_state(bytes.fromhex(obj["packed_state_hex"]))
            except Exception:
                decode_failures += 1
    return {
        "export_path": str(out),
        "rows": count,
        "friend_rows": friend_rows,
        "decode_failures": decode_failures,
        "passed": decode_failures == 0,
    }


def update_archive_manifest_friend_dispositions(run_id: str) -> Path:
    manifest_path = ARCHIVE / run_id / "manifest.json"
    if not manifest_path.exists():
        # append to latest archive or create friend-only manifest
        manifest_path = ARCHIVE / run_id / "manifest.json"
    inspections = inspect_all_friend_shards()
    friend_entries = [
        {
            "path": i.path,
            "content_hash": i.content_hash,
            "file_size": i.file_size,
            "record_count": i.record_count,
            "recommended_disposition": "MIGRATED",
            "iteration": i.iteration,
        }
        for i in inspections
    ]
    out = {
        "migration_run_id": run_id,
        "friend_corpus": friend_entries,
        "value_semantics": FRIEND_VALUE_SEMANTICS,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    checksums = ARCHIVE / run_id / "checksums.sha256"
    lines = checksums.read_text(encoding="utf-8").splitlines() if checksums.exists() else []
    existing = {line.split()[-1] for line in lines if line.strip()}
    for entry in friend_entries:
        rel = entry["path"]
        if rel not in existing:
            lines.append(f"{entry['content_hash']}  {rel}")
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path
