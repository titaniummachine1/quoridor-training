//! Micropool parallel friend shard ingestion pipeline.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use anyhow::{Context, Result};
use micropool::iter::*;
use micropool::{split_by_threads, ThreadPoolBuilder};
use serde_json::Value;

use crate::aggregate::{merge_batch_into, AggregatedLabel, BatchAgg, LabelKey};
use crate::db::{sha256_file, TeacherWriter};
use crate::friend::{parse_friend_record, FriendParseError};
use crate::progress::Progress;
use crate::sidecar::SidecarWriter;

pub const SHARD_FORMAT: &str = "alpha-selfplay-jsonl";

#[derive(Debug, Clone, serde::Serialize)]
pub struct ShardReport {
    pub shard: String,
    pub record_count: u64,
    pub accepted: u64,
    pub rejected: u64,
    pub duplicate_positions: u64,
    pub labels_after_aggregation: u64,
    pub status: String,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ImportReport {
    pub importer_version: String,
    pub micropool_version: String,
    pub threads: u64,
    pub batch_records: usize,
    pub shards: Vec<ShardReport>,
    pub progress: serde_json::Value,
}

pub(crate) struct BatchStats {
    pub accepted: u64,
    pub rejected: u64,
    pub duplicate_positions: u64,
}

pub fn discover_shards(input_dir: &Path) -> Result<Vec<PathBuf>> {
    let mut shards = Vec::new();
    for entry in std::fs::read_dir(input_dir).with_context(|| format!("read_dir {input_dir:?}"))? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("iter_") {
            continue;
        }
        let shard = entry.path().join("shard_000.jsonl");
        if shard.is_file() {
            shards.push(shard);
        }
    }
    shards.sort();
    Ok(shards)
}

pub fn run_friend_import(
    input_dir: &Path,
    teacher_db: &Path,
    sidecar_dir: &Path,
    rel_root: &Path,
    threads: usize,
    batch_records: usize,
    resume: bool,
    shard_limit: Option<usize>,
    progress: &Progress,
) -> Result<ImportReport> {
    let mut shards = discover_shards(input_dir)?;
    if let Some(n) = shard_limit {
        shards.truncate(n);
    }
    if shards.is_empty() {
        anyhow::bail!("no friend shards under {input_dir:?}");
    }
    let pool = ThreadPoolBuilder::default().num_threads(threads).build();
    let mut writer = TeacherWriter::open(teacher_db, sidecar_dir)?;
    let mut shard_reports = Vec::new();

    pool.install(|| -> Result<()> {
        for shard_path in &shards {
            shard_reports.push(import_one_shard(
                shard_path,
                rel_root,
                sidecar_dir,
                batch_records,
                resume,
                &mut writer,
                progress,
            )?);
        }
        Ok(())
    })?;

    let (positions, labels, games, edges) = writer.counts()?;
    if games != 0 || edges != 0 {
        anyhow::bail!("teacher store invariant violated: games={games} edges={edges}");
    }
    let integrity = writer.integrity_check()?;
    if integrity != "ok" {
        anyhow::bail!("integrity_check failed: {integrity}");
    }

    Ok(ImportReport {
        importer_version: crate::IMPORTER_VERSION.to_string(),
        micropool_version: crate::MICROPOOL_CRATE_VERSION.to_string(),
        threads: threads as u64,
        batch_records,
        shards: shard_reports,
        progress: serde_json::json!({
            "summary": progress.summary(),
            "phase_timing": progress.phases.snapshot(),
            "positions": positions,
            "labels": labels,
            "integrity": integrity,
        }),
    })
}

fn import_one_shard(
    shard_path: &Path,
    rel_root: &Path,
    sidecar_dir: &Path,
    batch_records: usize,
    resume: bool,
    writer: &mut TeacherWriter,
    progress: &Progress,
) -> Result<ShardReport> {
    let source_hash = sha256_file(shard_path)?;
    let rel = shard_path
        .strip_prefix(rel_root)
        .unwrap_or(shard_path)
        .to_string_lossy()
        .replace('\\', "/");
    if resume && writer.shard_complete(&source_hash, SHARD_FORMAT)? {
        progress
            .shards_done
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        return Ok(ShardReport {
            shard: rel,
            record_count: 0,
            accepted: 0,
            rejected: 0,
            duplicate_positions: 0,
            labels_after_aggregation: 0,
            status: "already_imported".into(),
        });
    }
    writer.reset_running_import(&source_hash, SHARD_FORMAT)?;
    let import_id =
        writer.begin_shard_import(&shard_path.to_string_lossy(), &source_hash, SHARD_FORMAT)?;

    let iteration = shard_path
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|s| s.to_str())
        .unwrap_or("iter_unknown");
    let cohort = format!("friend_selfplay:{iteration}");
    let mut sidecar = SidecarWriter::open(sidecar_dir, rel_root, iteration)?;
    let mut cross_batch: BTreeMap<LabelKey, AggregatedLabel> = BTreeMap::new();

    let file = File::open(shard_path)?;
    let reader = BufReader::new(file);
    let mut batch_lines: Vec<(u64, String)> = Vec::with_capacity(batch_records);
    let mut record_count = 0u64;
    let mut accepted = 0u64;
    let mut rejected = 0u64;
    let mut duplicate_positions = 0u64;

    for (line_no, line) in reader.lines().enumerate() {
        let read_start = std::time::Instant::now();
        let line = line?;
        progress
            .phases
            .add_us(&progress.phases.json_read_us, read_start.elapsed());
        if line.trim().is_empty() {
            continue;
        }
        record_count += 1;
        batch_lines.push((line_no as u64 + 1, line));
        if batch_lines.len() >= batch_records {
            let stats = process_batch(
                &mut batch_lines,
                &cohort,
                &mut sidecar,
                &mut cross_batch,
                writer,
                progress,
            )?;
            accepted += stats.accepted;
            rejected += stats.rejected;
            duplicate_positions += stats.duplicate_positions;
        }
    }
    if !batch_lines.is_empty() {
        let stats = process_batch(
            &mut batch_lines,
            &cohort,
            &mut sidecar,
            &mut cross_batch,
            writer,
            progress,
        )?;
        accepted += stats.accepted;
        rejected += stats.rejected;
        duplicate_positions += stats.duplicate_positions;
    }
    sidecar.finalize()?;
    writer.wal_checkpoint(Some(&progress.phases))?;
    writer.finish_shard_import(
        import_id,
        record_count as i64,
        accepted as i64,
        rejected as i64,
        0,
    )?;
    progress
        .shards_done
        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    Ok(ShardReport {
        shard: rel,
        record_count,
        accepted,
        rejected,
        duplicate_positions,
        labels_after_aggregation: cross_batch.len() as u64,
        status: "completed".into(),
    })
}

/// Parallel transform + in-memory batch aggregation (no SQLite).
pub fn transform_batch(
    batch_lines: &mut Vec<(u64, String)>,
    cohort: &str,
    sidecar: &mut SidecarWriter,
    progress: &Progress,
) -> Result<(BatchStats, BatchAgg)> {
    let lines = std::mem::take(batch_lines);
    let parsed = Mutex::new(Vec::with_capacity(lines.len()));
    let transform_start = std::time::Instant::now();
    progress
        .phases
        .batches_in_writer
        .store(0, std::sync::atomic::Ordering::Relaxed);
    lines
        .par_iter()
        .with_thread_pool(split_by_threads())
        .for_each(|(line_no, line)| {
            let lock_start = std::time::Instant::now();
            let item = {
                let value: Value = match serde_json::from_str(line) {
                    Ok(v) => v,
                    Err(e) => {
                        parsed
                            .lock()
                            .expect("parsed lock")
                            .push(Err((*line_no, FriendParseError::Other(e.to_string()))));
                        progress
                            .phases
                            .add_us(&progress.phases.worker_lock_wait_us, lock_start.elapsed());
                        return;
                    }
                };
                match parse_friend_record(&value) {
                    Ok(rec) => Ok((*line_no, rec)),
                    Err(e) => Err((*line_no, e)),
                }
            };
            parsed.lock().expect("parsed lock").push(item);
            progress
                .phases
                .add_us(&progress.phases.worker_lock_wait_us, lock_start.elapsed());
        });
    progress.phases.add_us(
        &progress.phases.parallel_transform_us,
        transform_start.elapsed(),
    );
    progress
        .phases
        .worker_rows
        .fetch_add(lines.len() as u64, std::sync::atomic::Ordering::Relaxed);
    let parsed = parsed.into_inner().expect("parsed mutex");

    let reduce_start = std::time::Instant::now();
    let mut batch = BatchAgg::new();
    let mut stats = BatchStats {
        accepted: 0,
        rejected: 0,
        duplicate_positions: 0,
    };

    for item in parsed {
        progress
            .records_read
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        match item {
            Ok((_line_no, record)) => {
                stats.accepted += 1;
                progress
                    .records_accepted
                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                let sidecar_ref = if record.move_codes_u8.is_empty() {
                    None
                } else {
                    let sc_start = std::time::Instant::now();
                    let canonical = record.state.canonical_hash();
                    let sidecar_ref = sidecar.write_policy(
                        &canonical,
                        &record.move_codes_u8,
                        &record.policy_values,
                    )?;
                    progress
                        .phases
                        .add_us(&progress.phases.sidecar_write_us, sc_start.elapsed());
                    progress.policy_bytes.fetch_add(
                        sidecar_ref.record_bytes as u64,
                        std::sync::atomic::Ordering::Relaxed,
                    );
                    Some(sidecar_ref)
                };
                let before = batch.labels.len();
                batch.ingest_friend(&record, cohort, sidecar_ref);
                if batch.labels.len() == before {
                    stats.duplicate_positions += 1;
                }
            }
            Err((_line_no, _err)) => {
                stats.rejected += 1;
                progress
                    .records_rejected
                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }
        }
        progress.maybe_log();
    }
    progress
        .phases
        .add_us(&progress.phases.batch_reduce_us, reduce_start.elapsed());
    Ok((stats, batch))
}

fn process_batch(
    batch_lines: &mut Vec<(u64, String)>,
    cohort: &str,
    sidecar: &mut SidecarWriter,
    cross_batch: &mut BTreeMap<LabelKey, AggregatedLabel>,
    writer: &mut TeacherWriter,
    progress: &Progress,
) -> Result<BatchStats> {
    progress
        .phases
        .batches_in_writer
        .store(1, std::sync::atomic::Ordering::Relaxed);
    let (stats, batch) = transform_batch(batch_lines, cohort, sidecar, progress)?;
    let commit_labels: Vec<(LabelKey, AggregatedLabel)> = batch.labels.into_iter().collect();
    writer.commit_batch(&commit_labels, cohort, Some(&progress.phases))?;
    merge_batch_into(
        cross_batch,
        BatchAgg {
            labels: commit_labels.into_iter().collect(),
        },
    );
    progress.labels_aggregated.store(
        cross_batch.len() as u64,
        std::sync::atomic::Ordering::Relaxed,
    );
    progress
        .batches_committed
        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    Ok(stats)
}
