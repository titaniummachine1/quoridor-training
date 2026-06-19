# Canonical Datastore — Titanium v15 Training

**DO NOT TRAIN DIRECTLY FROM LEGACY JSONL OR OLD GAME DATABASES.**  
**THE CANONICAL POSITION STORE IS THE SOURCE OF TRUTH.**

## Active architecture

```text
canonical position graph database     training/data/canonical/position_store_v2.db
+ versioned binary sidecars           training/data/canonical/sidecars/   (compact v2 — experimental)
+ compact training exports            training/data/exports/
+ original immutable source archive   training/data/archive/legacy_sources/
+ self-play shard inbox               training/data/selfplay_shards/inbox/
```

## Schema versions (frozen for migration run)

| Key | Version |
|-----|---------|
| `DATABASE_SCHEMA_VERSION` | 1 |
| `POSITION_SCHEMA_VERSION` | 1 |
| `MOVE_SCHEMA_VERSION` | 1 |
| `LABEL_SCHEMA_VERSION` | 1 |
| `SHARD_SCHEMA_VERSION` | 1 |

Move alphabet: wall `0–127`, pawn direction classes `128–135`, reserved `136–255`.

Production metadata flags:

```text
canonical_migration_complete = true
engine_parity_verified       = false   # Rust/Python parity not yet proven
compact_label_migration        = pending # position_store_compact.py is experimental
```

## Authoritative commands

```powershell
Set-Location "C:\gitProjects\Quoridor best AI"

# Production DB status
python training\position_store.py stats
python training\position_store.py audit-canonical

# Full migration (from scratch, preserves source archive)
python training\position_store.py migrate-production

# Ingest new self-play shards
python training\position_store.py ingest-shards training\data\selfplay_shards\inbox

# Export labeled training rows
python training\position_store.py export-training training\data\exports\training_export.jsonl --label-type teacher_value

# NNUE outcome training (reads games from canonical store)
python training\train.py
```

## Environment overrides

| Variable | Default |
|----------|---------|
| `TI_POSITION_STORE_DB` | `training/data/canonical/position_store_v2.db` |
| `TI_POSITION_STORE_SIDECARS` | `training/data/canonical/sidecars` |
| `TI_POSITION_STORE_SHARD_INBOX` | `training/data/selfplay_shards/inbox` |
| `TI_POSITION_STORE_EXPORTS` | `training/data/exports` |
| `TI_POSITION_STORE_ARCHIVE` | `training/data/archive/legacy_sources` |

Pool/coordinator legacy writes (temporary until shard pipeline is default):

```powershell
$env:TI_ALLOW_LEGACY_GAME_DB = "1"
```

## Legacy vs smoke vs production

| Path | Role |
|------|------|
| `training/data/canonical/position_store_v2.db` | **Production canonical store** |
| `training/data/all_games.db` | Legacy game-ingest source only — not for training |
| `training/data/search_pressure.jsonl` | Legacy label source — import only |
| `training/data/smoke/*` | Reproducible smoke DBs — never production |
| `training/data/archive/legacy_sources/*` | Immutable migration manifests + checksums |
| `training/data/position_store_reports/*` | Reject reports and inventories |

## Rebuild from archive

```powershell
python training\position_store.py migrate-production
python training\position_store.py prove-idempotence
python training\position_store.py prove-rebuild --migration-run-id <run_id>
```

`<run_id>` is the timestamp directory under `training/data/archive/legacy_sources/`.

## Backup / restore

Sources are never deleted. Before migration, checksums are written to:

```text
training/data/archive/legacy_sources/<migration_run_id>/checksums.sha256
```

Copy the production DB for backup:

```powershell
Copy-Item training\data\canonical\position_store_v2.db training\data\archive\backups\position_store_v2.db.bak
```

## Compact schema status (Option B — deferred)

`position_store_compact.py` implements schema v2 scaffolding (`i16` centitempo, sidecars).  
It is **experimental only**. Production canonical format remains schema v1 graph SQLite until compact migration is explicitly promoted.

## Instructions for future coding agents

- Do not create a second training-data database.
- Do not train directly from legacy JSONL or old SQLite files.
- Use the canonical position-store CLI.
- New self-play data must arrive through versioned binary shards.
- New labels must be attached through versioned label sets.
- Preserve occurrence counts while deduplicating positions.
- Never reinterpret rejected records without an explicit migration.
- Update schema versions and this document for format changes.

See also: [`POSITION_STORE_RUNBOOK.md`](POSITION_STORE_RUNBOOK.md)
