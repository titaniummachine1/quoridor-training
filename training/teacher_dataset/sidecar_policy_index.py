"""Index policy payloads scanned from TIQSIDE1 sidecar files (matches Rust importer canonical hashes)."""
from __future__ import annotations

import gzip
import zlib
from functools import lru_cache
from pathlib import Path

from position_store_config import TEACHER_SIDECARS
from position_store_lib import policy_semantic_hash

from .sidecar_reader import SidecarRecord, iter_sidecar_records


@lru_cache(maxsize=1)
def build_sidecar_policy_index(
    sidecar_dir: Path = TEACHER_SIDECARS,
) -> tuple[dict[tuple[bytes, str], SidecarRecord], list[str]]:
    """Return (canonical_hash, policy_hash) index from TIQSIDE1 sidecar scan."""
    by_key: dict[tuple[bytes, str], SidecarRecord] = {}
    friend_dir = sidecar_dir / "friend_selfplay"
    skipped: list[str] = []
    if not friend_dir.is_dir():
        return by_key, skipped
    for path in sorted(friend_dir.glob("iter_*.policy.bin.gz")):
        try:
            for _off, rec in iter_sidecar_records(path):
                ph = policy_semantic_hash(list(rec.move_codes), list(rec.policy_values))
                by_key[(rec.canonical_hash, ph)] = rec
        except (OSError, ValueError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
            skipped.append(f"{path.name}:{exc}")
            continue
    return by_key, skipped


def lookup_policy(
    *,
    canonical_hash: bytes,
    policy_hash: str,
    sidecar_dir: Path = TEACHER_SIDECARS,
) -> SidecarRecord | None:
    by_key, _skipped = build_sidecar_policy_index(sidecar_dir)
    return by_key.get((canonical_hash, policy_hash))
