#!/usr/bin/env python3
"""Backward-compatible entrypoint — delegates to titanium_training.cli."""
from __future__ import annotations

import sys
from pathlib import Path

_TRAINING_ROOT = Path(__file__).resolve().parent
if str(_TRAINING_ROOT) not in sys.path:
    sys.path.insert(0, str(_TRAINING_ROOT))

from titanium_training.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
