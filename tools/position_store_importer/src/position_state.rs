//! Quoridor position state — byte layout and validation parity with
//! `training/position_store_state.py` (POSITION_SCHEMA_VERSION = 1).

use std::collections::HashSet;

pub const POSITION_SCHEMA_VERSION: u8 = 1;
pub const MOVE_SCHEMA_VERSION: u8 = 1;

pub const BOARD_SIZE: u8 = 9;
pub const WALL_GRID_SIZE: u8 = 8;
pub const WALLS_PER_PLAYER: u8 = 10;
pub const CELL_COUNT: u8 = BOARD_SIZE * BOARD_SIZE;
pub const WALL_SLOT_COUNT: u8 = WALL_GRID_SIZE * WALL_GRID_SIZE;

pub const HORIZONTAL_WALL_BASE: u8 = 0;
pub const VERTICAL_WALL_BASE: u8 = 64;
pub const PAWN_MOVE_BASE: u8 = 128;

pub const PAWN_NORTH: u8 = 128;
pub const PAWN_SOUTH: u8 = 129;
pub const PAWN_EAST: u8 = 130;
pub const PAWN_WEST: u8 = 131;
pub const PAWN_NORTHEAST: u8 = 132;
pub const PAWN_NORTHWEST: u8 = 133;
pub const PAWN_SOUTHEAST: u8 = 134;
pub const PAWN_SOUTHWEST: u8 = 135;

pub const START_P0_CELL: u8 = 4;
pub const START_P1_CELL: u8 = 76;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PositionState {
    pub player0_cell: u8,
    pub player1_cell: u8,
    pub player0_walls: u8,
    pub player1_walls: u8,
    pub horizontal_walls: u64,
    pub vertical_walls: u64,
    pub side_to_move: u8,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum PositionError {
    #[error("column out of range: {0}")]
    ColumnOutOfRange(i32),
    #[error("bad file: {file:?}")]
    BadFile { file: String },
    #[error("cell out of range: {0}")]
    CellOutOfRange(u8),
    #[error("bad coordinates: ({row}, {col})")]
    BadCoordinates { row: u8, col: u8 },
    #[error("bad square: {text:?}")]
    BadSquare { text: String },
    #[error("bad rank: {text:?}")]
    BadRank { text: String },
    #[error("wall slot out of range: {0}")]
    WallSlotOutOfRange(u8),
    #[error("bad wall move: {text:?}")]
    BadWallMove { text: String },
    #[error("bad wall rank: {text:?}")]
    BadWallRank { text: String },
    #[error("wall code out of range: {0}")]
    WallCodeOutOfRange(u8),
    #[error("delta does not map to pawn direction: ({dr}, {dc})")]
    BadDirectionDelta { dr: i8, dc: i8 },
    #[error("not a pawn code: {0}")]
    NotPawnCode(u8),
    #[error("player0 cell out of range")]
    Player0CellOutOfRange,
    #[error("player1 cell out of range")]
    Player1CellOutOfRange,
    #[error("both pawns occupy the same cell")]
    PawnCollision,
    #[error("player0 walls out of range")]
    Player0WallsOutOfRange,
    #[error("player1 walls out of range")]
    Player1WallsOutOfRange,
    #[error("side_to_move must be 0 or 1")]
    BadSideToMove,
    #[error("wall masks must be nonnegative")]
    NegativeWallMask,
    #[error("wall masks must fit in 64 bits")]
    WallMaskOverflow,
    #[error("{orientation} wall slot out of range: {slot}")]
    WallMaskSlotOutOfRange { orientation: &'static str, slot: u8 },
    #[error("neighboring horizontal walls overlap illegally")]
    HorizontalNeighborOverlap,
    #[error("neighboring vertical walls overlap illegally")]
    VerticalNeighborOverlap,
    #[error("horizontal/vertical walls cross at same anchor")]
    CrossWallCollision,
    #[error("wall layout disconnects a player from goal")]
    PathDisconnected,
    #[error("packed state must be 24 bytes, got {0}")]
    PackedStateWrongLength(usize),
    #[error("unsupported position schema version: {0}")]
    UnsupportedSchemaVersion(u8),
    #[error("illegal wall move from state: {notation}")]
    IllegalWallMove { notation: String },
    #[error("bad move notation: {notation:?}")]
    BadMoveNotation { notation: String },
    #[error("illegal pawn move from state: {notation}")]
    IllegalPawnMove { notation: String },
    #[error("unsupported alpha action id: {0}")]
    UnsupportedAlphaAction(u16),
}

impl Default for PositionState {
    fn default() -> Self {
        Self::initial()
    }
}

impl PositionState {
    pub fn initial() -> Self {
        Self {
            player0_cell: START_P0_CELL,
            player1_cell: START_P1_CELL,
            player0_walls: WALLS_PER_PLAYER,
            player1_walls: WALLS_PER_PLAYER,
            horizontal_walls: 0,
            vertical_walls: 0,
            side_to_move: 0,
        }
    }

    pub fn validate(&self, require_paths: bool) -> Result<(), PositionError> {
        if self.player0_cell >= CELL_COUNT {
            return Err(PositionError::Player0CellOutOfRange);
        }
        if self.player1_cell >= CELL_COUNT {
            return Err(PositionError::Player1CellOutOfRange);
        }
        if self.player0_cell == self.player1_cell {
            return Err(PositionError::PawnCollision);
        }
        if self.player0_walls > WALLS_PER_PLAYER {
            return Err(PositionError::Player0WallsOutOfRange);
        }
        if self.player1_walls > WALLS_PER_PLAYER {
            return Err(PositionError::Player1WallsOutOfRange);
        }
        if self.side_to_move > 1 {
            return Err(PositionError::BadSideToMove);
        }
        validate_wall_mask(self.horizontal_walls, true)?;
        validate_wall_mask(self.vertical_walls, false)?;
        validate_cross_and_neighbor_collisions(self.horizontal_walls, self.vertical_walls)?;
        if require_paths && !both_players_reach_goals(self) {
            return Err(PositionError::PathDisconnected);
        }
        Ok(())
    }

    pub fn current_cell(&self) -> u8 {
        if self.side_to_move == 0 {
            self.player0_cell
        } else {
            self.player1_cell
        }
    }

    pub fn player_cell(&self, player: u8) -> u8 {
        if player == 0 {
            self.player0_cell
        } else {
            self.player1_cell
        }
    }

    pub fn player_walls_left(&self, player: u8) -> u8 {
        if player == 0 {
            self.player0_walls
        } else {
            self.player1_walls
        }
    }

    pub fn packed_state(&self) -> [u8; 24] {
        let mut out = [0u8; 24];
        out[0] = POSITION_SCHEMA_VERSION;
        out[1] = self.player0_cell;
        out[2] = self.player1_cell;
        out[3] = self.player0_walls;
        out[4] = self.player1_walls;
        out[5] = self.side_to_move;
        out[6] = 0;
        out[7] = 0;
        out[8..16].copy_from_slice(&self.horizontal_walls.to_le_bytes());
        out[16..24].copy_from_slice(&self.vertical_walls.to_le_bytes());
        out
    }

    pub fn unpack_state(data: &[u8]) -> Result<Self, PositionError> {
        if data.len() != 24 {
            return Err(PositionError::PackedStateWrongLength(data.len()));
        }
        if data[0] != POSITION_SCHEMA_VERSION {
            return Err(PositionError::UnsupportedSchemaVersion(data[0]));
        }
        Ok(Self {
            player0_cell: data[1],
            player1_cell: data[2],
            player0_walls: data[3],
            player1_walls: data[4],
            side_to_move: data[5],
            horizontal_walls: u64::from_le_bytes(data[8..16].try_into().expect("slice len")),
            vertical_walls: u64::from_le_bytes(data[16..24].try_into().expect("slice len")),
        })
    }

    pub fn canonical_hash(&self) -> [u8; 32] {
        use sha2::{Digest, Sha256};
        let packed = self.packed_state();
        let digest = Sha256::digest(packed);
        digest.into()
    }

    pub fn fast_hash(&self) -> u64 {
        use blake2::{digest::consts::U8, Blake2b, Digest};
        let packed = self.packed_state();
        let digest = Blake2b::<U8>::digest(packed);
        u64::from_le_bytes(digest[..8].try_into().expect("blake2b-8"))
    }

    pub fn terminal_winner(&self) -> Option<u8> {
        let (p0_row, _) = cell_to_coords(self.player0_cell).ok()?;
        let (p1_row, _) = cell_to_coords(self.player1_cell).ok()?;
        if p0_row == 8 {
            return Some(0);
        }
        if p1_row == 0 {
            return Some(1);
        }
        None
    }
}

pub fn col_to_file(col: u8) -> Result<char, PositionError> {
    if col >= BOARD_SIZE {
        return Err(PositionError::ColumnOutOfRange(col as i32));
    }
    Ok((b'a' + col) as char)
}

pub fn file_to_col(file_char: char) -> Result<u8, PositionError> {
    if !('a'..='i').contains(&file_char) {
        return Err(PositionError::BadFile {
            file: file_char.to_string(),
        });
    }
    Ok(file_char as u8 - b'a')
}

pub fn cell_to_coords(cell: u8) -> Result<(u8, u8), PositionError> {
    if cell >= CELL_COUNT {
        return Err(PositionError::CellOutOfRange(cell));
    }
    Ok((cell / BOARD_SIZE, cell % BOARD_SIZE))
}

pub fn coords_to_cell(row: u8, col: u8) -> Result<u8, PositionError> {
    if row >= BOARD_SIZE || col >= BOARD_SIZE {
        return Err(PositionError::BadCoordinates { row, col });
    }
    Ok(row * BOARD_SIZE + col)
}

pub fn cell_to_notation(cell: u8) -> Result<String, PositionError> {
    let (row, col) = cell_to_coords(cell)?;
    Ok(format!("{}{}", col_to_file(col)?, row + 1))
}

pub fn notation_to_cell(text: &str) -> Result<u8, PositionError> {
    let bytes = text.as_bytes();
    if bytes.len() != 2 {
        return Err(PositionError::BadSquare {
            text: text.to_string(),
        });
    }
    let col = file_to_col(bytes[0] as char)?;
    if !b"123456789".contains(&bytes[1]) {
        return Err(PositionError::BadRank {
            text: text.to_string(),
        });
    }
    let row = bytes[1] - b'1';
    coords_to_cell(row, col)
}

pub fn wall_slot_to_notation(slot: u8, horizontal: bool) -> Result<String, PositionError> {
    if slot >= WALL_SLOT_COUNT {
        return Err(PositionError::WallSlotOutOfRange(slot));
    }
    let row = slot / WALL_GRID_SIZE;
    let col = slot % WALL_GRID_SIZE;
    let suffix = if horizontal { 'h' } else { 'v' };
    Ok(format!("{}{}{}", col_to_file(col)?, row + 1, suffix))
}

pub fn notation_to_wall_slot(text: &str) -> Result<(u8, bool), PositionError> {
    let bytes = text.as_bytes();
    if bytes.len() != 3 || (bytes[2] != b'h' && bytes[2] != b'v') {
        return Err(PositionError::BadWallMove {
            text: text.to_string(),
        });
    }
    let col = file_to_col(bytes[0] as char)?;
    if !b"12345678".contains(&bytes[1]) {
        return Err(PositionError::BadWallRank {
            text: text.to_string(),
        });
    }
    let row = bytes[1] - b'1';
    let slot = row * WALL_GRID_SIZE + col;
    Ok((slot, bytes[2] == b'h'))
}

pub fn wall_code_to_notation(code: u8) -> Result<String, PositionError> {
    if code < 64 {
        return wall_slot_to_notation(code, true);
    }
    if code < 128 {
        return wall_slot_to_notation(code - 64, false);
    }
    Err(PositionError::WallCodeOutOfRange(code))
}

pub fn wall_notation_to_code(text: &str) -> Result<u8, PositionError> {
    let (slot, horizontal) = notation_to_wall_slot(text)?;
    Ok(if horizontal { slot } else { 64 + slot })
}

pub fn direction_code_from_delta(dr: i8, dc: i8) -> Result<u8, PositionError> {
    let code = match (dr, dc) {
        (d, 0) if d > 0 => PAWN_NORTH,
        (d, 0) if d < 0 => PAWN_SOUTH,
        (0, d) if d > 0 => PAWN_EAST,
        (0, d) if d < 0 => PAWN_WEST,
        (d, c) if d > 0 && c > 0 => PAWN_NORTHEAST,
        (d, c) if d > 0 && c < 0 => PAWN_NORTHWEST,
        (d, c) if d < 0 && c > 0 => PAWN_SOUTHEAST,
        (d, c) if d < 0 && c < 0 => PAWN_SOUTHWEST,
        _ => {
            return Err(PositionError::BadDirectionDelta { dr, dc });
        }
    };
    Ok(code)
}

fn iter_wall_slots(mask: u64) -> impl Iterator<Item = u8> {
    let mut bits = mask;
    std::iter::from_fn(move || {
        if bits == 0 {
            return None;
        }
        let lsb = bits & bits.wrapping_neg();
        let slot = lsb.trailing_zeros() as u8;
        bits ^= lsb;
        Some(slot)
    })
}

fn validate_wall_mask(mask: u64, horizontal: bool) -> Result<(), PositionError> {
    let orientation = if horizontal {
        "horizontal"
    } else {
        "vertical"
    };
    for slot in iter_wall_slots(mask) {
        let row = slot / WALL_GRID_SIZE;
        let col = slot % WALL_GRID_SIZE;
        if row >= WALL_GRID_SIZE || col >= WALL_GRID_SIZE {
            return Err(PositionError::WallMaskSlotOutOfRange { orientation, slot });
        }
    }
    Ok(())
}

fn validate_cross_and_neighbor_collisions(
    horizontal_mask: u64,
    vertical_mask: u64,
) -> Result<(), PositionError> {
    let h_slots: HashSet<u8> = iter_wall_slots(horizontal_mask).collect();
    let v_slots: HashSet<u8> = iter_wall_slots(vertical_mask).collect();

    for slot in &h_slots {
        let col = slot % WALL_GRID_SIZE;
        if col > 0 && h_slots.contains(&(slot - 1)) {
            return Err(PositionError::HorizontalNeighborOverlap);
        }
        if col < 7 && h_slots.contains(&(slot + 1)) {
            return Err(PositionError::HorizontalNeighborOverlap);
        }
        if v_slots.contains(slot) {
            return Err(PositionError::CrossWallCollision);
        }
    }

    for slot in &v_slots {
        let row = slot / WALL_GRID_SIZE;
        if row > 0 && v_slots.contains(&(slot - 8)) {
            return Err(PositionError::VerticalNeighborOverlap);
        }
        if row < 7 && v_slots.contains(&(slot + 8)) {
            return Err(PositionError::VerticalNeighborOverlap);
        }
    }
    Ok(())
}

pub fn horizontal_wall_present(state: &PositionState, js_row: u8, col0: u8) -> bool {
    if js_row < 1 || js_row > 8 || col0 >= 8 {
        return false;
    }
    let bit = (js_row - 1) * 8 + col0;
    (state.horizontal_walls >> bit) & 1 != 0
}

pub fn vertical_wall_present(state: &PositionState, js_row: u8, col0: u8) -> bool {
    if js_row < 1 || js_row > 8 || col0 >= 8 {
        return false;
    }
    let bit = (js_row - 1) * 8 + col0;
    (state.vertical_walls >> bit) & 1 != 0
}

pub fn pawn_can_move(state: &PositionState, cell: u8, dr: i8, dc: i8) -> bool {
    let Ok((row, col)) = cell_to_coords(cell) else {
        return false;
    };
    let nr = row as i8 + dr;
    let nc = col as i8 + dc;
    if nr < 0 || nr > 8 || nc < 0 || nc > 8 {
        return false;
    }
    let js_from = row + 1;
    let js_to = (nr as u8) + 1;
    let col_u = col;
    let nc_u = nc as u8;
    match (dr, dc) {
        (1, 0) => {
            !horizontal_wall_present(state, js_from, col_u)
                && (col_u == 0 || !horizontal_wall_present(state, js_from, col_u - 1))
        }
        (-1, 0) => {
            !horizontal_wall_present(state, js_to, col_u)
                && (col_u == 0 || !horizontal_wall_present(state, js_to, col_u - 1))
        }
        (0, 1) => {
            !vertical_wall_present(state, js_from, col_u)
                && !vertical_wall_present(state, row, col_u)
        }
        (0, -1) => {
            !vertical_wall_present(state, js_to, nc_u) && !vertical_wall_present(state, nr as u8, nc_u)
        }
        _ => false,
    }
}

pub fn valid_pawn_destinations(state: &PositionState) -> Vec<u8> {
    let me = state.side_to_move;
    let current = state.player_cell(me);
    let opponent = state.player_cell(1 - me);
    let mut moves = Vec::new();

    for (dr, dc) in [(1i8, 0), (0, 1), (-1, 0), (0, -1)] {
        if !pawn_can_move(state, current, dr, dc) {
            continue;
        }
        let Ok((row, col)) = cell_to_coords(current) else {
            continue;
        };
        let step = coords_to_cell((row as i8 + dr) as u8, (col as i8 + dc) as u8).unwrap();
        if step != opponent {
            moves.push(step);
            continue;
        }
        if pawn_can_move(state, opponent, dr, dc) {
            let Ok((orow, ocol)) = cell_to_coords(opponent) else {
                continue;
            };
            moves.push(
                coords_to_cell((orow as i8 + dr) as u8, (ocol as i8 + dc) as u8).unwrap(),
            );
            continue;
        }
        let side_steps: &[(i8, i8)] = if dr != 0 {
            &[(0, -1), (0, 1)]
        } else {
            &[(1, 0), (-1, 0)]
        };
        for (sdr, sdc) in side_steps {
            if pawn_can_move(state, opponent, *sdr, *sdc) {
                let Ok((orow, ocol)) = cell_to_coords(opponent) else {
                    continue;
                };
                let diag =
                    coords_to_cell((orow as i8 + sdr) as u8, (ocol as i8 + sdc) as u8).unwrap();
                if diag != current {
                    moves.push(diag);
                }
            }
        }
    }
    moves
}

fn step_cell(cell: u8, dr: i8, dc: i8) -> Result<u8, PositionError> {
    let (row, col) = cell_to_coords(cell)?;
    coords_to_cell((row as i8 + dr) as u8, (col as i8 + dc) as u8)
}

fn flood_reachable(state: &PositionState, start_cell: u8) -> HashSet<u8> {
    let mut seen = HashSet::from([start_cell]);
    let mut queue = vec![start_cell];
    let mut head = 0usize;
    while head < queue.len() {
        let cell = queue[head];
        head += 1;
        for (dr, dc) in [(1i8, 0), (0, 1), (-1, 0), (0, -1)] {
            if !pawn_can_move(state, cell, dr, dc) {
                continue;
            }
            let Ok(nxt) = step_cell(cell, dr, dc) else {
                continue;
            };
            if seen.contains(&nxt) {
                continue;
            }
            seen.insert(nxt);
            queue.push(nxt);
        }
    }
    seen
}

fn goal_row_reachable(reachable: &HashSet<u8>, player: u8) -> bool {
    let goal_row = if player == 0 { 8 } else { 0 };
    (0..BOARD_SIZE).any(|col| reachable.contains(&coords_to_cell(goal_row, col).unwrap()))
}

pub fn both_players_reach_goals(state: &PositionState) -> bool {
    let white_reach = flood_reachable(state, state.player0_cell);
    if !goal_row_reachable(&white_reach, 0) {
        return false;
    }
    if white_reach.contains(&state.player1_cell) {
        return goal_row_reachable(&white_reach, 1);
    }
    let black_reach = flood_reachable(state, state.player1_cell);
    goal_row_reachable(&black_reach, 1)
}

pub fn collides_with_existing_wall(state: &PositionState, slot: u8, horizontal: bool) -> bool {
    let row = slot / WALL_GRID_SIZE;
    let col = slot % WALL_GRID_SIZE;
    if horizontal {
        if (state.horizontal_walls >> slot) & 1 != 0 || (state.vertical_walls >> slot) & 1 != 0 {
            return true;
        }
        if col > 0 && (state.horizontal_walls >> (slot - 1)) & 1 != 0 {
            return true;
        }
        if col < 7 && (state.horizontal_walls >> (slot + 1)) & 1 != 0 {
            return true;
        }
        return false;
    }
    if (state.vertical_walls >> slot) & 1 != 0 || (state.horizontal_walls >> slot) & 1 != 0 {
        return true;
    }
    if row > 0 && (state.vertical_walls >> (slot - 8)) & 1 != 0 {
        return true;
    }
    if row < 7 && (state.vertical_walls >> (slot + 8)) & 1 != 0 {
        return true;
    }
    false
}

fn touching_wall_candidates(
    slot: u8,
    horizontal: bool,
) -> (HashSet<u16>, HashSet<u16>, HashSet<u16>, (bool, bool)) {
    let row = slot / WALL_GRID_SIZE;
    let col = slot % WALL_GRID_SIZE;
    let mut side_a = HashSet::new();
    let mut side_b = HashSet::new();
    let mut middle = HashSet::new();

    if horizontal {
        side_a.insert(64 + u16::from(row) * 8 + u16::from(col));
        side_b.insert(64 + u16::from(row) * 8 + u16::from(col) + 1);
        if row < 7 {
            side_a.insert(64 + u16::from(row + 1) * 8 + u16::from(col));
            side_b.insert(64 + u16::from(row + 1) * 8 + u16::from(col) + 1);
        }
        if row > 0 {
            side_a.insert(64 + u16::from(row - 1) * 8 + u16::from(col));
            side_b.insert(64 + u16::from(row - 1) * 8 + u16::from(col) + 1);
        }
        side_a.insert(u16::from(if col > 0 { slot - 1 } else { slot }));
        side_b.insert(u16::from(if col < 7 { slot + 1 } else { slot }));
        if row < 7 {
            middle.insert(64 + u16::from(row + 1) * 8 + u16::from(col));
        }
        if row > 0 {
            middle.insert(64 + u16::from(row - 1) * 8 + u16::from(col));
        }
        return (side_a, side_b, middle, (col == 0, col == 7));
    }

    side_a.insert(u16::from(if row > 0 { slot - 8 } else { slot }));
    side_b.insert(u16::from(if row < 7 { slot + 8 } else { slot }));
    side_a.insert(u16::from(row) * 8 + u16::from(col));
    side_b.insert(
        u16::from(if row < 7 { row + 1 } else { row }) * 8 + u16::from(col),
    );
    if col > 0 {
        side_a.insert(u16::from(row) * 8 + u16::from(col - 1));
        side_b.insert(
            u16::from(if row < 7 { row + 1 } else { row }) * 8 + u16::from(col - 1),
        );
    }
    if col < 7 {
        side_a.insert(u16::from(row) * 8 + u16::from(col + 1));
        side_b.insert(
            u16::from(if row < 7 { row + 1 } else { row }) * 8 + u16::from(col + 1),
        );
    }
    if col > 0 {
        middle.insert(u16::from(row) * 8 + u16::from(col - 1));
    }
    if col < 7 {
        middle.insert(u16::from(row) * 8 + u16::from(col + 1));
    }
    (side_a, side_b, middle, (row == 7, row == 0))
}

pub fn can_wall_block(state: &PositionState, slot: u8, horizontal: bool) -> bool {
    let (side_a, side_b, middle, edges) = touching_wall_candidates(slot, horizontal);
    let occupied: u128 =
        u128::from(state.horizontal_walls) | (u128::from(state.vertical_walls) << 64);
    let has_side_a = edges.0
        || side_a
            .iter()
            .any(|&idx| (0..128).contains(&idx) && (occupied >> idx) & 1 != 0);
    let has_side_b = edges.1
        || side_b
            .iter()
            .any(|&idx| (0..128).contains(&idx) && (occupied >> idx) & 1 != 0);
    let has_middle = middle
        .iter()
        .any(|&idx| (0..128).contains(&idx) && (occupied >> idx) & 1 != 0);
    (has_side_a && has_side_b) || (has_side_a && has_middle) || (has_side_b && has_middle)
}

pub fn apply_move(
    state: &PositionState,
    move_notation: &str,
    assume_legal: bool,
) -> Result<PositionState, PositionError> {
    let move_notation = move_notation.trim().to_ascii_lowercase();
    if !assume_legal {
        encode_move(state, &move_notation)?;
    }
    if move_notation.len() == 3 {
        let (slot, horizontal) = notation_to_wall_slot(&move_notation)?;
        let (horizontal_walls, vertical_walls) = if horizontal {
            (state.horizontal_walls | (1u64 << slot), state.vertical_walls)
        } else {
            (state.horizontal_walls, state.vertical_walls | (1u64 << slot))
        };
        if state.side_to_move == 0 {
            return Ok(PositionState {
                player0_cell: state.player0_cell,
                player1_cell: state.player1_cell,
                player0_walls: state.player0_walls - 1,
                player1_walls: state.player1_walls,
                horizontal_walls,
                vertical_walls,
                side_to_move: 1,
            });
        }
        return Ok(PositionState {
            player0_cell: state.player0_cell,
            player1_cell: state.player1_cell,
            player0_walls: state.player0_walls,
            player1_walls: state.player1_walls - 1,
            horizontal_walls,
            vertical_walls,
            side_to_move: 0,
        });
    }
    let target = notation_to_cell(&move_notation)?;
    if state.side_to_move == 0 {
        Ok(PositionState {
            player0_cell: target,
            player1_cell: state.player1_cell,
            player0_walls: state.player0_walls,
            player1_walls: state.player1_walls,
            horizontal_walls: state.horizontal_walls,
            vertical_walls: state.vertical_walls,
            side_to_move: 1,
        })
    } else {
        Ok(PositionState {
            player0_cell: state.player0_cell,
            player1_cell: target,
            player0_walls: state.player0_walls,
            player1_walls: state.player1_walls,
            horizontal_walls: state.horizontal_walls,
            vertical_walls: state.vertical_walls,
            side_to_move: 0,
        })
    }
}

pub fn is_valid_wall_placement(
    state: &PositionState,
    slot: u8,
    horizontal: bool,
) -> Result<bool, PositionError> {
    if state.player_walls_left(state.side_to_move) == 0 {
        return Ok(false);
    }
    if collides_with_existing_wall(state, slot, horizontal) {
        return Ok(false);
    }
    if !can_wall_block(state, slot, horizontal) {
        return Ok(true);
    }
    let move_notation = wall_slot_to_notation(slot, horizontal)?;
    let next_state = apply_move(state, &move_notation, true)?;
    Ok(both_players_reach_goals(&next_state))
}

pub fn encode_move(state: &PositionState, move_notation: &str) -> Result<u8, PositionError> {
    let move_notation = move_notation.trim().to_ascii_lowercase();
    if move_notation.len() == 3 {
        let code = wall_notation_to_code(&move_notation)?;
        let horizontal = code < 64;
        let slot = if horizontal { code } else { code - 64 };
        if !is_valid_wall_placement(state, slot, horizontal)? {
            return Err(PositionError::IllegalWallMove {
                notation: move_notation,
            });
        }
        return Ok(code);
    }
    if move_notation.len() != 2 {
        return Err(PositionError::BadMoveNotation {
            notation: move_notation,
        });
    }
    let target = notation_to_cell(&move_notation)?;
    let legal = valid_pawn_destinations(state);
    if !legal.contains(&target) {
        return Err(PositionError::IllegalPawnMove {
            notation: move_notation,
        });
    }
    let (from_row, from_col) = cell_to_coords(state.current_cell())?;
    let (to_row, to_col) = cell_to_coords(target)?;
    direction_code_from_delta(
        to_row as i8 - from_row as i8,
        to_col as i8 - from_col as i8,
    )
}

pub fn alpha_action_to_move_u8(state: &PositionState, action: u16) -> Result<u8, PositionError> {
    if action <= 80 {
        let move_notation = cell_to_notation(action as u8)?;
        return encode_move(state, &move_notation);
    }
    if (81..=144).contains(&action) {
        return Ok((action - 81) as u8);
    }
    if (145..=208).contains(&action) {
        return Ok(64 + (action - 145) as u8);
    }
    Err(PositionError::UnsupportedAlphaAction(action))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_packed_start_position_matches_expected_length() {
        let state = PositionState::initial();
        let packed = state.packed_state();
        assert_eq!(packed.len(), 24);
        assert_eq!(
            packed,
            [
                0x01, 0x04, 0x4c, 0x0a, 0x0a, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            ]
        );

        let roundtrip = PositionState::unpack_state(&packed).unwrap();
        assert_eq!(roundtrip, state);
        state.validate(true).unwrap();
    }

    #[test]
    fn test_hashes_match_python_start_position() {
        let state = PositionState::initial();
        let canonical = state.canonical_hash();
        assert_eq!(
            hex::encode(canonical),
            "ad93e3e3204f2474f38efc04a48db23925525f02a5c6b73a5da86e4b934d62aa"
        );
        assert_eq!(state.fast_hash(), 13166396488446335976);
    }

    #[test]
    fn test_alpha_action_pawn_and_wall_ranges() {
        let state = PositionState::initial();
        // action 76 = cell e9 (player1 start) — illegal for side-to-move 0
        assert!(alpha_action_to_move_u8(&state, 76).is_err());
        // horizontal wall slot 0 -> action 81 -> move code 0
        assert_eq!(alpha_action_to_move_u8(&state, 81).unwrap(), 0);
        // vertical wall slot 0 -> action 145 -> move code 64
        assert_eq!(alpha_action_to_move_u8(&state, 145).unwrap(), 64);
    }
}
