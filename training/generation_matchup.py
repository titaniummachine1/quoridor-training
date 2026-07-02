"""Generation matchup selection: 30% prior-epoch vs 70% mixed opponent pool."""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Production Titanium v16 for candidate; embedded-weight engines for opponents.
MIXED_OPPONENT_POOL: tuple[str, ...] = (
    "ace-v13-ti-pure",
    "titanium-v15-frozen",
    "ace-v13-grafted",
)

MATCHUP_PRIOR_EPOCH = "prior_epoch"
MATCHUP_MIXED_OPPONENT = "mixed_opponent"
MATCHUP_SELFPLAY = "selfplay"  # no distinct prior yet


@dataclass(frozen=True)
class GenerationMatchup:
    kind: str
    engine_p0: str
    engine_p1: str
    weights_p0: Path | None
    weights_p1: Path | None
    current_is_p0: bool
    opponent_engine: str | None
    opening_exploration: bool
    metadata: dict[str, Any]


def _candidate_engine() -> str:
    return os.environ.get("TITANIUM_GENERATION_ENGINE", "titanium-v16").strip() or "titanium-v16"


def _prior_epoch_fraction() -> float:
    raw = os.environ.get("STREAM_PRIOR_EPOCH_FRACTION", "0.30")
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.30


def _selfplay_fraction() -> float:
    """Fraction of the non-prior remainder played as pure self-play
    (current vs current) instead of the mixed opponent pool."""
    raw = os.environ.get("STREAM_SELFPLAY_FRACTION", "0.0")
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.0


def _selfplay_matchup(cur_eng: str, current_weights: Path, prior_frac: float) -> GenerationMatchup:
    return GenerationMatchup(
        kind=MATCHUP_SELFPLAY,
        engine_p0=cur_eng,
        engine_p1=cur_eng,
        weights_p0=current_weights,
        weights_p1=current_weights,
        current_is_p0=True,
        opponent_engine=None,
        opening_exploration=True,
        metadata={"prior_epoch_fraction": prior_frac, "selfplay_fraction": _selfplay_fraction()},
    )


def uses_weight_override(engine: str) -> bool:
    return engine.startswith("titanium-v")


def choose_generation_matchup(
    rng: random.Random,
    *,
    current_weights: Path,
    previous_weights: Path | None,
) -> GenerationMatchup:
    """~30% current vs immediately previous accepted; ~70% mixed opponent pool."""
    cur_eng = _candidate_engine()
    prior_frac = _prior_epoch_fraction()
    has_prior = (
        previous_weights is not None
        and previous_weights.is_file()
        and previous_weights.resolve() != current_weights.resolve()
    )

    if not has_prior:
        # Epoch 1 path: 100% mixed opponent pool (no prior-epoch self match).
        opp = rng.choice(MIXED_OPPONENT_POOL)
        current_is_p0 = rng.random() < 0.5
        if current_is_p0:
            return GenerationMatchup(
                kind=MATCHUP_MIXED_OPPONENT,
                engine_p0=cur_eng,
                engine_p1=opp,
                weights_p0=current_weights,
                weights_p1=None,
                current_is_p0=True,
                opponent_engine=opp,
                opening_exploration=True,
                metadata={"prior_epoch_fraction": 0.0, "mixed_pool": list(MIXED_OPPONENT_POOL)},
            )
        return GenerationMatchup(
            kind=MATCHUP_MIXED_OPPONENT,
            engine_p0=opp,
            engine_p1=cur_eng,
            weights_p0=None,
            weights_p1=current_weights,
            current_is_p0=False,
            opponent_engine=opp,
            opening_exploration=True,
            metadata={"prior_epoch_fraction": 0.0, "mixed_pool": list(MIXED_OPPONENT_POOL)},
        )

    if rng.random() < prior_frac:
        current_is_p0 = rng.random() < 0.5
        if current_is_p0:
            w_p0, w_p1 = current_weights, previous_weights
        else:
            w_p0, w_p1 = previous_weights, current_weights
        return GenerationMatchup(
            kind=MATCHUP_PRIOR_EPOCH,
            engine_p0=cur_eng,
            engine_p1=cur_eng,
            weights_p0=w_p0,
            weights_p1=w_p1,
            current_is_p0=current_is_p0,
            opponent_engine=None,
            opening_exploration=True,
            metadata={"prior_epoch_fraction": prior_frac},
        )

    if rng.random() < _selfplay_fraction():
        return _selfplay_matchup(cur_eng, current_weights, prior_frac)

    opp = rng.choice(MIXED_OPPONENT_POOL)
    current_is_p0 = rng.random() < 0.5
    if current_is_p0:
        return GenerationMatchup(
            kind=MATCHUP_MIXED_OPPONENT,
            engine_p0=cur_eng,
            engine_p1=opp,
            weights_p0=current_weights,
            weights_p1=None,
            current_is_p0=True,
            opponent_engine=opp,
            opening_exploration=True,
            metadata={"prior_epoch_fraction": prior_frac, "mixed_pool": list(MIXED_OPPONENT_POOL)},
        )
    return GenerationMatchup(
        kind=MATCHUP_MIXED_OPPONENT,
        engine_p0=opp,
        engine_p1=cur_eng,
        weights_p0=None,
        weights_p1=current_weights,
        current_is_p0=False,
        opponent_engine=opp,
        opening_exploration=True,
        metadata={"prior_epoch_fraction": prior_frac, "mixed_pool": list(MIXED_OPPONENT_POOL)},
    )
