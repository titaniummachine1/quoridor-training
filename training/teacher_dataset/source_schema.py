"""Authoritative schema declaration for friend JSONL source records.

All friend-corpus JSONL records must conform to this schema before ingestion.
This module is the single source of truth — importers and tests both reference it.
"""
from __future__ import annotations

from typing import Any

REQUIRED_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "currentPlayer",
        "player0Cell",
        "player1Cell",
        "player0Walls",
        "player1Walls",
        "horizontalWalls",
        "verticalWalls",
    }
)

KNOWN_OPTIONAL_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "sideToMove",
    }
)

REQUIRED_RECORD_FIELDS: frozenset[str] = frozenset({"state"})

KNOWN_OPTIONAL_RECORD_FIELDS: frozenset[str] = frozenset(
    {
        "policy",
        "value",
        "bestMove",
        "visitCount",
        "source",
        "iteration",
        "schema",
    }
)

FIELD_SEMANTICS: dict[str, str] = {
    "currentPlayer": "0=player0/white-to-move; 1=player1/black-to-move; authoritative",
    "sideToMove": "LEGACY alias for currentPlayer; must NOT be the only side field present",
    "player0Cell": "packed 0-80 board cell index for player 0 pawn",
    "player1Cell": "packed 0-80 board cell index for player 1 pawn",
    "player0Walls": "walls remaining for player 0 (0-10)",
    "player1Walls": "walls remaining for player 1 (0-10)",
    "horizontalWalls": "64-bit mask of placed horizontal walls",
    "verticalWalls": "64-bit mask of placed vertical walls",
    "policy": "sparse {move: prob} dict; move is algebraic or move-code integer",
    "value": "root value in [-1, 1] from the side of currentPlayer",
    "bestMove": "best move as algebraic string",
    "visitCount": "MCTS visit count at root",
}


def validate_record(obj: dict[str, Any]) -> list[str]:
    """Return a list of validation errors; empty list = record is valid."""
    errors: list[str] = []
    for field in REQUIRED_RECORD_FIELDS:
        if field not in obj:
            errors.append(f"missing required field: {field!r}")
    state = obj.get("state", {})
    if not isinstance(state, dict):
        errors.append("state must be a dict")
        return errors
    for field in REQUIRED_STATE_FIELDS:
        if field not in state:
            errors.append(f"missing required state field: state.{field!r}")
    if "currentPlayer" not in state and "sideToMove" in state:
        errors.append(
            "state.sideToMove present without state.currentPlayer — "
            "this is a legacy alias and must not be the only side field"
        )
    for key in ("player0Cell", "player1Cell", "player0Walls", "player1Walls"):
        if key in state:
            try:
                v = int(state[key])
                if key.endswith("Walls") and not 0 <= v <= 10:
                    errors.append(f"state.{key}={v} out of range 0..10")
                elif key.endswith("Cell") and not 0 <= v <= 80:
                    errors.append(f"state.{key}={v} out of range 0..80")
            except (TypeError, ValueError):
                errors.append(f"state.{key} must be an integer")
    return errors
