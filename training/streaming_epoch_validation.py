"""Post-epoch validation for streaming NNUE training (no auto-deploy)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_TRAINING = Path(__file__).resolve().parent
_REPO = _TRAINING.parent
sys.path.insert(0, str(_TRAINING))

from streaming_checkpoint_chain import FROZEN_WEIGHTS, sha256_file
from titanium_training.paths import ENGINE_BIN, REPO_ROOT
from titanium_training.validation.export_parity import verify_export_parity
from titanium_training.validation.opening_sanity import assert_opening_sanity

RUN_DIR = _TRAINING / "runs" / "v16"
LOG_DIR = _TRAINING / "data" / "overnight_logs"


def _run_match(
    *,
    games: int,
    time_sec: float,
    engine_a: str,
    engine_b: str,
    weights_a: Path | None,
    weights_b: Path | None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("TITANIUM_NET_WEIGHTS_PATH", None)
    if weights_a:
        env["TITANIUM_NET_WEIGHTS_PATH"] = str(weights_a.resolve())
    cmd = [
        str(ENGINE_BIN),
        "match",
        "--games",
        str(games),
        "--time",
        str(time_sec),
        "--openings",
        "book",
        "--a",
        engine_a,
        "--b",
        engine_b,
        "--no-early-stop",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=3600)
    lines = (proc.stdout + proc.stderr).splitlines()
    summary = [ln for ln in lines if "wins" in ln.lower() or "STRENGTH" in ln or "score" in ln.lower()]
    return {
        "exit_code": proc.returncode,
        "weights_a_sha256": sha256_file(weights_a) if weights_a else None,
        "weights_b_sha256": sha256_file(weights_b) if weights_b else None,
        "summary": summary[-8:],
    }


def _search_bench(weights: Path | None) -> dict[str, Any]:
    bench = _REPO / "engine" / "target" / "release" / "search_bench.exe"
    if not bench.is_file():
        return {"skipped": True, "reason": "search_bench missing"}
    env = os.environ.copy()
    if weights:
        env["TITANIUM_NET_WEIGHTS_PATH"] = str(weights.resolve())
    else:
        env.pop("TITANIUM_NET_WEIGHTS_PATH", None)
    proc = subprocess.run(
        [str(bench), "time", "--sec", "2", "--runs", "3"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=120,
    )
    tail = proc.stdout.strip().splitlines()
    parsed = {}
    if tail:
        try:
            parsed = json.loads(tail[-1])
        except json.JSONDecodeError:
            parsed = {"raw": tail[-1]}
    return {"exit_code": proc.returncode, "median_nps": parsed.get("median_nps"), "median_depth": parsed.get("median_depth"), "move": parsed.get("move")}


def run_epoch_validation(
    *,
    checkpoint: Path,
    candidate_bin: Path,
    previous_bin: Path | None,
    frozen_bin: Path = FROZEN_WEIGHTS,
    short_games: int = 20,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "candidate_sha256": sha256_file(candidate_bin),
        "checkpoint": str(checkpoint),
    }

    parity = verify_export_parity(checkpoint, candidate_bin)
    report["export_parity"] = {
        "passed": parity.passed,
        "max_cp": parity.max_parity_error,
    }

    try:
        assert_opening_sanity(candidate_bin)
        report["opening_sanity"] = {"passed": True}
    except Exception as exc:
        report["opening_sanity"] = {"passed": False, "error": str(exc)}

    report["parity_check"] = {
        "skipped": True,
        "blocking": False,
        "reason": "streaming NNUE training intentionally does not gate on Python/engine eval parity",
    }

    report["search_bench"] = _search_bench(candidate_bin)

    # Per-epoch strength gate (user-mandated): the candidate must hold its own
    # against the immediately prior accepted weights or the epoch is
    # quarantined. Uses the per-side-env weights match driver (the in-process
    # `titanium match` harness cannot pit two different weight files — that is
    # why this gate was historically skipped). Bookless, alternating colors.
    if previous_bin is not None and previous_bin.is_file() and sha256_file(previous_bin) != report["candidate_sha256"]:
        from tools.weights_match import run_weights_match

        gate_games = int(os.environ.get("STREAM_GATE_GAMES", "12"))
        gate_sec = float(os.environ.get("STREAM_GATE_SEC", "0.5"))
        min_score = float(os.environ.get("STREAM_MIN_WINRATE_VS_PREV", "0.45"))
        try:
            m = run_weights_match(str(candidate_bin), str(previous_bin), gate_games, gate_sec, quiet=True)
            report["match_vs_previous"] = {
                "skipped": False,
                "blocking": True,
                "games": m["games"],
                "candidate_points": m["a_points"],
                "previous_points": m["b_points"],
                "score": m["score"],
                "elo_diff": m["elo_diff"],
                "min_score": min_score,
                "passed": m["score"] >= min_score,
            }
        except Exception as exc:
            # A broken gate must not silently wave epochs through.
            report["match_vs_previous"] = {
                "skipped": False,
                "blocking": True,
                "passed": False,
                "error": str(exc),
            }
    else:
        report["match_vs_previous"] = {
            "skipped": True,
            "blocking": False,
            "reason": "no distinct prior accepted weights to compare against",
        }
    report["match_vs_frozen"] = {
        "skipped": True,
        "blocking": False,
        "reason": "unfinished streaming weights are not compared against older weights",
    }
    report["control_vs_control"] = {
        "skipped": True,
        "blocking": False,
        "reason": "unfinished streaming weights are not match-tested during streaming acceptance",
    }

    report["passed"] = (
        report["export_parity"]["passed"]
        and report["opening_sanity"]["passed"]
        and report["match_vs_previous"].get("passed", True)
    )
    if not report["match_vs_previous"].get("passed", True):
        report["reject_reason"] = "strength_gate_vs_previous"
    return report
