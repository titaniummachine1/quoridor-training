"""NNUE field plane names — single source of truth for engine JSON and trainer.

Philosophy: BFS/search owns exact geometry; NN compresses topology into priors (H=32).
See engine/src/acev13/field_planes.rs for the full table and pre-training notes.

Do NOT add extra BFS / wall-delta planes here — those belong in search.
Optional later: block_pressure (pawn interferes with route) if tactical losses warrant it.
"""

# Canonical JSON keys (eval --json / datagen)
GOAL_INV_P0 = "goal_inv_p0_field"
GOAL_INV_P1 = "goal_inv_p1_field"
PAWN_FWD_P0 = "pawn_fwd_p0_field"
PAWN_FWD_P1 = "pawn_fwd_p1_field"
CORRIDOR_DELTA_P0 = "corridor_delta_p0_field"
CORRIDOR_DELTA_P1 = "corridor_delta_p1_field"
PATH_CROSS_P0 = "path_cross_p0_field"
PATH_CROSS_P1 = "path_cross_p1_field"
CHOKE_P0 = "choke_p0_field"
CHOKE_P1 = "choke_p1_field"
CONTESTED = "contested_field"

# Legacy aliases (older overnight JSONL before rename)
_LEGACY = {
    GOAL_INV_P0: ("d0_field",),
    GOAL_INV_P1: ("d1_field",),
    PAWN_FWD_P0: ("player0_field",),
    PAWN_FWD_P1: ("player1_field",),
    CORRIDOR_DELTA_P0: ("delta0_field",),
    CORRIDOR_DELTA_P1: ("delta1_field",),
    PATH_CROSS_P0: ("cross0_field",),
    PATH_CROSS_P1: ("cross1_field",),
}


def encode_contested(delta_p0: int, delta_p1: int) -> float:
    """Continuous shared importance: 1/(1+d0+d1), u8 stored as round(16×value)÷16."""
    if delta_p0 == 255 or delta_p1 == 255:
        return 0.0
    raw = min(round(16 / (1 + delta_p0 + delta_p1)), 16)
    return raw / 16.0


def rec_field(rec: dict, canonical_key: str) -> list:
    """Read a per-cell field from a training record (canonical or legacy key)."""
    val = rec.get(canonical_key)
    if val:
        return val
    for alt in _LEGACY.get(canonical_key, ()):
        val = rec.get(alt)
        if val:
            return val
    return []

# Weight blob plane order (must match acev13/net.rs load order)
WEIGHT_PLANE_ORDER = (
    "goal_inv_p0", "goal_inv_p1",
    "pawn_fwd_p0", "pawn_fwd_p1",
    "corridor_delta_p0", "corridor_delta_p1",
    "path_cross_p0", "path_cross_p1",
    "choke_p0", "choke_p1",
    "contested",
)
FIELD_PLANE_COUNT = len(WEIGHT_PLANE_ORDER)
