//! SQLite single-writer merge for the teacher store.

use std::collections::HashMap;
use std::path::Path;

use anyhow::{bail, Context, Result};
use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::json;

use crate::aggregate::{AggregatedLabel, LabelKey};
use crate::position_state::PositionState;
use crate::progress::PhaseMetrics;
use crate::staging::PositionStageRow;

pub const LABEL_SCHEMA_VERSION: i64 = 1;
pub const POSITION_SCHEMA_VERSION: i64 = 1;
pub const DATABASE_SCHEMA_VERSION: i64 = 1;

const SCHEMA_SQL: &str = include_str!("schema.sql");

#[derive(Debug, Default, Clone)]
pub struct WriterStats {
    pub positions_inserted: u64,
    pub positions_reused: u64,
    pub labels_inserted: u64,
    pub labels_merged: u64,
    pub observations_bumped: u64,
}

pub struct TeacherWriter {
    conn: Connection,
    position_cache: HashMap<[u8; 32], i64>,
}

impl TeacherWriter {
    pub fn open(db_path: &Path, sidecar_dir: &Path) -> Result<Self> {
        if db_path
            .file_name()
            .and_then(|n| n.to_str())
            .map(|n| n.contains("game_store"))
            .unwrap_or(false)
        {
            bail!("refusing friend import into game_store.db — use position_teacher_store.db");
        }
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::create_dir_all(sidecar_dir)?;
        let conn = Connection::open(db_path).context("open teacher db")?;
        conn.execute_batch(SCHEMA_SQL)?;
        conn.execute("PRAGMA synchronous=NORMAL", [])?;
        conn.execute("PRAGMA cache_size=-65536", [])?;
        ensure_teacher_metadata(&conn)?;
        reject_game_store_role(&conn)?;
        Ok(Self {
            conn,
            position_cache: HashMap::new(),
        })
    }

    pub fn shard_complete(&self, source_hash: &str, format: &str) -> Result<bool> {
        let row: Option<String> = self
            .conn
            .query_row(
                "SELECT status FROM imports WHERE source_hash=? AND format=?",
                params![source_hash, format],
                |r| r.get(0),
            )
            .optional()?;
        Ok(row.as_deref() == Some("completed"))
    }

    pub fn reset_running_import(&self, source_hash: &str, format: &str) -> Result<()> {
        self.conn.execute(
            "UPDATE imports SET status='failed', completed_at=? WHERE source_hash=? AND format=? AND status='running'",
            params![Utc::now().to_rfc3339(), source_hash, format],
        )?;
        Ok(())
    }

    pub fn begin_shard_import(
        &self,
        source_path: &str,
        source_hash: &str,
        format: &str,
    ) -> Result<i64> {
        if self.shard_complete(source_hash, format)? {
            bail!("shard already imported: {source_path}");
        }
        let now = Utc::now().to_rfc3339();
        let existing: Option<(i64, String)> = self
            .conn
            .query_row(
                "SELECT import_id, status FROM imports WHERE source_hash=? AND format=?",
                params![source_hash, format],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        if let Some((id, status)) = existing {
            if status == "completed" {
                bail!("shard already imported");
            }
            self.conn.execute(
                "UPDATE imports SET source_path=?, started_at=?, completed_at=NULL, status='running', \
                 record_count=0, accepted_count=0, rejected_count=0, duplicate_count=0 WHERE import_id=?",
                params![source_path, now, id],
            )?;
            return Ok(id);
        }
        self.conn.execute(
            "INSERT INTO imports(source_path, source_hash, format, started_at, importer_version, status) \
             VALUES(?, ?, ?, ?, ?, 'running')",
            params![source_path, source_hash, format, now, crate::IMPORTER_VERSION],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn finish_shard_import(
        &self,
        import_id: i64,
        record_count: i64,
        accepted: i64,
        rejected: i64,
        duplicate: i64,
    ) -> Result<()> {
        self.conn.execute(
            "UPDATE imports SET record_count=?, accepted_count=?, rejected_count=?, duplicate_count=?, \
             completed_at=?, status='completed' WHERE import_id=?",
            params![
                record_count,
                accepted,
                rejected,
                duplicate,
                Utc::now().to_rfc3339(),
                import_id
            ],
        )?;
        Ok(())
    }

    pub fn commit_batch(
        &mut self,
        labels: &[(LabelKey, AggregatedLabel)],
        cohort: &str,
        phases: Option<&PhaseMetrics>,
    ) -> Result<WriterStats> {
        let mut stats = WriterStats::default();
        let tx = self.conn.unchecked_transaction()?;
        for (_key, label) in labels {
            let pos_start = std::time::Instant::now();
            let state = PositionState::unpack_state(&label.packed_state)?;
            let canonical = label.key.canonical_hash;
            let packed = label.packed_state;
            let pos_id = if let Some(&id) = self.position_cache.get(&canonical) {
                stats.positions_reused += 1;
                tx.execute(
                    "UPDATE positions SET total_visits=total_visits+?, last_seen_at=? WHERE position_id=?",
                    params![label.observation_count, Utc::now().to_rfc3339(), id],
                )?;
                id
            } else if let Some(id) = find_position_id(&tx, &canonical, &packed)? {
                stats.positions_reused += 1;
                self.position_cache.insert(canonical, id);
                tx.execute(
                    "UPDATE positions SET total_visits=total_visits+?, last_seen_at=? WHERE position_id=?",
                    params![label.observation_count, Utc::now().to_rfc3339(), id],
                )?;
                id
            } else {
                stats.positions_inserted += 1;
                tx.execute(
                    "INSERT INTO positions(canonical_hash, fast_hash, packed_state, side_to_move, ply_min_seen, \
                     ply_max_seen, first_seen_at, last_seen_at, total_visits, source_flags, schema_version) \
                     VALUES(?, ?, ?, ?, NULL, NULL, ?, ?, ?, 2, ?)",
                    params![
                        canonical.as_slice(),
                        state.fast_hash() as i64,
                        packed.as_slice(),
                        state.side_to_move,
                        Utc::now().to_rfc3339(),
                        Utc::now().to_rfc3339(),
                        label.observation_count,
                        POSITION_SCHEMA_VERSION,
                    ],
                )?;
                let id = tx.last_insert_rowid();
                self.position_cache.insert(canonical, id);
                id
            };

            if let Some(p) = phases {
                p.add_us(&p.sqlite_position_us, pos_start.elapsed());
            }

            let obs_start = std::time::Instant::now();
            bump_observation(
                &tx,
                pos_id,
                cohort,
                label.outcome,
                label.observation_count,
                &mut stats,
            )?;
            if let Some(p) = phases {
                p.add_us(&p.sqlite_observation_us, obs_start.elapsed());
            }

            let label_start = std::time::Instant::now();
            if let Some(existing) = find_label_id(&tx, pos_id, &label.key)? {
                tx.execute(
                    "UPDATE labels SET payload_json=? WHERE label_id=?",
                    params![payload_json(&label.key, label, cohort)?, existing],
                )?;
                stats.labels_merged += 1;
            } else {
                tx.execute(
                    "INSERT INTO labels(position_id, label_type, value, best_move_u8, label_schema_version, \
                     created_at, quality_rank, source, payload_json) VALUES(?, 'teacher_value', ?, ?, ?, ?, 0, ?, ?)",
                    params![
                        pos_id,
                        label.root_value,
                        label.best_move_u8,
                        LABEL_SCHEMA_VERSION,
                        Utc::now().to_rfc3339(),
                        &label.key.source,
                        payload_json(&label.key, label, cohort)?,
                    ],
                )?;
                stats.labels_inserted += 1;
            }
            if let Some(p) = phases {
                p.add_us(&p.sqlite_label_us, label_start.elapsed());
            }
        }
        let commit_phase = std::time::Instant::now();
        tx.commit()?;
        if let Some(p) = phases {
            p.add_us(&p.sqlite_commit_us, commit_phase.elapsed());
            p.writer_rows
                .fetch_add(labels.len() as u64, std::sync::atomic::Ordering::Relaxed);
        }
        Ok(stats)
    }

    /// Fresh-database bulk load after staging merge (no per-row SQLite lookups).
    pub fn bulk_load(
        &mut self,
        positions: &[PositionStageRow],
        labels: &[AggregatedLabel],
        phases: Option<&PhaseMetrics>,
    ) -> Result<WriterStats> {
        let mut stats = WriterStats::default();
        let tx = self.conn.unchecked_transaction()?;
        let mut pos_ids: HashMap<([u8; 32], [u8; 24]), i64> = HashMap::with_capacity(positions.len());

        for row in positions {
            let pos_start = std::time::Instant::now();
            let state = PositionState::unpack_state(&row.packed_state)?;
            tx.execute(
                "INSERT INTO positions(canonical_hash, fast_hash, packed_state, side_to_move, ply_min_seen, \
                 ply_max_seen, first_seen_at, last_seen_at, total_visits, source_flags, schema_version) \
                 VALUES(?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)",
                params![
                    row.canonical_hash.as_slice(),
                    state.fast_hash() as i64,
                    row.packed_state.as_slice(),
                    state.side_to_move,
                    Utc::now().to_rfc3339(),
                    Utc::now().to_rfc3339(),
                    row.observation_count,
                    row.source_flags,
                    POSITION_SCHEMA_VERSION,
                ],
            )?;
            let id = tx.last_insert_rowid();
            pos_ids.insert((row.canonical_hash, row.packed_state), id);
            self.position_cache.insert(row.canonical_hash, id);
            stats.positions_inserted += 1;
            if let Some(p) = phases {
                p.add_us(&p.sqlite_position_us, pos_start.elapsed());
            }
        }

        for label in labels {
            let pos_id = *pos_ids
                .get(&(label.key.canonical_hash, label.packed_state))
                .with_context(|| "bulk_load label missing position")?;

            let obs_start = std::time::Instant::now();
            bump_observation(
                &tx,
                pos_id,
                &label.key.source,
                label.outcome,
                label.observation_count,
                &mut stats,
            )?;
            if let Some(p) = phases {
                p.add_us(&p.sqlite_observation_us, obs_start.elapsed());
            }

            let label_start = std::time::Instant::now();
            tx.execute(
                "INSERT INTO labels(position_id, label_type, value, best_move_u8, label_schema_version, \
                 created_at, quality_rank, source, payload_json) VALUES(?, 'teacher_value', ?, ?, ?, ?, 0, ?, ?)",
                params![
                    pos_id,
                    label.root_value,
                    label.best_move_u8,
                    LABEL_SCHEMA_VERSION,
                    Utc::now().to_rfc3339(),
                    &label.key.source,
                    payload_json(&label.key, label, &label.key.source)?,
                ],
            )?;
            stats.labels_inserted += 1;
            if let Some(p) = phases {
                p.add_us(&p.sqlite_label_us, label_start.elapsed());
            }
        }

        let commit_phase = std::time::Instant::now();
        tx.commit()?;
        if let Some(p) = phases {
            p.add_us(&p.sqlite_commit_us, commit_phase.elapsed());
            p.writer_rows
                .fetch_add(labels.len() as u64, std::sync::atomic::Ordering::Relaxed);
        }
        Ok(stats)
    }

    pub fn wal_checkpoint(&self, phases: Option<&PhaseMetrics>) -> Result<()> {
        let t0 = std::time::Instant::now();
        self.conn.execute_batch("PRAGMA wal_checkpoint(PASSIVE);")?;
        if let Some(p) = phases {
            p.add_us(&p.wal_checkpoint_us, t0.elapsed());
        }
        Ok(())
    }

    pub fn integrity_check(&self) -> Result<String> {
        let v: String = self
            .conn
            .query_row("PRAGMA integrity_check", [], |r| r.get(0))?;
        Ok(v)
    }

    pub fn counts(&self) -> Result<(i64, i64, i64, i64)> {
        let positions: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM positions", [], |r| r.get(0))?;
        let labels: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM labels", [], |r| r.get(0))?;
        let games: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM games", [], |r| r.get(0))?;
        let edges: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM edges", [], |r| r.get(0))?;
        Ok((positions, labels, games, edges))
    }
}

fn ensure_teacher_metadata(conn: &Connection) -> Result<()> {
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES('store_kind', 'teacher')",
        [],
    )?;
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES('store_role', 'teacher')",
        [],
    )?;
    conn.execute(
        "INSERT OR REPLACE INTO store_metadata(key, value) VALUES('importer', ?)",
        params![format!("rust-micropool-{}", crate::IMPORTER_VERSION)],
    )?;
    Ok(())
}

fn reject_game_store_role(conn: &Connection) -> Result<()> {
    let role: Option<String> = conn
        .query_row(
            "SELECT value FROM store_metadata WHERE key='store_kind'",
            [],
            |r| r.get(0),
        )
        .optional()?;
    if role.as_deref() == Some("game") {
        bail!("database store_kind=game — friend import forbidden");
    }
    let games: i64 = conn.query_row("SELECT COUNT(*) FROM games", [], |r| r.get(0))?;
    if games > 0 {
        bail!("teacher import refused: database already contains {games} games");
    }
    Ok(())
}

fn find_position_id(
    conn: &Connection,
    canonical: &[u8; 32],
    packed: &[u8; 24],
) -> Result<Option<i64>> {
    conn.query_row(
        "SELECT position_id FROM positions WHERE canonical_hash=? AND packed_state=?",
        params![canonical.as_slice(), packed.as_slice()],
        |r| r.get(0),
    )
    .optional()
    .map_err(Into::into)
}

fn find_label_id(conn: &Connection, position_id: i64, key: &LabelKey) -> Result<Option<i64>> {
    let value: Option<f64> = if key.value_bits == crate::aggregate::VALUE_BITS_NONE {
        None
    } else {
        Some(f64::from_bits(key.value_bits))
    };
    let mut stmt = conn.prepare(
        "SELECT label_id, payload_json FROM labels WHERE position_id=? AND label_type='teacher_value' AND source=?",
    )?;
    let mut rows = stmt.query(params![position_id, &key.source])?;
    while let Some(row) = rows.next()? {
        let id: i64 = row.get(0)?;
        let payload: String = row.get(1)?;
        let row_value_ok = match value {
            None => payload.contains("\"root_value\":null") || !payload.contains("\"root_value\""),
            Some(v) => {
                payload.contains(&format!("\"root_value\":{v}"))
                    || payload.contains(&format!("\"root_value\":{v:?}"))
            }
        };
        if !row_value_ok {
            continue;
        }
        if payload_contains_policy_hash(&payload, key.policy_hash.as_deref()) {
            return Ok(Some(id));
        }
    }
    Ok(None)
}

fn payload_contains_policy_hash(payload: &str, policy_hash: Option<&str>) -> bool {
    match policy_hash {
        None => !payload.contains("\"policy_hash\""),
        Some(h) => payload.contains(h),
    }
}

fn payload_json(key: &LabelKey, label: &AggregatedLabel, cohort: &str) -> Result<String> {
    let mut obj = json!({
        "schema": "friend-selfplay-v1",
        "source": cohort,
        "root_value": label.root_value,
        "outcome": label.outcome,
        "observation_count": label.observation_count,
    });
    if let Some(h) = &key.policy_hash {
        obj["policy_hash"] = json!(h);
    }
    if let Some(sidecar) = &label.sidecar_ref {
        obj["sidecar_ref"] = json!({
            "sidecar": sidecar.path,
            "offset": sidecar.offset,
            "record_bytes": sidecar.record_bytes,
            "policy_len": sidecar.policy_len,
        });
    }
    Ok(obj.to_string())
}

fn bump_observation(
    conn: &Connection,
    position_id: i64,
    cohort: &str,
    outcome: Option<f64>,
    visits: u64,
    stats: &mut WriterStats,
) -> Result<()> {
    let now = Utc::now().to_rfc3339();
    let (p0, p1, draws) = outcome_to_wdl(outcome);
    let updated = conn.execute(
        "UPDATE observations SET visit_count=visit_count+?, p0_wins=p0_wins+?, p1_wins=p1_wins+?, \
         draws=draws+?, last_seen=? WHERE position_id=? AND source_cohort=?",
        params![visits, p0, p1, draws, now, position_id, cohort],
    )?;
    if updated == 0 {
        conn.execute(
            "INSERT INTO observations(position_id, source_cohort, visit_count, p0_wins, p1_wins, draws, \
             first_seen, last_seen) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            params![position_id, cohort, visits, p0, p1, draws, now, now],
        )?;
    }
    stats.observations_bumped += visits;
    Ok(())
}

fn outcome_to_wdl(outcome: Option<f64>) -> (i64, i64, i64) {
    match outcome {
        Some(v) if v == 1.0 => (0, 1, 0),
        Some(v) if v == -1.0 => (1, 0, 0),
        Some(v) if v == 0.0 => (0, 0, 1),
        _ => (0, 0, 0),
    }
}

pub fn sha256_file(path: &Path) -> Result<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hex::encode(hasher.finalize()))
}
