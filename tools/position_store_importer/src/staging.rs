//! Compact binary staging records for bulk-load pipeline (no SQLite in inner loop).
//!
//! Magic headers:
//!   TIPOS1 — position staging stream
//!   TIPLB1 — label staging stream

pub const POS_MAGIC: &[u8; 6] = b"TIPOS1";
pub const LABEL_MAGIC: &[u8; 6] = b"TIPLB1";
pub const STAGING_VERSION: u16 = 1;

/// One deduplicated position row before global merge.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct PositionStageRow {
    pub canonical_hash: [u8; 32],
    pub packed_state: [u8; 24],
    pub observation_count: u64,
    pub source_flags: u8,
}

impl PositionStageRow {
    pub fn sort_key(&self) -> ([u8; 32], [u8; 24]) {
        (self.canonical_hash, self.packed_state)
    }

    pub fn encode(&self, out: &mut Vec<u8>) {
        out.extend_from_slice(&self.canonical_hash);
        out.extend_from_slice(&self.packed_state);
        out.extend_from_slice(&self.observation_count.to_le_bytes());
        out.push(self.source_flags);
    }

    pub fn decode(data: &[u8]) -> Option<Self> {
        if data.len() < 32 + 24 + 8 + 1 {
            return None;
        }
        let mut canonical_hash = [0u8; 32];
        canonical_hash.copy_from_slice(&data[0..32]);
        let mut packed_state = [0u8; 24];
        packed_state.copy_from_slice(&data[32..56]);
        let observation_count = u64::from_le_bytes(data[56..64].try_into().ok()?);
        let source_flags = data[64];
        Some(Self {
            canonical_hash,
            packed_state,
            observation_count,
            source_flags,
        })
    }

    pub const ENCODED_LEN: usize = 32 + 24 + 8 + 1;
}

/// Collapse sorted-compatible rows in memory (caller must sort first).
pub fn collapse_positions(mut rows: Vec<PositionStageRow>) -> Vec<PositionStageRow> {
    rows.sort_by(|a, b| a.sort_key().cmp(&b.sort_key()));
    let mut out: Vec<PositionStageRow> = Vec::new();
    for row in rows {
        if let Some(last) = out.last_mut() {
            if last.canonical_hash == row.canonical_hash && last.packed_state == row.packed_state {
                last.observation_count += row.observation_count;
                continue;
            }
        }
        out.push(row);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn position_roundtrip_and_collapse() {
        let a = PositionStageRow {
            canonical_hash: [1u8; 32],
            packed_state: [2u8; 24],
            observation_count: 3,
            source_flags: 2,
        };
        let mut buf = Vec::new();
        a.encode(&mut buf);
        let b = PositionStageRow::decode(&buf).unwrap();
        assert_eq!(a, b);
        let collapsed = collapse_positions(vec![a.clone(), a]);
        assert_eq!(collapsed.len(), 1);
        assert_eq!(collapsed[0].observation_count, 6);
    }
}
