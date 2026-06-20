//! Live progress reporting and phase timing for worker vs writer bottleneck analysis.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

/// Cumulative phase timings in microseconds (atomics for parallel workers).
#[derive(Debug, Default)]
pub struct PhaseMetrics {
    pub json_read_us: AtomicU64,
    pub parallel_transform_us: AtomicU64,
    pub batch_reduce_us: AtomicU64,
    pub sidecar_write_us: AtomicU64,
    pub sqlite_position_us: AtomicU64,
    pub sqlite_label_us: AtomicU64,
    pub sqlite_observation_us: AtomicU64,
    pub sqlite_commit_us: AtomicU64,
    pub wal_checkpoint_us: AtomicU64,
    /// Time workers spent waiting on parsed-result mutex (contention proxy).
    pub worker_lock_wait_us: AtomicU64,
    pub writer_rows: AtomicU64,
    pub worker_rows: AtomicU64,
    pub batches_in_writer: AtomicU64,
}

impl PhaseMetrics {
    pub fn add_us(&self, field: &AtomicU64, d: Duration) {
        field.fetch_add(d.as_micros() as u64, Ordering::Relaxed);
    }

    pub fn snapshot(&self) -> serde_json::Value {
        let json_read = self.json_read_us.load(Ordering::Relaxed);
        let parallel = self.parallel_transform_us.load(Ordering::Relaxed);
        let reduce = self.batch_reduce_us.load(Ordering::Relaxed);
        let sidecar = self.sidecar_write_us.load(Ordering::Relaxed);
        let pos = self.sqlite_position_us.load(Ordering::Relaxed);
        let label = self.sqlite_label_us.load(Ordering::Relaxed);
        let obs = self.sqlite_observation_us.load(Ordering::Relaxed);
        let commit = self.sqlite_commit_us.load(Ordering::Relaxed);
        let wal = self.wal_checkpoint_us.load(Ordering::Relaxed);
        let lock_wait = self.worker_lock_wait_us.load(Ordering::Relaxed);
        let worker_rows = self.worker_rows.load(Ordering::Relaxed);
        let writer_rows = self.writer_rows.load(Ordering::Relaxed);
        let worker_us = parallel + lock_wait;
        let writer_us = reduce + sidecar + pos + label + obs + commit + wal;
        let total_us = json_read + worker_us + writer_us;
        let worker_pct = if total_us > 0 {
            100.0 * worker_us as f64 / total_us as f64
        } else {
            0.0
        };
        let writer_pct = if total_us > 0 {
            100.0 * writer_us as f64 / total_us as f64
        } else {
            0.0
        };
        let worker_sec = worker_us as f64 / 1_000_000.0;
        let writer_sec = writer_us as f64 / 1_000_000.0;
        serde_json::json!({
            "json_read_sec": json_read as f64 / 1_000_000.0,
            "parallel_transform_sec": parallel as f64 / 1_000_000.0,
            "batch_reduce_sec": reduce as f64 / 1_000_000.0,
            "sidecar_write_sec": sidecar as f64 / 1_000_000.0,
            "sqlite_position_sec": pos as f64 / 1_000_000.0,
            "sqlite_label_sec": label as f64 / 1_000_000.0,
            "sqlite_observation_sec": obs as f64 / 1_000_000.0,
            "sqlite_commit_sec": commit as f64 / 1_000_000.0,
            "wal_checkpoint_sec": wal as f64 / 1_000_000.0,
            "worker_lock_wait_sec": lock_wait as f64 / 1_000_000.0,
            "worker_total_sec": worker_sec,
            "writer_total_sec": writer_sec,
            "worker_pct": worker_pct,
            "writer_pct": writer_pct,
            "worker_rows": worker_rows,
            "writer_rows": writer_rows,
            "worker_rows_per_sec": if worker_sec > 0.0 { worker_rows as f64 / worker_sec } else { 0.0 },
            "writer_rows_per_sec": if writer_sec > 0.0 { writer_rows as f64 / writer_sec } else { 0.0 },
            "bottleneck": if writer_pct > worker_pct { "sqlite_single_writer" } else { "cpu_transform" },
            "pipeline_note": "Synchronous batch pipeline: implicit backpressure when writer phase runs (no inter-batch worker queue).",
        })
    }
}

#[derive(Debug)]
pub struct Progress {
    started: Instant,
    pub shards_total: u64,
    pub shards_done: AtomicU64,
    pub records_read: AtomicU64,
    pub records_accepted: AtomicU64,
    pub records_rejected: AtomicU64,
    pub unique_positions: AtomicU64,
    pub labels_aggregated: AtomicU64,
    pub policy_bytes: AtomicU64,
    pub batches_committed: AtomicU64,
    pub threads: u64,
    pub phases: PhaseMetrics,
}

impl Progress {
    pub fn new(shards_total: u64, threads: u64) -> Self {
        Self {
            started: Instant::now(),
            shards_total,
            shards_done: AtomicU64::new(0),
            records_read: AtomicU64::new(0),
            records_accepted: AtomicU64::new(0),
            records_rejected: AtomicU64::new(0),
            unique_positions: AtomicU64::new(0),
            labels_aggregated: AtomicU64::new(0),
            policy_bytes: AtomicU64::new(0),
            batches_committed: AtomicU64::new(0),
            threads,
            phases: PhaseMetrics::default(),
        }
    }

    pub fn maybe_log(&self) {
        let read = self.records_read.load(Ordering::Relaxed);
        if read == 0 || read % 100_000 != 0 {
            return;
        }
        let elapsed = self.started.elapsed().as_secs_f64().max(0.001);
        let rps = read as f64 / elapsed;
        let phases = self.phases.snapshot();
        eprintln!(
            "[import] shards {}/{} read={} accepted={} rejected={} labels={} batches={} {:.0} rec/s threads={} \
             worker={:.0}% writer={:.0}% w_rps={:.0} wr_rps={:.0}",
            self.shards_done.load(Ordering::Relaxed),
            self.shards_total,
            read,
            self.records_accepted.load(Ordering::Relaxed),
            self.records_rejected.load(Ordering::Relaxed),
            self.labels_aggregated.load(Ordering::Relaxed),
            self.batches_committed.load(Ordering::Relaxed),
            rps,
            self.threads,
            phases["worker_pct"].as_f64().unwrap_or(0.0),
            phases["writer_pct"].as_f64().unwrap_or(0.0),
            phases["worker_rows_per_sec"].as_f64().unwrap_or(0.0),
            phases["writer_rows_per_sec"].as_f64().unwrap_or(0.0),
        );
    }

    pub fn summary(&self) -> serde_json::Value {
        let elapsed = self.started.elapsed().as_secs_f64();
        let read = self.records_read.load(Ordering::Relaxed);
        serde_json::json!({
            "elapsed_sec": elapsed,
            "shards_done": self.shards_done.load(Ordering::Relaxed),
            "shards_total": self.shards_total,
            "records_read": read,
            "records_accepted": self.records_accepted.load(Ordering::Relaxed),
            "records_rejected": self.records_rejected.load(Ordering::Relaxed),
            "unique_positions": self.unique_positions.load(Ordering::Relaxed),
            "labels_aggregated": self.labels_aggregated.load(Ordering::Relaxed),
            "policy_bytes": self.policy_bytes.load(Ordering::Relaxed),
            "batches_committed": self.batches_committed.load(Ordering::Relaxed),
            "records_per_sec": if elapsed > 0.0 { read as f64 / elapsed } else { 0.0 },
            "threads": self.threads,
            "phase_timing": self.phases.snapshot(),
        })
    }
}
