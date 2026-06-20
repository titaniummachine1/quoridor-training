"""pytest configuration for training tests."""
from __future__ import annotations

import _pytest.pathlib


# On Windows the stale 'pytest-current' junction in the temp directory raises
# PermissionError inside cleanup_dead_symlinks, crashing pytest_sessionfinish
# and masking all test output + exit code.  Swallow it here.
_orig_cleanup_dead = _pytest.pathlib.cleanup_dead_symlinks


def _safe_cleanup_dead_symlinks(root):  # type: ignore[no-untyped-def]
    try:
        _orig_cleanup_dead(root)
    except PermissionError:
        pass


_pytest.pathlib.cleanup_dead_symlinks = _safe_cleanup_dead_symlinks
