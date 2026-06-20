"""Rebuild policy payloads from authoritative friend JSONL shards."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from position_store_config import FRIEND_CORPUS_DIR, ROOT
from position_store_friend import discover_friend_shards
from position_store_lib import _alpha_action_to_move_u8, policy_semantic_hash
from position_store_state import PositionState

from .sidecar_reader import SidecarRecord


def _parse_friend_line(obj: dict[str, Any], cohort: str) -> tuple[bytes, str, SidecarRecord] | None:
    try:
        state_obj = obj["state"]
        state = PositionState(
            player0_cell=int(state_obj["player0Cell"]),
            player1_cell=int(state_obj["player1Cell"]),
            side_to_move=int(state_obj["sideToMove"]),
            walls=tuple(int(x) for x in state_obj["walls"]),
        )
        actions = obj.get("policyActions") or obj.get("policy_actions") or []
        values = obj.get("policyValues") or obj.get("policy_values") or []
        if not actions or not values:
            return None
        move_codes = [_alpha_action_to_move_u8(state, int(a)) for a in actions]
        canonical = state.canonical_hash()
        ph = policy_semantic_hash(move_codes, [float(v) for v in values])
        u16 = tuple(min(65535, max(0, int(round(float(v) * 65535)))) for v in values)
        rec = SidecarRecord(canonical, tuple(move_codes), u16)
        return canonical, ph, rec
    except (KeyError, TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def build_jsonl_policy_index(corpus_dir: Path = FRIEND_CORPUS_DIR) -> dict[tuple[bytes, str], SidecarRecord]:
    """Index (canonical_hash, policy_hash) -> policy record from all friend shards."""
    index: dict[tuple[bytes, str], SidecarRecord] = {}
    for shard_path in discover_friend_shards():
        iteration = shard_path.parent.name
        cohort = f"friend_selfplay:{iteration}"
        with shard_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                parsed = _parse_friend_line(json.loads(line), cohort)
                if parsed is None:
                    continue
                canonical, ph, rec = parsed
                index[(canonical, ph)] = rec
    return index


def recover_policy_from_jsonl(
    *,
    canonical_hash: bytes,
    policy_hash: str | None,
    corpus_dir: Path = FRIEND_CORPUS_DIR,
) -> SidecarRecord | None:
    index = build_jsonl_policy_index(corpus_dir)
    if policy_hash:
        return index.get((canonical_hash, policy_hash))
    matches = [rec for (c, _h), rec in index.items() if c == canonical_hash]
    return matches[0] if len(matches) == 1 else None
