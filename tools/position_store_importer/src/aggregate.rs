//! In-memory batch aggregation before SQLite merge.

use std::collections::BTreeMap;

use crate::friend::FriendRecordOk;
use crate::sidecar::SidecarRef;

/// Sentinel `value_bits` when teacher root value is absent.
pub const VALUE_BITS_NONE: u64 = 0xFFFF_FFFF_FFFF_FFFF;

/// Identity for one teacher label aggregate row.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize)]
pub struct LabelKey {
    pub canonical_hash: [u8; 32],
    pub source: String,
    pub value_bits: u64,
    pub policy_hash: Option<String>,
}

impl LabelKey {
    pub fn new(
        canonical_hash: [u8; 32],
        source: impl Into<String>,
        root_value: Option<f64>,
        policy_hash: Option<String>,
    ) -> Self {
        Self {
            canonical_hash,
            source: source.into(),
            value_bits: root_value.map(f64::to_bits).unwrap_or(VALUE_BITS_NONE),
            policy_hash,
        }
    }
}

/// One aggregated teacher label produced from many source rows.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AggregatedLabel {
    pub key: LabelKey,
    pub packed_state: [u8; 24],
    pub observation_count: u64,
    pub best_move_u8: Option<u8>,
    pub root_value: Option<f64>,
    pub outcome: Option<f64>,
    pub sidecar_ref: Option<SidecarRef>,
}

/// Per-batch aggregation accumulator (deterministic key order via `BTreeMap`).
#[derive(Debug, Default)]
pub struct BatchAgg {
    pub labels: BTreeMap<LabelKey, AggregatedLabel>,
}

impl BatchAgg {
    pub fn new() -> Self {
        Self::default()
    }

    /// Insert or merge one parsed friend record into this batch.
    pub fn ingest_friend(
        &mut self,
        record: &FriendRecordOk,
        source: &str,
        sidecar_ref: Option<SidecarRef>,
    ) {
        let key = LabelKey::new(
            record.state.canonical_hash(),
            source,
            record.root_value,
            record.policy_hash.clone(),
        );
        let entry = AggregatedLabel {
            key: key.clone(),
            packed_state: record.state.packed_state(),
            observation_count: 1,
            best_move_u8: record.best_move_u8,
            root_value: record.root_value,
            outcome: record.outcome,
            sidecar_ref,
        };
        merge_label_entry(&mut self.labels, entry);
    }
}

/// Merge one batch into a cross-batch accumulator in deterministic key order.
pub fn merge_batch_into(acc: &mut BTreeMap<LabelKey, AggregatedLabel>, batch: BatchAgg) {
    for (_key, label) in batch.labels {
        merge_label_entry(acc, label);
    }
}

fn merge_label_entry(acc: &mut BTreeMap<LabelKey, AggregatedLabel>, incoming: AggregatedLabel) {
    acc.entry(incoming.key.clone())
        .and_modify(|existing| merge_into(existing, &incoming))
        .or_insert(incoming);
}

fn merge_into(existing: &mut AggregatedLabel, incoming: &AggregatedLabel) {
    debug_assert_eq!(existing.key, incoming.key);
    existing.observation_count += incoming.observation_count;
    existing.best_move_u8 = pick_optional_u8(existing.best_move_u8, incoming.best_move_u8);
    existing.sidecar_ref = pick_sidecar_ref(
        existing.sidecar_ref.as_ref(),
        incoming.sidecar_ref.as_ref(),
    );
    // Scalar fields are identical for the same LabelKey; keep the first seen.
}

fn pick_optional_u8(a: Option<u8>, b: Option<u8>) -> Option<u8> {
    match (a, b) {
        (None, b) => b,
        (a, None) => a,
        (Some(x), Some(y)) if x == y => Some(x),
        (Some(x), Some(y)) => Some(x.min(y)),
    }
}

fn pick_sidecar_ref(a: Option<&SidecarRef>, b: Option<&SidecarRef>) -> Option<SidecarRef> {
    match (a, b) {
        (None, None) => None,
        (Some(r), None) | (None, Some(r)) => Some(r.clone()),
        (Some(x), Some(y)) => {
            if x == y {
                Some(x.clone())
            } else {
                // Same policy hash implies equivalent payload; prefer lexicographically smaller path.
                if x.path <= y.path {
                    Some(x.clone())
                } else {
                    Some(y.clone())
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::position_state::PositionState;

    fn sample_record(root: f64) -> FriendRecordOk {
        FriendRecordOk {
            state: PositionState::initial(),
            policy_actions: vec![4],
            policy_values: vec![1.0],
            move_codes_u8: vec![128],
            policy_hash: Some("abc".into()),
            root_value: Some(root),
            outcome: Some(1.0),
            best_move_u8: Some(128),
        }
    }

    #[test]
    fn batch_merges_duplicate_keys() {
        let mut batch = BatchAgg::new();
        batch.ingest_friend(&sample_record(0.5), "friend_selfplay:iter_000001", None);
        batch.ingest_friend(&sample_record(0.5), "friend_selfplay:iter_000001", None);
        assert_eq!(batch.labels.len(), 1);
        let label = batch.labels.values().next().unwrap();
        assert_eq!(label.observation_count, 2);
    }

    #[test]
    fn merge_batch_into_is_deterministic() {
        let mut acc = BTreeMap::new();
        let mut b1 = BatchAgg::new();
        b1.ingest_friend(&sample_record(0.5), "cohort", None);
        let mut b2 = BatchAgg::new();
        b2.ingest_friend(&sample_record(0.5), "cohort", None);
        merge_batch_into(&mut acc, b1);
        merge_batch_into(&mut acc, b2);
        assert_eq!(acc.len(), 1);
        assert_eq!(acc.values().next().unwrap().observation_count, 2);
    }
}
