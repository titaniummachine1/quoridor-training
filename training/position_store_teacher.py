"""Teacher / pathless position store — isolated labels without replayable games."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from position_store_config import (
    GAME_STORE_DB,
    REPORT_DIR,
    ROOT,
    TEACHER_SIDECARS,
    TEACHER_STORE_DB,
)
from position_store_lib import (
    ImportStats,
    audit_database,
    connect_db,
    db_summary,
    export_training_rows,
    import_path,
    init_db,
    semantic_checksum,
)
from position_store_state import PositionState

TEACHER_IMPORT_SOURCES: list[str] = [
    "training/data/search_pressure.jsonl",
    "training/data/zero_teacher/labels/search_budget.jsonl",
    "training/data/lmr_phase3_smoke/natural.jsonl",
]

TEACHER_STORE_KIND = "teacher"
GAME_STORE_KIND = "game"


@dataclass
class TeacherImportResult:
    migration_run_id: str
    teacher_db: str
    per_source: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)


def init_teacher_metadata(conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("store_kind", TEACHER_STORE_KIND),
    )
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("teacher_store_initialized_at", now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("games_forbidden", "true"),
    )


def init_game_metadata(conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("store_kind", GAME_STORE_KIND),
    )
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES(?, ?)",
        ("game_store_initialized_at", now),
    )


def init_teacher_store(path: Path = TEACHER_STORE_DB) -> None:
    init_db(path)
    conn = connect_db(path)
    init_teacher_metadata(conn)
    conn.commit()
    conn.close()


def init_game_store(path: Path = GAME_STORE_DB) -> None:
    init_db(path)
    conn = connect_db(path)
    init_game_metadata(conn)
    conn.commit()
    conn.close()


def audit_teacher_store(db_path: Path = TEACHER_STORE_DB) -> dict[str, Any]:
    base = audit_database(db_path)
    conn = connect_db(db_path)
    issues = list(base.get("issues") or [])
    games = int(conn.execute("SELECT COUNT(*) FROM games").fetchone()[0])
    edges = int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
    paths = int(conn.execute("SELECT COUNT(*) FROM game_paths").fetchone()[0])
    if games:
        issues.append(f"teacher_store_has_games={games}")
    if edges:
        issues.append(f"teacher_store_has_edges={edges}")
    if paths:
        issues.append(f"teacher_store_has_game_paths={paths}")
    sidecar_refs = 0
    sidecar_missing = 0
    sidecar_resolved = 0
    try:
        from teacher_dataset.sidecar_paths import resolve_sidecar_path
    except ImportError:
        resolve_sidecar_path = None  # type: ignore[assignment,misc]
    for row in conn.execute("SELECT payload_json FROM labels WHERE payload_json LIKE '%sidecar%'"):
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        ref = payload.get("sidecar_ref") or payload.get("sidecar")
        if not ref:
            continue
        sidecar_refs += 1
        if isinstance(ref, dict):
            stored = str(ref.get("sidecar") or ref.get("path") or "")
        else:
            stored = str(ref)
        if resolve_sidecar_path is not None and stored:
            if resolve_sidecar_path(stored, root=ROOT).is_file():
                sidecar_resolved += 1
                continue
        sidecar_path = ROOT / str(ref.get("sidecar", ref) if isinstance(ref, dict) else ref)
        if not sidecar_path.exists():
            sidecar_missing += 1
    conn.close()
    base["store_kind"] = TEACHER_STORE_KIND
    base["teacher_invariants"] = {
        "games": games,
        "edges": edges,
        "game_paths": paths,
        "sidecar_refs": sidecar_refs,
        "sidecar_missing": sidecar_missing,
        "sidecar_resolved_with_remap": sidecar_resolved,
    }
    base["issues"] = issues
    base["passed"] = not issues
    return base


def teacher_semantic_checksum(db_path: Path = TEACHER_STORE_DB) -> dict[str, Any]:
    """Order-independent checksums for cross-pipeline parity (not keyed by SQLite row ids)."""
    import hashlib

    conn = connect_db(db_path)
    pos_rows = conn.execute(
        "SELECT canonical_hash, packed_state, total_visits, source_flags "
        "FROM positions ORDER BY canonical_hash, packed_state"
    ).fetchall()
    label_rows = conn.execute(
        "SELECT p.canonical_hash, p.packed_state, l.source, l.value, l.best_move_u8, l.payload_json "
        "FROM labels l JOIN positions p ON p.position_id = l.position_id "
        "ORDER BY p.canonical_hash, p.packed_state, l.source, l.label_type, l.payload_json"
    ).fetchall()
    obs_rows = conn.execute(
        "SELECT p.canonical_hash, o.source_cohort, o.visit_count, o.p0_wins, o.p1_wins, o.draws "
        "FROM observations o JOIN positions p ON p.position_id = o.position_id "
        "ORDER BY p.canonical_hash, o.source_cohort"
    ).fetchall()
    import_rows = conn.execute(
        "SELECT source_hash, format, status, record_count, accepted_count, rejected_count "
        "FROM imports ORDER BY source_hash"
    ).fetchall()
    conn.close()

    def digest(rows: list, fmt: str) -> str:
        h = hashlib.sha256()
        for row in rows:
            h.update(fmt.format(*row).encode())
        return h.hexdigest()

    summary = db_summary(db_path)
    return {
        "db_path": str(db_path),
        "summary": summary,
        "position_hash": digest(
            [tuple(r) for r in pos_rows],
            "{}:{}:{}:{}\n",
        ),
        "label_hash": digest(
            [tuple(r) for r in label_rows],
            "{}:{}:{}:{}:{}:{}\n",
        ),
        "observation_hash": digest(
            [tuple(r) for r in obs_rows],
            "{}:{}:{}:{}:{}:{}\n",
        ),
        "import_hash": digest(
            [tuple(r) for r in import_rows],
            "{}:{}:{}:{}:{}:{}\n",
        ),
    }


def audit_game_store(db_path: Path = GAME_STORE_DB) -> dict[str, Any]:
    base = audit_database(db_path)
    conn = connect_db(db_path)
    issues = list(base.get("issues") or [])
    friend_labels = int(
        conn.execute("SELECT COUNT(*) FROM labels WHERE source LIKE 'friend_selfplay:%'").fetchone()[0]
    )
    if friend_labels:
        issues.append(f"game_store_has_friend_labels={friend_labels}")
    teacher_only = int(
        conn.execute(
            "SELECT COUNT(*) FROM labels WHERE label_type IN "
            "('teacher_value','search_pressure','reduction_counterfactual')"
        ).fetchone()[0]
    )
    if teacher_only:
        issues.append(f"game_store_has_pathless_teacher_labels={teacher_only}")
    conn.close()
    base["store_kind"] = GAME_STORE_KIND
    base["game_invariants"] = {"pathless_teacher_labels": teacher_only, "friend_labels": friend_labels}
    base["issues"] = issues
    base["passed"] = not issues
    return base


def strip_pathless_artifacts_from_game_store(db_path: Path = GAME_STORE_DB) -> dict[str, int]:
    """Remove pathless teacher labels/observations and orphan positions."""
    conn = connect_db(db_path)
    before_labels = int(conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0])
    before_obs = int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
    before_pos = int(conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0])
    conn.execute(
        "DELETE FROM labels WHERE label_type IN ('teacher_value','search_pressure','reduction_counterfactual')"
    )
    conn.execute("DELETE FROM relabel_queue")
    conn.execute(
        "DELETE FROM observations WHERE source_cohort LIKE 'search_pressure:%' "
        "OR source_cohort LIKE 'zero_teacher:%' "
        "OR source_cohort LIKE 'friend_selfplay:%' "
        "OR source_cohort LIKE 'lmr:%' "
        "OR source_cohort = 'reduction-counterfactual'"
    )
    conn.execute(
        "DELETE FROM positions WHERE position_id NOT IN ("
        "SELECT start_position_id FROM games "
        "UNION SELECT parent_position_id FROM edges "
        "UNION SELECT child_position_id FROM edges)"
    )
    after_labels = int(conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0])
    after_obs = int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
    after_pos = int(conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0])
    conn.commit()
    conn.close()
    return {
        "labels_removed": before_labels - after_labels,
        "observations_removed": before_obs - after_obs,
        "positions_removed": before_pos - after_pos,
        "labels_remaining": after_labels,
        "observations_remaining": after_obs,
        "positions_remaining": after_pos,
    }


def verify_codec_parity(game_db: Path = GAME_STORE_DB, teacher_db: Path = TEACHER_STORE_DB) -> dict[str, Any]:
    if not game_db.exists() or not teacher_db.exists():
        return {"passed": False, "error": "missing database"}
    gconn = connect_db(game_db)
    tconn = connect_db(teacher_db)
    g_rows = gconn.execute("SELECT canonical_hash, packed_state FROM positions LIMIT 500").fetchall()
    t_rows = tconn.execute("SELECT canonical_hash, packed_state FROM positions LIMIT 500").fetchall()
    gconn.close()
    tconn.close()
    decode_failures = 0
    for row in g_rows + t_rows:
        try:
            state = PositionState.unpack_state(row["packed_state"])
            if state.canonical_hash() != row["canonical_hash"]:
                decode_failures += 1
        except Exception:
            decode_failures += 1
    g_set = {(r["canonical_hash"], r["packed_state"]) for r in g_rows}
    t_set = {(r["canonical_hash"], r["packed_state"]) for r in t_rows}
    return {
        "passed": decode_failures == 0,
        "decode_failures": decode_failures,
        "game_sample": len(g_rows),
        "teacher_sample": len(t_rows),
        "overlapping_identities_in_sample": len(g_set & t_set),
    }


def resolve_teacher_import_paths() -> list[Path]:
    paths: list[Path] = []
    for rel in TEACHER_IMPORT_SOURCES:
        p = ROOT / rel
        if p.exists() and p.stat().st_size > 0:
            paths.append(p)
    return paths


def import_teacher_sources(
    db_path: Path = TEACHER_STORE_DB,
    *,
    dry_run: bool = False,
    run_id: str | None = None,
) -> TeacherImportResult:
    run = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    init_teacher_store(db_path)
    per_source: list[dict[str, Any]] = []
    for source in resolve_teacher_import_paths():
        rel = str(source.relative_to(ROOT)).replace("\\", "/")
        try:
            stats = import_path(db_path, source, dry_run=dry_run, report_dir=REPORT_DIR, teacher_import=True)
            unaccounted = stats.record_count - stats.accepted_count - stats.rejected_count - stats.quarantined_count
            per_source.append({**stats.__dict__, "source": rel, "unaccounted": unaccounted})
            if unaccounted != 0:
                raise RuntimeError(f"teacher import unaccounted for {rel}: {unaccounted}")
        except FileExistsError:
            per_source.append({"source": rel, "status": "already_imported", "unaccounted": 0})
    totals: dict[str, Any] = {
        "sources": len(per_source),
        "unaccounted": sum(r.get("unaccounted", 0) for r in per_source),
    }
    audit = audit_teacher_store(db_path) if not dry_run and db_path.exists() else {}
    return TeacherImportResult(
        migration_run_id=run,
        teacher_db=str(db_path),
        per_source=per_source,
        totals=totals,
        audit=audit,
    )


def prove_teacher_idempotence(db_path: Path = TEACHER_STORE_DB) -> dict[str, Any]:
    before = db_summary(db_path)
    before_checksum = semantic_checksum(db_path)
    errors: list[str] = []
    noop: list[str] = []
    for source in resolve_teacher_import_paths():
        rel = str(source.relative_to(ROOT)).replace("\\", "/")
        try:
            import_path(db_path, source, dry_run=False, report_dir=REPORT_DIR, teacher_import=True)
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
        "noop_sources": noop,
        "errors": errors,
        "passed": before == after and before_checksum == after_checksum and not errors,
    }


def export_teacher_training(
    out_path: Path,
    *,
    db_path: Path = TEACHER_STORE_DB,
    label_type: str = "teacher_value",
    limit: int | None = None,
) -> int:
    return export_training_rows(db_path, out_path=out_path, label_type=label_type, limit=limit)


def export_mixed_training(
    out_path: Path,
    *,
    game_db: Path = GAME_STORE_DB,
    teacher_db: Path = TEACHER_STORE_DB,
    label_type: str = "teacher_value",
    limit: int | None = None,
) -> dict[str, Any]:
    game_tmp = out_path.with_suffix(".game.jsonl")
    teacher_tmp = out_path.with_suffix(".teacher.jsonl")
    game_rows = export_training_rows(game_db, out_path=game_tmp, label_type=label_type, limit=limit)
    teacher_rows = export_training_rows(teacher_db, out_path=teacher_tmp, label_type=label_type, limit=limit)
    seen: set[tuple[str, str]] = set()
    written = 0
    deduped = 0
    with out_path.open("w", encoding="utf-8") as out_handle:
        for src in (game_tmp, teacher_tmp):
            if not src.exists():
                continue
            with src.open("r", encoding="utf-8") as handle:
                for line in handle:
                    obj = json.loads(line)
                    key = (obj.get("canonical_hash_hex", ""), obj.get("packed_state_hex", ""))
                    if key in seen:
                        deduped += 1
                        continue
                    seen.add(key)
                    out_handle.write(line)
                    written += 1
    return {
        "out": str(out_path),
        "game_rows": game_rows,
        "teacher_rows": teacher_rows,
        "written": written,
        "deduped": deduped,
    }
