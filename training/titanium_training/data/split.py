"""Deterministic train/validation splits for teacher-value training."""
from __future__ import annotations

import hashlib
from typing import Any, Callable, TypeVar

T = TypeVar("T")

SPLIT_ALGORITHM = "blake2b_position_key_mod_fraction"


class ValidationSplitError(ValueError):
    """Raised when validation is enabled but cannot produce a valid split."""


def _split_bucket(key: bytes, seed: int) -> float:
    h = hashlib.blake2b(key + seed.to_bytes(8, "little"), digest_size=8).digest()
    return int.from_bytes(h, "little") / float(2**64)


def deterministic_train_val_split(
    records: list[T],
    *,
    val_fraction: float,
    seed: int,
    min_val: int = 1,
    min_train: int = 1,
    key_fn: Callable[[T], bytes] | None = None,
) -> tuple[list[T], list[T], dict[str, Any]]:
    """Hash-stable split; guarantees non-empty train and val when val_fraction > 0."""
    if val_fraction <= 0:
        if min_train > 0 and len(records) < min_train:
            raise ValidationSplitError(
                f"need >={min_train} training records, got {len(records)}"
            )
        return records, [], {
            "split_algorithm": SPLIT_ALGORITHM,
            "split_seed": seed,
            "train_count": len(records),
            "validation_count": 0,
            "grouping_key": "position_key",
        }

    if len(records) < min_train + min_val:
        raise ValidationSplitError(
            f"need >={min_train + min_val} records for val_fraction={val_fraction}, got {len(records)}"
        )

    def key_of(rec: T) -> bytes:
        if key_fn is not None:
            return key_fn(rec)
        if isinstance(rec, dict):
            raw = rec.get("_position_key") or rec.get("position_key") or rec.get("_src", "")
            if isinstance(raw, bytes):
                return raw
            return str(raw).encode()
        return str(rec).encode()

    indexed = [( _split_bucket(key_of(r), seed) < val_fraction, r) for r in records]
    val = [r for is_val, r in indexed if is_val]
    train = [r for is_val, r in indexed if not is_val]

    # Deterministic rebalance: move highest-bucket train rows into val if needed.
    if len(val) < min_val:
        need = min_val - len(val)
        train_sorted = sorted(
            train,
            key=lambda r: _split_bucket(key_of(r), seed ^ 0xA5A5),
            reverse=True,
        )
        val.extend(train_sorted[:need])
        moved = set(id(r) for r in train_sorted[:need])
        train = [r for r in train if id(r) not in moved]

    if len(train) < min_train:
        need = min_train - len(train)
        val_sorted = sorted(
            val,
            key=lambda r: _split_bucket(key_of(r), seed ^ 0x5A5A),
        )
        train.extend(val_sorted[:need])
        moved = set(id(r) for r in val_sorted[:need])
        val = [r for r in val if id(r) not in moved]

    if len(val) < min_val or len(train) < min_train:
        raise ValidationSplitError(
            f"split failed: train={len(train)} val={len(val)} "
            f"(need train>={min_train} val>={min_val})"
        )

    return train, val, {
        "split_algorithm": SPLIT_ALGORITHM,
        "split_seed": seed,
        "train_count": len(train),
        "validation_count": len(val),
        "grouping_key": "position_key",
        "val_fraction_requested": val_fraction,
    }
