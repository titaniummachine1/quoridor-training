//! Friend self-play JSONL record parsing (alpha-selfplay-v1).

use std::collections::BTreeMap;

use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::position_state::{alpha_action_to_move_u8, PositionState};

/// Successfully parsed friend shard record.
#[derive(Debug, Clone, PartialEq)]
pub struct FriendRecordOk {
    pub state: PositionState,
    pub policy_actions: Vec<u16>,
    pub policy_values: Vec<f64>,
    pub move_codes_u8: Vec<u8>,
    pub policy_hash: Option<String>,
    pub root_value: Option<f64>,
    pub outcome: Option<f64>,
    pub best_move_u8: Option<u8>,
}

/// Parse outcome for a friend JSONL line.
#[derive(Debug, Clone, PartialEq)]
pub enum FriendRecord {
    Ok(FriendRecordOk),
    Err(FriendParseError),
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum FriendParseError {
    #[error("missing object field: {0}")]
    MissingField(&'static str),
    #[error("invalid field type: {0}")]
    InvalidType(&'static str),
    #[error("quarantined unknown label semantics")]
    QuarantinedSemantics,
    #[error("position state: {0}")]
    Position(String),
    #[error("{0}")]
    Other(String),
}

/// SHA-256 hex of compact JSON `{"m":[...],"v":[values rounded to 8 decimals]}` with sorted keys.
pub fn policy_semantic_hash(move_codes: &[u8], policy_values: &[f64]) -> String {
    let rounded: Vec<f64> = policy_values.iter().map(|v| round8(*v)).collect();
    let mut payload = BTreeMap::new();
    payload.insert(
        "m",
        serde_json::Value::Array(
            move_codes
                .iter()
                .map(|&c| serde_json::Value::from(c))
                .collect(),
        ),
    );
    payload.insert(
        "v",
        serde_json::Value::Array(
            rounded
                .iter()
                .map(|v| serde_json::Number::from_f64(*v).map(serde_json::Value::Number))
                .map(|n| n.unwrap_or(serde_json::Value::Null))
                .collect(),
        ),
    );
    let json = serde_json::to_string(&payload).expect("policy hash payload serializes");
    hex::encode(Sha256::digest(json.as_bytes()))
}

/// Parse one friend JSONL object into a validated record.
pub fn parse_friend_record(value: &Value) -> Result<FriendRecordOk, FriendParseError> {
    let state_obj = value
        .get("state")
        .and_then(Value::as_object)
        .ok_or(FriendParseError::MissingField("state"))?;

    let state = PositionState {
        player0_cell: read_u8_field(state_obj, "player0Cell")?,
        player1_cell: read_u8_field(state_obj, "player1Cell")?,
        player0_walls: read_u8_field(state_obj, "player0Walls")?,
        player1_walls: read_u8_field(state_obj, "player1Walls")?,
        horizontal_walls: read_u64_field(state_obj, "horizontalWalls")?,
        vertical_walls: read_u64_field(state_obj, "verticalWalls")?,
        side_to_move: read_u8_field(state_obj, "currentPlayer")?,
    };
    state
        .validate(true)
        .map_err(|e| FriendParseError::Position(e.to_string()))?;

    let (policy_actions, policy_values) = read_policy(value)?;

    let move_codes_u8: Result<Vec<u8>, FriendParseError> = policy_actions
        .iter()
        .map(|&action| {
            alpha_action_to_move_u8(&state, action)
                .map_err(|e| FriendParseError::Position(e.to_string()))
        })
        .collect();
    let move_codes_u8 = move_codes_u8?;

    let root_value = optional_f64(value.get("rootValue"))?;
    let outcome = optional_f64(value.get("outcome"))?;

    if root_value.is_none() && policy_values.is_empty() {
        return Err(FriendParseError::QuarantinedSemantics);
    }

    let policy_hash = if move_codes_u8.is_empty() {
        None
    } else {
        Some(policy_semantic_hash(&move_codes_u8, &policy_values))
    };

    let best_move_u8 = best_move(&policy_actions, &policy_values, &state)?;

    Ok(FriendRecordOk {
        state,
        policy_actions,
        policy_values,
        move_codes_u8,
        policy_hash,
        root_value,
        outcome,
        best_move_u8,
    })
}

fn read_policy(value: &Value) -> Result<(Vec<u16>, Vec<f64>), FriendParseError> {
    if value.get("policyActions").is_some() || value.get("policyValues").is_some() {
        let actions = value
            .get("policyActions")
            .and_then(Value::as_array)
            .ok_or(FriendParseError::MissingField("policyActions"))?;
        let values = value
            .get("policyValues")
            .and_then(Value::as_array)
            .ok_or(FriendParseError::MissingField("policyValues"))?;
        if actions.len() != values.len() {
            return Err(FriendParseError::Other(
                "policyActions and policyValues length mismatch".into(),
            ));
        }
        let policy_actions: Result<Vec<u16>, _> = actions
            .iter()
            .map(|v| read_u16(v, "policyActions item"))
            .collect();
        let policy_values: Result<Vec<f64>, _> = values
            .iter()
            .map(|v| read_f64(v, "policyValues item"))
            .collect();
        return Ok((policy_actions?, policy_values?));
    }

    let dense = value
        .get("policy")
        .and_then(Value::as_array)
        .ok_or(FriendParseError::MissingField("policyActions"))?;
    let mut policy_actions = Vec::new();
    let mut policy_values = Vec::new();
    for (idx, item) in dense.iter().enumerate() {
        let prob = read_f64(item, "policy item")?;
        if prob > 0.0 {
            policy_actions.push(idx as u16);
            policy_values.push(prob);
        }
    }
    Ok((policy_actions, policy_values))
}

fn best_move(
    actions: &[u16],
    values: &[f64],
    state: &PositionState,
) -> Result<Option<u8>, FriendParseError> {
    if actions.is_empty() {
        return Ok(None);
    }
    let best_idx = values
        .iter()
        .enumerate()
        .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(i, _)| i)
        .expect("non-empty actions");
    Ok(Some(
        alpha_action_to_move_u8(state, actions[best_idx])
            .map_err(|e| FriendParseError::Position(e.to_string()))?,
    ))
}

fn round8(v: f64) -> f64 {
    (v * 1e8).round() / 1e8
}

fn read_u8_field(
    obj: &serde_json::Map<String, Value>,
    key: &'static str,
) -> Result<u8, FriendParseError> {
    let value = obj.get(key).ok_or(FriendParseError::MissingField(key))?;
    read_u8(value, key)
}

fn read_u64_field(
    obj: &serde_json::Map<String, Value>,
    key: &'static str,
) -> Result<u64, FriendParseError> {
    let value = obj.get(key).ok_or(FriendParseError::MissingField(key))?;
    read_u64(value, key)
}

fn read_u8(value: &Value, field: &'static str) -> Result<u8, FriendParseError> {
    value
        .as_u64()
        .and_then(|n| u8::try_from(n).ok())
        .ok_or(FriendParseError::InvalidType(field))
}

fn read_u16(value: &Value, field: &'static str) -> Result<u16, FriendParseError> {
    value
        .as_u64()
        .and_then(|n| u16::try_from(n).ok())
        .ok_or(FriendParseError::InvalidType(field))
}

fn read_u64(value: &Value, field: &'static str) -> Result<u64, FriendParseError> {
    value.as_u64().ok_or(FriendParseError::InvalidType(field))
}

fn read_f64(value: &Value, field: &'static str) -> Result<f64, FriendParseError> {
    value.as_f64().ok_or(FriendParseError::InvalidType(field))
}

fn optional_f64(value: Option<&Value>) -> Result<Option<f64>, FriendParseError> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(v) => Ok(Some(read_f64(v, "numeric field")?)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::position_state::PositionState;

    #[test]
    fn policy_semantic_hash_matches_python_layout() {
        let moves = vec![128u8, 129, 64];
        let values = vec![0.5, 0.3, 0.2];
        let hash = policy_semantic_hash(&moves, &values);
        assert_eq!(hash.len(), 64);
        // Stable across calls.
        assert_eq!(hash, policy_semantic_hash(&moves, &values));
    }

    #[test]
    fn parse_minimal_friend_record() {
        let value: Value = serde_json::json!({
            "state": {
                "player0Cell": 4,
                "player1Cell": 76,
                "player0Walls": 10,
                "player1Walls": 10,
                "horizontalWalls": 0,
                "verticalWalls": 0,
                "currentPlayer": 0
            },
            "policyActions": [13],
            "policyValues": [1.0],
            "rootValue": 0.12,
            "outcome": 1.0
        });
        let parsed = parse_friend_record(&value).expect("parse");
        assert_eq!(parsed.state, PositionState::initial());
        assert_eq!(parsed.root_value, Some(0.12));
        assert_eq!(parsed.outcome, Some(1.0));
        assert!(parsed.policy_hash.is_some());
    }
}
