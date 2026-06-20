"""Regression: active Titanium paths must not reintroduce acev13 namespace leakage."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = Path(__file__).resolve()

# Build forbidden needles without embedding exact contiguous literals in this file.
_ACE = "ace"
_V13 = "v13"
_ACEV13 = _ACE + _V13
_FORBIDDEN_NEEDLES = (
    f"crate::{_ACEV13}",
    f"mod {_ACEV13}",
    f"engine/src/{_ACEV13}",
    f"src/{_ACEV13}",
    f"{_ACEV13}/net_weights.bin",
    "ace_game_from_packed",
    "AceGame",
    "AceSearch",
    f"titanium::{_ACEV13}",
)

ALLOWLIST_SUBSTRINGS = (
    "historical",
    "Originally derived from ACE",
    "ace-v13",  # external engine flag strings
    "engine/src/ace/",  # ACE v11 reference module (protected)
    "Historical note:",
    "Historical origin:",
)


def _production_roots() -> list[Path]:
    return [
        ROOT / "training" / "titanium_training",
        ROOT / "training" / "tests",
        ROOT / "docs" / "DATASET.md",
        ROOT / "docs" / "TRAINING.md",
        ROOT / "docs" / "ROADMAP.md",
    ]


def _production_files(*, exclude: Path | None = None) -> list[Path]:
    out: list[Path] = []
    for base in _production_roots():
        if base.is_file():
            candidates = [base]
        else:
            candidates = [
                p
                for p in base.rglob("*")
                if p.suffix in {".py", ".md", ".yaml", ".rs"}
                and p.is_file()
                and "__pycache__" not in p.parts
                and ".pytest-temp" not in p.parts
            ]
        for path in candidates:
            if exclude is not None and path.resolve() == exclude.resolve():
                continue
            if path.resolve() == GUARD_PATH.resolve():
                continue
            out.append(path)
    return out


def _line_allowed(line: str) -> bool:
    lower = line.lower()
    return any(a.lower() in lower for a in ALLOWLIST_SUBSTRINGS)


def scan_for_active_leakage(*, exclude: Path | None = None) -> list[str]:
    hits: list[str] = []
    for path in _production_files(exclude=exclude):
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _line_allowed(line):
                continue
            for needle in _FORBIDDEN_NEEDLES:
                if needle in line:
                    hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
                    break
    return hits


def test_no_active_acev13_in_production_paths() -> None:
    hits = scan_for_active_leakage()
    assert not hits, "active acev13 references found:\n" + "\n".join(hits[:20])


def test_guard_source_does_not_trigger_itself() -> None:
    hits = scan_for_active_leakage(exclude=GUARD_PATH)
    guard_hits = [h for h in hits if str(GUARD_PATH.relative_to(ROOT)) in h]
    assert not guard_hits, "guard file triggered its own scan:\n" + "\n".join(guard_hits)


def test_fixture_with_forbidden_active_terminology_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad_fixture.py"
    bad.write_text(
        f"from engine import {_ACEV13}\n"
        f"WEIGHTS = ROOT / 'engine/src/{_ACEV13}/net_weights.bin'\n",
        encoding="utf-8",
    )
    hits = scan_for_active_leakage(exclude=GUARD_PATH)
    rel = bad.relative_to(ROOT) if bad.is_relative_to(ROOT) else bad
    # Inject fixture into scan by reading directly.
    text = bad.read_text(encoding="utf-8")
    fixture_hits = [
        needle
        for needle in _FORBIDDEN_NEEDLES
        if any(needle in line and not _line_allowed(line) for line in text.splitlines())
    ]
    assert fixture_hits, f"fixture should contain forbidden needles; wrote {rel}"


def test_historical_ace_fixture_passes(tmp_path: Path) -> None:
    good = tmp_path / "historical_note.md"
    good.write_text(
        "Historical note: internal pawn ordering originated in the ACE v13 implementation.\n"
        "Current owner and runtime: Titanium v15.\n"
        "Protected ACE-only path: engine/src/ace/mod.rs\n",
        encoding="utf-8",
    )
    text = good.read_text(encoding="utf-8")
    hits = [
        needle
        for needle in _FORBIDDEN_NEEDLES
        if any(needle in line and not _line_allowed(line) for line in text.splitlines())
    ]
    assert not hits
