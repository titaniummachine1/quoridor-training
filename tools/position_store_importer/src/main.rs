//! import_teacher_store — micropool parallel friend shard importer.

use std::path::PathBuf;

use anyhow::{bail, Result};
use clap::Parser;

use position_store_importer::pipeline::{run_friend_import, ImportReport};
use position_store_importer::progress::Progress;
use position_store_importer::staging_pipeline::run_staging_pipeline;
use position_store_importer::{IMPORTER_VERSION, MICROPOOL_CRATE_VERSION};

#[derive(clap::ValueEnum, Clone, Debug, Default)]
enum PipelineMode {
    #[default]
    Upsert,
    Staging,
}

#[derive(Parser, Debug)]
#[command(name = "import_teacher_store", version = IMPORTER_VERSION)]
struct Args {
    /// Friend corpus root (contains iter_*/shard_000.jsonl)
    #[arg(long)]
    input: Option<PathBuf>,

    /// Teacher SQLite destination (never game_store.db)
    #[arg(long)]
    teacher_db: Option<PathBuf>,

    /// Compact policy sidecar directory
    #[arg(long)]
    sidecar_dir: Option<PathBuf>,

    /// Repository root for relative sidecar paths in label payloads
    #[arg(long, default_value = ".")]
    rel_root: PathBuf,

    /// Worker thread count (default: logical CPUs)
    #[arg(long)]
    threads: Option<usize>,

    /// JSONL records per batch
    #[arg(long, default_value_t = 50_000)]
    batch_records: usize,

    /// Skip shards already marked completed in imports table
    #[arg(long, default_value_t = true, action = clap::ArgAction::SetFalse)]
    resume: bool,

    /// Print dependency versions and exit
    #[arg(long)]
    version_info: bool,

    /// Import pipeline: upsert (default) or staging bulk-build prototype
    #[arg(long, value_enum, default_value_t = PipelineMode::Upsert)]
    pipeline: PipelineMode,

    /// Staging directory for --pipeline staging (phase 1 output)
    #[arg(long)]
    staging_dir: Option<PathBuf>,

    /// Limit shard count for staging benchmark (e.g. 2)
    #[arg(long)]
    shard_limit: Option<usize>,
}

fn main() -> Result<()> {
    let args = Args::parse();
    if args.version_info {
        println!(
            "{{\"importer\":\"{IMPORTER_VERSION}\",\"micropool\":\"{MICROPOOL_CRATE_VERSION}\"}}"
        );
        return Ok(());
    }
    let input = args
        .input
        .ok_or_else(|| anyhow::anyhow!("--input required"))?;
    let teacher_db = args
        .teacher_db
        .ok_or_else(|| anyhow::anyhow!("--teacher-db required"))?;
    let sidecar_dir = args
        .sidecar_dir
        .ok_or_else(|| anyhow::anyhow!("--sidecar-dir required"))?;
    if teacher_db
        .file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.contains("game_store"))
        .unwrap_or(false)
    {
        bail!("refusing to import friend data into game_store.db");
    }
    let threads = args.threads.unwrap_or_else(|| num_cpus::get().max(1));
    let progress = Progress::new(20, threads as u64);
    let report = match args.pipeline {
        PipelineMode::Upsert => {
            eprintln!(
                "[import] starting micropool upsert pipeline threads={threads} batch={}",
                args.batch_records
            );
            run_friend_import(
                &input,
                &teacher_db,
                &sidecar_dir,
                &args.rel_root,
                threads,
                args.batch_records,
                args.resume,
                args.shard_limit,
                &progress,
            )?
        }
        PipelineMode::Staging => {
            let staging_dir = args
                .staging_dir
                .ok_or_else(|| anyhow::anyhow!("--staging-dir required for --pipeline staging"))?;
            eprintln!(
                "[import] starting staging bulk-build pipeline threads={threads} batch={} staging={:?}",
                args.batch_records, staging_dir
            );
            run_staging_pipeline(
                &input,
                &staging_dir,
                &teacher_db,
                &sidecar_dir,
                &args.rel_root,
                threads,
                args.batch_records,
                args.shard_limit,
                &progress,
            )?
        }
    };
    print_report(&report);
    Ok(())
}

fn print_report(report: &ImportReport) {
    let json = serde_json::to_string_pretty(report).expect("report json");
    println!("{json}");
}
