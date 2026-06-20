"""Recover TIQSIDE1 policies by scanning decompressed sidecars (DB offsets are compressed-file positions)."""
from __future__ import annotations

import gzip
import json
import zlib
from functools import lru_cache
from pathlib import Path

from titanium_training.store.lib import policy_semantic_hash

from .sidecar_paths import resolve_sidecar_path
from .sidecar_reader import SidecarRecord, iter_sidecar_records


def preload_sidecar_indexes(sidecar_dir: Path) -> None:
    """Warm LRU cache for all friend iteration sidecars."""
    friend_dir = sidecar_dir / "friend_selfplay"
    if not friend_dir.is_dir():
        return
    for path in sorted(friend_dir.glob("iter_*.policy.bin.gz")):
        _load_file_index(path)


@lru_cache(maxsize=32)
def _load_file_index(path: Path) -> dict[bytes, list[SidecarRecord]]:
    index: dict[bytes, list[SidecarRecord]] = {}
    try:
        for _off, rec in iter_sidecar_records(path):
            index.setdefault(rec.canonical_hash, []).append(rec)
    except (OSError, ValueError, EOFError, gzip.BadGzipFile, zlib.error):
        return index
    return index


def recover_policy_record(
    stored_path: str,
    *,
    canonical_hash: bytes,
    packed_state: bytes | None = None,
    policy_hash: str | None,
    root: Path,
) -> SidecarRecord | None:
    path = resolve_sidecar_path(stored_path, root=root)
    if path.is_file():
        try:
            candidates = _load_file_index(path).get(canonical_hash, [])
            if candidates:
                if len(candidates) == 1:
                    return candidates[0]
                if policy_hash:
                    for rec in candidates:
                        h = policy_semantic_hash(list(rec.move_codes), list(rec.policy_values))
                        if h == policy_hash:
                            return rec
                return candidates[0]
        except (OSError, ValueError, gzip.BadGzipFile, zlib.error):
            pass

    if packed_state is not None and policy_hash:
        from .jsonl_policy_index import recover_policy_from_jsonl_packed

        return recover_policy_from_jsonl_packed(packed_state=packed_state, policy_hash=str(policy_hash))
    return None
