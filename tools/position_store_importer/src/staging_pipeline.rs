//! Staging pipeline: parallel transform → per-shard staging files → bulk SQLite build.
//!
//! Does not touch the production teacher DB during phase 1. Benchmark on a disposable DB.

use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use micropool::ThreadPoolBuilder;

use crate::aggregate::{merge_batch_into, AggregatedLabel, BatchAgg, LabelKey};
use crate::db::TeacherWriter;
use crate::pipeline::{discover_shards, transform_batch, ImportReport, ShardReport};
use crate::progress::Progress;
use crate::sidecar::SidecarWriter;
use crate::staging::{collapse_positions, PositionStageRow, POS_MAGIC, STAGING_VERSION};

/// Run staging import for `shard_limit` shards (None = all) into `staging_dir`, then bulk-build `teacher_db`.
pub fn run_staging_pipeline(
    input_dir: &Path,
    staging_dir: &Path,
    teacher_db: &Path,
    sidecar_dir: &Path,
    rel_root: &Path,
    threads: usize,
    batch_records: usize,
    shard_limit: Option<usize>,
    progress: &Progress,
) -> Result<ImportReport> {
    fs::create_dir_all(staging_dir)?;
    let mut shards = discover_shards(input_dir)?;
    if let Some(n) = shard_limit {
        shards.truncate(n);
    }
    if shards.is_empty() {
        anyhow::bail!("no friend shards under {input_dir:?}");
    }
    let pool = ThreadPoolBuilder::default().num_threads(threads).build();
    let mut shard_reports = Vec::new();

    pool.install(|| -> Result<()> {
        for shard_path in &shards {
            shard_reports.push(stage_one_shard(
                shard_path,
                staging_dir,
                sidecar_dir,
                rel_root,
                batch_records,
                progress,
            )?);
        }
        Ok(())
    })?;

    let merge_start = std::time::Instant::now();
    let (positions, labels) = merge_staging_dir(staging_dir)?;
    progress.phases.add_us(
        &progress.phases.batch_reduce_us,
        merge_start.elapsed(),
    );

    if teacher_db.exists() {
        fs::remove_file(teacher_db)?;
    }
    let load_start = std::time::Instant::now();
    let mut writer = TeacherWriter::open(teacher_db, sidecar_dir)?;
    writer.bulk_load(&positions, &labels, Some(&progress.phases))?;
    progress.phases.add_us(
        &progress.phases.sqlite_commit_us,
        load_start.elapsed(),
    );

    let (pos_count, label_count, games, edges) = writer.counts()?;
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
            "pipeline": "staging_bulk_build",
            "summary": progress.summary(),
            "phase_timing": progress.phases.snapshot(),
            "merged_unique_positions": positions.len(),
            "merged_unique_labels": labels.len(),
            "positions": pos_count,
            "labels": label_count,
            "integrity": integrity,
        }),
    })
}

fn stage_one_shard(
    shard_path: &Path,
    staging_dir: &Path,
    sidecar_dir: &Path,
    rel_root: &Path,
    batch_records: usize,
    progress: &Progress,
) -> Result<ShardReport> {
    use std::io::BufRead;

    let iteration = shard_path
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|s| s.to_str())
        .unwrap_or("iter_unknown");
    let rel = shard_path
        .strip_prefix(rel_root)
        .unwrap_or(shard_path)
        .to_string_lossy()
        .replace('\\', "/");
    let cohort = format!("friend_selfplay:{iteration}");

    let file = File::open(shard_path)?;
    let reader = BufReader::new(file);
    let mut batch_lines: Vec<(u64, String)> = Vec::with_capacity(batch_records);
    let mut pos_rows: Vec<PositionStageRow> = Vec::new();
    let mut cross_batch: BTreeMap<LabelKey, AggregatedLabel> = BTreeMap::new();
    let mut sidecar = SidecarWriter::open(sidecar_dir, rel_root, iteration)?;
    let mut record_count = 0u64;
    let mut accepted = 0u64;
    let mut rejected = 0u64;
    let mut duplicate_positions = 0u64;

    for (line_no, line) in reader.lines().enumerate() {
        let read_start = std::time::Instant::now();
        let line = line?;
        progress.phases.add_us(&progress.phases.json_read_us, read_start.elapsed());
        if line.trim().is_empty() {
            continue;
        }
        record_count += 1;
        batch_lines.push((line_no as u64 + 1, line));
        if batch_lines.len() >= batch_records {
            let (stats, batch) = transform_batch(&mut batch_lines, &cohort, &mut sidecar, progress)?;
            accepted += stats.accepted;
            rejected += stats.rejected;
            duplicate_positions += stats.duplicate_positions;
            extract_staging_rows(&batch, &mut pos_rows);
            merge_batch_into(&mut cross_batch, batch);
        }
    }
    if !batch_lines.is_empty() {
        let (stats, batch) = transform_batch(&mut batch_lines, &cohort, &mut sidecar, progress)?;
        accepted += stats.accepted;
        rejected += stats.rejected;
        duplicate_positions += stats.duplicate_positions;
        extract_staging_rows(&batch, &mut pos_rows);
        merge_batch_into(&mut cross_batch, batch);
    }
    sidecar.finalize()?;

    let collapsed = collapse_positions(pos_rows);
    write_position_staging(staging_dir, iteration, &collapsed)?;
    write_label_staging(staging_dir, iteration, &cross_batch)?;

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
        status: "staged".into(),
    })
}

fn extract_staging_rows(batch: &BatchAgg, out: &mut Vec<PositionStageRow>) {
    for (_key, label) in &batch.labels {
        out.push(PositionStageRow {
            canonical_hash: label.key.canonical_hash,
            packed_state: label.packed_state,
            observation_count: label.observation_count,
            source_flags: 2,
        });
    }
}

fn write_position_staging(staging_dir: &Path, iteration: &str, rows: &[PositionStageRow]) -> Result<PathBuf> {
    let partial = staging_dir.join(format!("{iteration}.positions.stg.partial"));
    let final_path = staging_dir.join(format!("{iteration}.positions.stg"));
    let file = File::create(&partial)?;
    let mut enc = GzEncoder::new(file, Compression::default());
    enc.write_all(POS_MAGIC)?;
    enc.write_all(&STAGING_VERSION.to_le_bytes())?;
    for row in rows {
        let mut buf = Vec::with_capacity(PositionStageRow::ENCODED_LEN);
        row.encode(&mut buf);
        enc.write_all(&build_record_frame(&buf))?;
    }
    enc.finish()?;
    if final_path.exists() {
        fs::remove_file(&final_path)?;
    }
    fs::rename(&partial, &final_path)?;
    Ok(final_path)
}

fn write_label_staging(
    staging_dir: &Path,
    iteration: &str,
    labels: &BTreeMap<LabelKey, AggregatedLabel>,
) -> Result<PathBuf> {
    let partial = staging_dir.join(format!("{iteration}.labels.stg.partial"));
    let final_path = staging_dir.join(format!("{iteration}.labels.stg"));
    let file = File::create(&partial)?;
    let mut enc = GzEncoder::new(file, Compression::default());
    enc.write_all(crate::staging::LABEL_MAGIC)?;
    enc.write_all(&STAGING_VERSION.to_le_bytes())?;
    for (_key, label) in labels {
        let payload = serde_json::to_vec(label)?;
        enc.write_all(&build_record_frame(&payload))?;
    }
    enc.finish()?;
    if final_path.exists() {
        fs::remove_file(&final_path)?;
    }
    fs::rename(&partial, &final_path)?;
    Ok(final_path)
}

fn build_record_frame(payload: &[u8]) -> Vec<u8> {
    let len = payload.len() as u32;
    let mut out = Vec::with_capacity(4 + payload.len());
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(payload);
    out
}

fn read_staging_frames<R: Read>(mut reader: R) -> Result<Vec<Vec<u8>>> {
    let mut frames = Vec::new();
    loop {
        let mut len_buf = [0u8; 4];
        match reader.read_exact(&mut len_buf) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(e) => return Err(e.into()),
        }
        let len = u32::from_le_bytes(len_buf) as usize;
        let mut payload = vec![0u8; len];
        reader.read_exact(&mut payload)?;
        frames.push(payload);
    }
    Ok(frames)
}

fn merge_staging_dir(staging_dir: &Path) -> Result<(Vec<PositionStageRow>, Vec<AggregatedLabel>)> {
    let mut pos_map: BTreeMap<([u8; 32], [u8; 24]), PositionStageRow> = BTreeMap::new();
    let mut label_map: BTreeMap<LabelKey, AggregatedLabel> = BTreeMap::new();

    for entry in fs::read_dir(staging_dir)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        let path = entry.path();
        if name.ends_with(".positions.stg") {
            let file = File::open(&path)?;
            let mut decoder = GzDecoder::new(file);
            let mut magic = [0u8; 6];
            decoder.read_exact(&mut magic)?;
            if &magic != POS_MAGIC {
                anyhow::bail!("bad position staging magic in {path:?}");
            }
            let mut ver = [0u8; 2];
            decoder.read_exact(&mut ver)?;
            for frame in read_staging_frames(&mut decoder)? {
                let row = PositionStageRow::decode(&frame)
                    .with_context(|| format!("decode position frame in {path:?}"))?;
                let key = row.sort_key();
                pos_map
                    .entry(key)
                    .and_modify(|existing| existing.observation_count += row.observation_count)
                    .or_insert(row);
            }
        } else if name.ends_with(".labels.stg") {
            let file = File::open(&path)?;
            let mut decoder = GzDecoder::new(file);
            let mut magic = [0u8; 6];
            decoder.read_exact(&mut magic)?;
            if &magic != crate::staging::LABEL_MAGIC {
                anyhow::bail!("bad label staging magic in {path:?}");
            }
            let mut ver = [0u8; 2];
            decoder.read_exact(&mut ver)?;
            for frame in read_staging_frames(&mut decoder)? {
                let label: AggregatedLabel = serde_json::from_slice(&frame)
                    .with_context(|| format!("decode label frame in {path:?}"))?;
                let key = label.key.clone();
                label_map
                    .entry(key)
                    .and_modify(|existing| merge_label_rows(existing, &label))
                    .or_insert(label);
            }
        }
    }

    Ok((pos_map.into_values().collect(), label_map.into_values().collect()))
}

fn merge_label_rows(dst: &mut AggregatedLabel, src: &AggregatedLabel) {
    dst.observation_count += src.observation_count;
    if dst.best_move_u8.is_none() {
        dst.best_move_u8 = src.best_move_u8;
    }
    if dst.sidecar_ref.is_none() {
        dst.sidecar_ref = src.sidecar_ref.clone();
    }
}
