"""Unified policy lookup — packed-state identity is source of truth."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from position_store_config import ROOT, TEACHER_SIDECARS

from .jsonl_policy_index import build_jsonl_policy_index
from .policy_recovery import recover_policy_record
from .sidecar_policy_index import build_sidecar_policy_index
from .sidecar_reader import SidecarRecord


@dataclass
class PolicyLookupStats:
    from_sidecar_index: int = 0
    from_jsonl_packed: int = 0
    from_sidecar_recovery: int = 0
    no_policy: int = 0
    unresolved: int = 0
    quarantined: list[dict] = field(default_factory=list)


def lookup_teacher_policy(
    *,
    canonical_hash: bytes,
    packed_state: bytes,
    policy_hash: str | None,
    sidecar_ref: dict | None,
    source: str,
    label_id: int,
    sidecar_index: dict[tuple[bytes, str], SidecarRecord] | None = None,
    jsonl_by_packed: dict[tuple[bytes, str], SidecarRecord] | None = None,
    root: Path = ROOT,
    stats: PolicyLookupStats | None = None,
) -> SidecarRecord | None:
    """Lookup order: sidecar index → JSONL packed match → per-ref recovery → None."""
    if not policy_hash or not str(source).startswith("friend_selfplay:"):
        if stats:
            stats.no_policy += 1
        return None

    ph = str(policy_hash)
    if sidecar_index is None:
        sidecar_index, _skipped = build_sidecar_policy_index(TEACHER_SIDECARS)
    if jsonl_by_packed is None:
        _jsonl_canonical, jsonl_by_packed = build_jsonl_policy_index()

    key = (canonical_hash, ph)
    record = sidecar_index.get(key)
    if record is not None and record.canonical_hash == canonical_hash:
        if stats:
            stats.from_sidecar_index += 1
        return record

    packed_key = (packed_state, ph)
    record = jsonl_by_packed.get(packed_key)
    if record is not None:
        if stats:
            stats.from_jsonl_packed += 1
        return record

    if sidecar_ref and sidecar_ref.get("sidecar"):
        record = recover_policy_record(
            str(sidecar_ref["sidecar"]),
            canonical_hash=canonical_hash,
            packed_state=packed_state,
            policy_hash=ph,
            root=root,
        )
        if record is not None and record.canonical_hash == canonical_hash:
            if stats:
                stats.from_sidecar_recovery += 1
            return record

    if stats:
        stats.unresolved += 1
        if len(stats.quarantined) < 100:
            stats.quarantined.append(
                {
                    "label_id": label_id,
                    "source": source,
                    "policy_hash": ph,
                    "canonical_prefix": canonical_hash[:8].hex(),
                }
            )
    return None
