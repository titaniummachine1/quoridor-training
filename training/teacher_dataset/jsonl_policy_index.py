"""Rebuild policy payloads from authoritative friend JSONL shards."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from titanium_training.store.config import FRIEND_CORPUS_DIR
from titanium_training.store.friend import discover_friend_shards
from titanium_training.store.lib import _alpha_action_to_move_u8, policy_semantic_hash
from .friend_state import parse_friend_state
from .sidecar_reader import SidecarRecord


def _parse_friend_line(obj: dict[str, Any]) -> tuple[bytes, bytes, str, SidecarRecord] | None:
    try:
        state = parse_friend_state(obj)
        actions = obj.get("policyActions") or obj.get("policy_actions") or []
        values = obj.get("policyValues") or obj.get("policy_values") or []
        if not actions or not values:
            return None
        move_codes = [_alpha_action_to_move_u8(state, int(a)) for a in actions]
        packed = state.packed_state()
        canonical = state.canonical_hash()
        ph = policy_semantic_hash(move_codes, [float(v) for v in values])
        u16 = tuple(min(65535, max(0, int(round(float(v) * 65535)))) for v in values)
        rec = SidecarRecord(canonical, tuple(move_codes), u16)
        return packed, canonical, ph, rec
    except (KeyError, TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def build_jsonl_policy_index(
    corpus_dir: Path = FRIEND_CORPUS_DIR,
) -> tuple[dict[tuple[bytes, str], SidecarRecord], dict[tuple[bytes, str], SidecarRecord]]:
    """Return (canonical,ph) and (packed_state,ph) indexes — lookup must use packed identity."""
    by_canonical: dict[tuple[bytes, str], SidecarRecord] = {}
    by_packed: dict[tuple[bytes, str], SidecarRecord] = {}
    for shard_path in discover_friend_shards():
        with shard_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                parsed = _parse_friend_line(json.loads(line))
                if parsed is None:
                    continue
                packed, canonical, ph, rec = parsed
                by_canonical[(canonical, ph)] = rec
                by_packed[(packed, ph)] = rec
    return by_canonical, by_packed


def recover_policy_from_jsonl_packed(
    *,
    packed_state: bytes,
    policy_hash: str,
) -> SidecarRecord | None:
    _by_canonical, by_packed = build_jsonl_policy_index()
    return by_packed.get((packed_state, policy_hash))
