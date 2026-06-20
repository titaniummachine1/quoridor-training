"""Compact sparse policy encoding for immutable teacher_dataset sidecars."""
from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass

from .schema import POLICY_CHUNK_MAGIC, POLICY_INDEX_MAGIC, POLICY_SIDECAR_SCHEMA_VERSION


@dataclass(frozen=True)
class EncodedPolicy:
    move_codes: tuple[int, ...]
    values_u16: tuple[int, ...]
    content_hash: bytes

    @classmethod
    def from_sparse(cls, move_codes: list[int] | tuple[int, ...], values: list[float] | tuple[float, ...]) -> EncodedPolicy:
        if len(move_codes) != len(values):
            raise ValueError("move_codes/values length mismatch")
        u16 = tuple(min(65535, max(0, int(round(float(v) * 65535)))) for v in values)
        payload = struct.pack("<BB", len(move_codes) & 0xFF, 1)  # count, u16 encoding
        for mv, q in zip(move_codes, u16):
            payload += struct.pack("<BH", mv & 0xFF, q)
        content_hash = hashlib.blake2b(payload, digest_size=32).digest()
        return cls(tuple(int(m) & 0xFF for m in move_codes), u16, content_hash)

    def to_bytes(self) -> bytes:
        header = struct.pack("<BB", len(self.move_codes) & 0xFF, 1)
        body = b"".join(struct.pack("<BH", mv, q) for mv, q in zip(self.move_codes, self.values_u16))
        return header + body


@dataclass
class PolicyChunkWriter:
    chunk_id: int
    records: list[tuple[int, bytes, bytes]]  # policy_record_id placeholder, encoded, content_hash

    def add(self, encoded: EncodedPolicy) -> int:
        rid = len(self.records)
        self.records.append((rid, encoded.to_bytes(), encoded.content_hash))
        return rid

    def finalize(self) -> tuple[bytes, bytes]:
        """Return (bin_bytes, idx_bytes) ready for atomic rename."""
        bin_parts = [
            POLICY_CHUNK_MAGIC,
            struct.pack("<HII", POLICY_SIDECAR_SCHEMA_VERSION, len(self.records), 0),
        ]
        idx_parts = [POLICY_INDEX_MAGIC, struct.pack("<HI", POLICY_SIDECAR_SCHEMA_VERSION, len(self.records))]
        offset = len(b"".join(bin_parts)) + 4  # checksum placeholder
        for rid, payload, content_hash in self.records:
            crc = zlib.crc32(payload) & 0xFFFFFFFF
            bin_parts.append(struct.pack("<I", len(payload)))
            bin_parts.append(payload)
            idx_parts.append(
                struct.pack("<IQII32s", rid, offset, len(payload), crc, content_hash)
            )
            offset += 4 + len(payload)
        bin_body = b"".join(bin_parts)
        chunk_crc = zlib.crc32(bin_body) & 0xFFFFFFFF
        bin_bytes = bin_body[:16] + struct.pack("<I", chunk_crc) + bin_body[20:]
        idx_bytes = b"".join(idx_parts)
        return bin_bytes, idx_bytes
