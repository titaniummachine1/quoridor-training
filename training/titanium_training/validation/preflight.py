#!/usr/bin/env python3
"""Preflight before NNUE training — binary, parity, eval-batch schema.

Standalone; does not modify training logic.

    python training/nnue_cli.py preflight
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from titanium_training.paths import REPO_ROOT, TRAINING_ROOT

ROOT = REPO_ROOT
BIN = REPO_ROOT / "engine" / "target" / "release" / "titanium.exe"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


def main() -> int:
    if not BIN.exists():
        fail(f"missing binary: {BIN}")
    pass_(f"binary exists ({BIN.name})")

    parity = subprocess.run(
        [sys.executable, str(TRAINING_ROOT / "titanium_training" / "validation" / "parity_check.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if parity.stdout:
        print(parity.stdout.rstrip())
    if parity.stderr:
        print(parity.stderr.rstrip(), file=sys.stderr)
    if parity.returncode != 0 or "6/6 match" not in parity.stdout:
        fail("parity_check.py did not report 6/6 match")
    pass_("parity_check.py 6/6")

    try:
        batch = subprocess.run(
            [str(BIN), "eval-batch"],
            input="\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        fail(f"eval-batch failed (exit {e.returncode}): {(e.stderr or '')[:400]}")
    lines = [ln for ln in batch.stdout.splitlines() if ln.strip()]
    if not lines:
        fail("eval-batch returned no JSON lines")
    try:
        rec = json.loads(lines[0])
    except json.JSONDecodeError as e:
        fail(f"eval-batch JSON parse error: {e}")
    if "legal_wall_count" not in rec:
        fail("eval-batch record missing legal_wall_count")
    pass_(f"eval-batch legal_wall_count={rec['legal_wall_count']}")

    print("\nREADY: training preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
