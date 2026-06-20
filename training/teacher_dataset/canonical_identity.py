"""Frozen cross-language canonical position identity specification."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from position_store_state import POSITION_SCHEMA_VERSION

CANONICAL_IDENTITY_SPEC_VERSION = 1
CANONICAL_HASH_ALGORITHM = "sha256"
CANONICAL_HASH_WIDTH_BYTES = 32
FAST_HASH_ALGORITHM = "blake2b"
FAST_HASH_WIDTH_BYTES = 8
PACKED_STATE_WIDTH_BYTES = 24

# Input bytes: POSITION_SCHEMA_VERSION (u8) + 7-byte head + le_u64 horizontal + le_u64 vertical
# See position_store_state.py / position_state.rs — bytes [6:8] reserved zero.


@dataclass(frozen=True)
class CanonicalIdentitySpec:
    spec_version: int = CANONICAL_IDENTITY_SPEC_VERSION
    position_schema_version: int = POSITION_SCHEMA_VERSION
    packed_state_width: int = PACKED_STATE_WIDTH_BYTES
    hash_algorithm: str = CANONICAL_HASH_ALGORITHM
    hash_width_bytes: int = CANONICAL_HASH_WIDTH_BYTES
    hash_input: str = "packed_state_bytes_only"
    notes: str = (
        "Canonical hash is SHA-256 over the 24-byte little-endian packed state. "
        "No struct padding, no hex strings, no JSON. Hash is an index accelerator; "
        "packed-state byte equality is the source of truth."
    )


def canonical_hash_from_packed(packed_state: bytes) -> bytes:
    if len(packed_state) != PACKED_STATE_WIDTH_BYTES:
        raise ValueError(f"packed state must be {PACKED_STATE_WIDTH_BYTES} bytes")
    return hashlib.sha256(packed_state).digest()


def verify_stored_canonical(packed_state: bytes, stored_canonical: bytes) -> bool:
    return canonical_hash_from_packed(packed_state) == stored_canonical


def spec_document() -> dict:
    s = CanonicalIdentitySpec()
    return {
        "CANONICAL_IDENTITY_SPEC_VERSION": s.spec_version,
        "POSITION_SCHEMA_VERSION": s.position_schema_version,
        "PACKED_STATE_WIDTH_BYTES": s.packed_state_width,
        "CANONICAL_HASH_ALGORITHM": s.hash_algorithm,
        "CANONICAL_HASH_WIDTH_BYTES": s.hash_width_bytes,
        "CANONICAL_HASH_INPUT": s.hash_input,
        "FAST_HASH_ALGORITHM": FAST_HASH_ALGORITHM,
        "FAST_HASH_WIDTH_BYTES": FAST_HASH_WIDTH_BYTES,
        "notes": s.notes,
    }
