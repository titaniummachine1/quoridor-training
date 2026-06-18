#!/usr/bin/env python3
"""Migrate HalfPW blobs to five zero-initialized sparse route embeddings."""

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NET_H = 32
BASE_F64S = 16 + NET_H + NET_H + 9 * 128 * NET_H + 81 * NET_H * 2
FIELD_LEN = 81
SPARSE_F64S = BASE_F64S + FIELD_LEN * 5


def migrate(path: Path) -> None:
    raw = path.read_bytes()
    if len(raw) % 8:
        raise ValueError(f"{path}: size is not an f64 blob")
    count = len(raw) // 8
    if count == SPARSE_F64S:
        print(f"{path.name}: already sparse-route-v1")
        return
    if count < BASE_F64S:
        raise ValueError(f"{path}: truncated blob ({count} f64s)")
    base = raw[: BASE_F64S * 8]
    zeros = struct.pack(f"<{FIELD_LEN * 5}d", *([0.0] * (FIELD_LEN * 5)))
    path.write_bytes(base + zeros)
    print(f"{path.name}: {count} -> {SPARSE_F64S} f64s")


def main() -> None:
    for name in ("net_weights.bin", "net_weights_frozen.bin"):
        migrate(ROOT / "engine" / "src" / "acev13" / name)


if __name__ == "__main__":
    main()
