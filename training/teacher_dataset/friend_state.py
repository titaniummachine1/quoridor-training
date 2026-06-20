"""Shared friend JSONL state parsing — must match position_store_lib / Rust importer."""
from __future__ import annotations

from typing import Any

from position_store_state import PositionState


def parse_friend_state(obj: dict[str, Any]) -> PositionState:
    state_obj = obj["state"]
    if "currentPlayer" not in state_obj:
        raise KeyError("state.currentPlayer")
    side = state_obj["currentPlayer"]
    return PositionState(
        player0_cell=int(state_obj["player0Cell"]),
        player1_cell=int(state_obj["player1Cell"]),
        player0_walls=int(state_obj["player0Walls"]),
        player1_walls=int(state_obj["player1Walls"]),
        horizontal_walls=int(state_obj["horizontalWalls"]),
        vertical_walls=int(state_obj["verticalWalls"]),
        side_to_move=int(side),
    )
