from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
DATA = REPO / "training" / "data" / "zero_teacher"
SCRAPE_DIR = DATA / "scrape"
LABELS_DIR = DATA / "labels"
DEFAULT_LABELS = LABELS_DIR / "search_budget.jsonl"
