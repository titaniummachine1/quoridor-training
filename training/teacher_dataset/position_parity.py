"""Rust/Python position codec parity audit over friend corpus."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium_training.store.config import FRIEND_CORPUS_DIR, REPORT_DIR, ROOT, TEACHER_STORE_DB
from titanium_training.store.friend import discover_friend_shards
from titanium_training.store.state import PositionState

from .canonical_identity import canonical_hash_from_packed
from .friend_state import parse_friend_state

HASH_ONLY_MISMATCH = "HASH_ONLY_MISMATCH"
PACKED_STATE_MISMATCH = "PACKED_STATE_MISMATCH"
SOURCE_SCHEMA_MISMATCH = "SOURCE_SCHEMA_MISMATCH"
UNRESOLVED = "UNRESOLVED"
MATCH = "MATCH"


@dataclass
class ParityReport:
    records_checked: int = 0
    unique_positions_checked: int = 0
    matching_packed_states: int = 0
    hash_only_mismatches: int = 0
    packed_state_mismatches: int = 0
    schema_mismatches: int = 0
    unresolved_mismatches: int = 0
    classifications: Counter = field(default_factory=Counter)
    affected_shards: Counter = field(default_factory=Counter)
    first_diff_byte_counts: Counter = field(default_factory=Counter)
    samples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.packed_state_mismatches == 0
            and self.unresolved_mismatches == 0
            and self.schema_mismatches == 0
            and self.hash_only_mismatches == 0
            and self.records_checked > 0
            and self.matching_packed_states > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "records_checked": self.records_checked,
            "unique_positions_checked": self.unique_positions_checked,
            "matching_packed_states": self.matching_packed_states,
            "hash_only_mismatches": self.hash_only_mismatches,
            "packed_state_mismatches": self.packed_state_mismatches,
            "schema_mismatches": self.schema_mismatches,
            "unresolved_mismatches": self.unresolved_mismatches,
            "classifications": dict(self.classifications),
            "affected_shards": dict(self.affected_shards.most_common(25)),
            "first_differing_byte_index_counts": dict(self.first_diff_byte_counts.most_common(24)),
            "passed": self.passed,
            "promotion_blocked": not self.passed,
            "samples": self.samples[:30],
        }


def _parse_state_from_json(obj: dict[str, Any]) -> PositionState:
    return parse_friend_state(obj)


def _first_diff_byte(a: bytes, b: bytes) -> int | None:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    if len(a) != len(b):
        return min(len(a), len(b))
    return None


def audit_friend_position_parity(
    *,
    teacher_db: Path = TEACHER_STORE_DB,
    corpus_dir: Path = FRIEND_CORPUS_DIR,
    limit: int | None = None,
) -> ParityReport:
    """Compare Python-parsed JSONL states against Rust-imported teacher DB identities."""
    report = ParityReport()
    conn = sqlite3.connect(teacher_db)
    packed_to_db: dict[bytes, tuple[bytes, bytes]] = {}
    for canonical, packed in conn.execute("SELECT canonical_hash, packed_state FROM positions"):
        packed_to_db[bytes(packed)] = (bytes(canonical), bytes(packed))
    conn.close()

    seen_packed: set[bytes] = set()
    for shard_path in discover_friend_shards():
        shard_name = shard_path.parent.name
        with shard_path.open(encoding="utf-8") as handle:
            for line in handle:
                if limit is not None and report.records_checked >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                report.records_checked += 1
                try:
                    obj = json.loads(line)
                    state = _parse_state_from_json(obj)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    report.schema_mismatches += 1
                    report.classifications[SOURCE_SCHEMA_MISMATCH] += 1
                    report.affected_shards[shard_name] += 1
                    continue

                packed_py = state.packed_state()
                hash_py = canonical_hash_from_packed(packed_py)
                if packed_py not in seen_packed:
                    seen_packed.add(packed_py)
                    report.unique_positions_checked += 1

                db_row = packed_to_db.get(packed_py)
                if db_row is None:
                    report.packed_state_mismatches += 1
                    report.classifications[PACKED_STATE_MISMATCH] += 1
                    report.affected_shards[shard_name] += 1
                    continue

                db_canonical, db_packed = db_row
                if db_packed != packed_py:
                    report.packed_state_mismatches += 1
                    report.classifications[PACKED_STATE_MISMATCH] += 1
                    report.affected_shards[shard_name] += 1
                    continue

                if db_canonical != hash_py:
                    report.hash_only_mismatches += 1
                    report.classifications[HASH_ONLY_MISMATCH] += 1
                    report.affected_shards[shard_name] += 1
                    if len(report.samples) < 30:
                        report.samples.append(
                            {
                                "shard": shard_name,
                                "classification": HASH_ONLY_MISMATCH,
                                "db_canonical": db_canonical.hex(),
                                "py_hash": hash_py.hex(),
                                "packed": packed_py.hex(),
                            }
                        )
                    continue

                report.matching_packed_states += 1
                report.classifications[MATCH] += 1

        if limit is not None and report.records_checked >= limit:
            break

    return report


def write_parity_report(report: ParityReport, *, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"position_parity_{stamp}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec": __import__("teacher_dataset.canonical_identity", fromlist=["spec_document"]).spec_document(),
        **report.to_dict(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
