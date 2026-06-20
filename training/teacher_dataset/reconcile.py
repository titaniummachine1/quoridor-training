"""Reconcile SQLite teacher counts against source semantics."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from position_store_config import TEACHER_STORE_DB


def reconcile_teacher_counts(db_path: Path = TEACHER_STORE_DB) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    by_type = {
        str(r[0]): int(r[1])
        for r in conn.execute("SELECT label_type, COUNT(*) FROM labels GROUP BY label_type")
    }
    friend_labels = int(
        conn.execute("SELECT COUNT(*) FROM labels WHERE source LIKE 'friend_selfplay:%'").fetchone()[0]
    )
    friend_cohorts = int(
        conn.execute("SELECT COUNT(DISTINCT source) FROM labels WHERE source LIKE 'friend_selfplay:%'").fetchone()[0]
    )
    imports = list(
        conn.execute(
            "SELECT source_path, accepted_count, rejected_count, status FROM imports WHERE format='alpha-selfplay-jsonl'"
        )
    )
    accepted_import = sum(int(r[1] or 0) for r in imports if r[3] == "completed")
    conn.close()

    return {
        "labels_by_type": by_type,
        "friend_teacher_labels": friend_labels,
        "friend_distinct_cohorts": friend_cohorts,
        "accepted_friend_source_records": accepted_import,
        "label_inflation_explanation": (
            "Friend import aggregates within a batch by LabelKey "
            "(canonical_hash, cohort source, value_bits, policy_hash) but uses one cohort per iteration "
            "(friend_selfplay:iter_NNNNNN). The same canonical position visited in multiple iterations "
            "produces multiple label rows. Non-friend label types (search_pressure, reduction_counterfactual) "
            "add further rows beyond friend JSONL line count."
        ),
        "labels_minus_friend_accepted": sum(by_type.values()) - accepted_import,
    }
