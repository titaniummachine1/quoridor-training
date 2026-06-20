"""Resolve stored sidecar paths to on-disk policy files."""
from __future__ import annotations

from pathlib import Path

from titanium_training.store.config import ROOT, TEACHER_SIDECARS

from .config import LEGACY_WRONG_SIDECAR_PREFIX


def normalize_stored_path(stored: str) -> str:
    return stored.replace("\\", "/")


def resolve_sidecar_path(stored: str, *, root: Path = ROOT) -> Path:
    """Map DB-stored sidecar path to the actual file on disk."""
    stored = normalize_stored_path(stored.strip())
    if not stored:
        raise ValueError("empty sidecar path")

    direct = root / stored
    if direct.is_file():
        return direct

    # Rust v0.1.0 bug: stored C:/.../friend_selfplay/iter_XXX.policy.bin.gz
    # Files live under training/data/canonical/teacher_sidecars/friend_selfplay/
    name = Path(stored).name
    if name.endswith(".policy.bin.gz"):
        candidate = TEACHER_SIDECARS / "friend_selfplay" / name
        if candidate.is_file():
            return candidate

    if stored.startswith(LEGACY_WRONG_SIDECAR_PREFIX):
        candidate = TEACHER_SIDECARS / stored
        if candidate.is_file():
            return candidate

    if stored.startswith("training/data/canonical/teacher_sidecars/"):
        candidate = root / stored
        if candidate.is_file():
            return candidate

    return direct


def classify_sidecar_path(stored: str, *, root: Path = ROOT) -> str:
    stored_n = normalize_stored_path(stored)
    if not stored_n:
        return "missing_because_no_path"
    try:
        resolved = resolve_sidecar_path(stored_n, root=root)
    except ValueError:
        return "missing_because_no_path"
    if resolved.is_file():
        if stored_n.startswith("C:") or stored_n.startswith("c:"):
            return "repaired_wrong_base_friend_selfplay_at_root"
        if stored_n.startswith(LEGACY_WRONG_SIDECAR_PREFIX) and not (root / stored_n).is_file():
            return "repaired_wrong_base_friend_selfplay_at_root"
        if stored_n.startswith("training/data/canonical/teacher_sidecars/"):
            return "path_ok_teacher_sidecars_relative"
        return "path_ok_other"
    if stored_n.startswith("C:") or LEGACY_WRONG_SIDECAR_PREFIX in stored_n:
        return "missing_because_filename_mismatch_or_file_absent"
    return "missing_because_wrong_base_directory"
