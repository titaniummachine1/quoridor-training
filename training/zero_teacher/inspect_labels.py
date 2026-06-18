#!/usr/bin/env python3
"""Summarize paired zero-ink label quality before sidecar training."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data")
    args = ap.parse_args()
    rows = [json.loads(line) for line in Path(args.data).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        print("no labels")
        return 1
    pressures = [float(row["search_pressure"]) for row in rows]
    disagreements = [row["search"]["disagreement"] for row in rows]
    changed = sum(bool(d["best_move_changed"]) for d in disagreements)
    jsd = [float(d["visit_js_divergence"]) for d in disagreements]
    delta = [float(d["root_value_delta"]) for d in disagreements]
    sources = len({row.get("source_game_key") for row in rows})
    plies = [int(row.get("ply", 0)) for row in rows]
    print(f"rows={len(rows)} source_games={sources} ply={min(plies)}..{max(plies)}")
    print(
        f"pressure min={min(pressures):+.3f} mean={statistics.mean(pressures):+.3f} "
        f"max={max(pressures):+.3f} unique={len(set(round(v, 6) for v in pressures))}"
    )
    print(f"best_move_changed={changed}/{len(rows)} ({changed/len(rows):.1%})")
    print(f"visit_jsd mean={statistics.mean(jsd):.4f} max={max(jsd):.4f}")
    print(f"root_value_delta mean={statistics.mean(delta):.4f} max={max(delta):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
