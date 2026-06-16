#!/usr/bin/env python3
"""Overnight pool + NNUE micro-train supervisor.

Polls health every N seconds, logs a one-line status, and exits non-zero on
hard failures (parity broken, train crash loop, artifact hard cap).

Usage:
    python training/supervise.py                  # loop every 5 min (default)
    python training/supervise.py --once           # single check, exit
    python training/supervise.py --start-pool     # start pool if missing, then loop
    python training/supervise.py --interval 120   # check every 2 min

Log: training/data/supervisor.log
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "training"))

from datagen import DB_PATH, max_game_id, untrained_game_ids  # noqa: E402
from engine_identity import BIN, STAMP, load_expected_stamp  # noqa: E402
from manifest import CURRENT_ENGINE, entity_label, load_manifest  # noqa: E402
from nnue_guards import (  # noqa: E402
    CKPT_DIR,
    NNUE_LOG,
    artifact_usage,
    enforce_artifact_cap,
    load_guard_state,
)

SUP_LOG = ROOT / "training" / "data" / "supervisor.log"
POOL_SCRIPT = ROOT / "training" / "run_swiss_overnight.py"
PARITY_SCRIPT = ROOT / "training" / "parity_check.py"

# Windows native crash codes seen from train.py subprocess
_NATIVE_CRASH_RE = re.compile(r"exited (322122\d+|-?\d+)")
_TRAIN_FAIL_RE = re.compile(
    r"Training blocked|checkpoint schema|engine validation failed|HARD_CAP|exited [1-9]",
    re.I,
)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    line = f"{_ts()} {msg}"
    print(line, flush=True)
    SUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SUP_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def pool_running() -> bool:
    """True if run_swiss_overnight.py appears in a python command line."""
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
                    "Where-Object { $_.CommandLine -match 'run_swiss_overnight' } | "
                    "Measure-Object | Select-Object -ExpandProperty Count",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return out.stdout.strip() not in ("", "0")
        except Exception:
            return False
    try:
        out = subprocess.run(["pgrep", "-f", "run_swiss_overnight"], capture_output=True, text=True)
        return out.returncode == 0
    except FileNotFoundError:
        return False


def start_pool() -> bool:
    if not POOL_SCRIPT.exists():
        _log("ERROR: missing run_swiss_overnight.py")
        return False
    subprocess.Popen(
        [sys.executable, str(POOL_SCRIPT)],
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    _log("started run_swiss_overnight.py")
    time.sleep(8.0)
    return pool_running()


def tail_train_log(n: int = 40) -> list[str]:
    if not NNUE_LOG.exists():
        return []
    lines = NNUE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def recent_train_failures(lines: list[str], window: int = 15) -> tuple[list[str], list[str]]:
    """Returns (hard_failures, warnings)."""
    hard, warn = [], []
    for line in lines[-window:]:
        s = line.strip()
        if not s:
            continue
        if "Access is denied" in s and "titanium.exe" in s:
            warn.append(s)
        elif "parity_check failed" in s or "engine validation failed" in s:
            warn.append(s)
        elif _TRAIN_FAIL_RE.search(s):
            hard.append(s)
    return hard, warn


def run_parity() -> tuple[bool, str]:
    if not PARITY_SCRIPT.exists() or not BIN.exists():
        return False, "parity script or titanium.exe missing"
    r = subprocess.run(
        [sys.executable, str(PARITY_SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    text = r.stdout + r.stderr
    m = re.search(r"(\d+)/6 match", text)
    if m and m.group(1) == "6" and r.returncode == 0:
        return True, "6/6"
    return False, text.strip().splitlines()[-1] if text.strip() else f"exit {r.returncode}"


def v15_rating() -> int | None:
    manifest = load_manifest()
    ent = entity_label(CURRENT_ENGINE, "5s")
    info = manifest.get("global_ratings", {}).get(ent)
    return int(info["rating"]) if info else None


def check(*, run_parity_check: bool) -> tuple[str, int]:
    """Returns (summary_line, severity 0=ok 1=warn 2=fail)."""
    issues: list[str] = []
    severity = 0

    if not BIN.exists():
        issues.append("titanium.exe missing")
        severity = max(severity, 2)

    stamp = load_expected_stamp()
    if stamp is None:
        issues.append("engine stamp missing")
        severity = max(severity, 1)
    elif not STAMP.exists():
        issues.append("stamp file gone")
        severity = max(severity, 1)

    state = load_guard_state()
    last_trained = int(state.get("last_trained_game_id", 0))
    mx = max_game_id(DB_PATH)
    pending = untrained_game_ids(DB_PATH, last_trained)
    deploy_gap = int(state.get("games_since_deploy", 0))

    cap_ok, cap_msg = enforce_artifact_cap()
    usage = artifact_usage()
    ckpt_mb = usage["checkpoints_bytes"] / 1e6

    if not cap_ok:
        issues.append(cap_msg)
        severity = max(severity, 2)
    elif "WARN" in cap_msg:
        issues.append("soft cap")
        severity = max(severity, 1)

    if len(pending) > 12:
        issues.append(f"train backlog {len(pending)}")
        severity = max(severity, 1)

    pool_ok = pool_running()
    if not pool_ok:
        issues.append("pool not running")
        severity = max(severity, 2)

    log_tail = tail_train_log()
    hard_fails, warn_fails = recent_train_failures(log_tail)
    if hard_fails:
        issues.append(f"recent train errors ({len(hard_fails)})")
        severity = max(severity, 2)
    if warn_fails:
        issues.append(f"deploy blocked ({len(warn_fails)})")
        severity = max(severity, 1)

    parity_ok = True
    parity_msg = "skipped"
    if run_parity_check:
        parity_ok, parity_msg = run_parity()
        if not parity_ok:
            issues.append(f"parity {parity_msg}")
            # Stale embedded weights need rebuild — warn in loop, fail only with --once.
            severity = max(severity, 1)

    rating = v15_rating()
    rating_s = f"v15={rating}" if rating is not None else "v15=?"

    summary = (
        f"pool={'up' if pool_ok else 'DOWN'} "
        f"games={mx} trained={last_trained} pending={len(pending)} "
        f"deploy_gap={deploy_gap} ckpt={ckpt_mb:.0f}MB "
        f"parity={parity_msg} {rating_s}"
    )
    if issues:
        summary += " | " + "; ".join(issues)
    return summary, severity


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=int, default=300, help="Seconds between checks (default 300)")
    ap.add_argument("--once", action="store_true", help="Run one check and exit")
    ap.add_argument("--start-pool", action="store_true", help="Start pool if not running")
    ap.add_argument(
        "--parity-every",
        type=int,
        default=3,
        help="Run parity_check every N ticks (default 3 ≈ 15 min at 5m interval)",
    )
    args = ap.parse_args()

    tick = 0
    while True:
        tick += 1
        if args.start_pool and not pool_running():
            start_pool()

        do_parity = args.parity_every > 0 and (tick == 1 or tick % args.parity_every == 0)
        summary, severity = check(run_parity_check=do_parity)
        level = ("OK", "WARN", "FAIL")[severity]
        _log(f"[{level}] {summary}")

        if args.once:
            sys.exit(2 if severity >= 2 else (1 if severity == 1 else 0))

        if severity >= 2:
            _log("hard failure — supervisor exiting")
            sys.exit(2)

        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
