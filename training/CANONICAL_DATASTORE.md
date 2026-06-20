# Canonical Datastore — Titanium v15 Training

**DO NOT TRAIN DIRECTLY FROM LEGACY JSONL OR OLD GAME DATABASES.**  
**DO NOT MERGE TEACHER SNAPSHOTS INTO THE GAME STORE.**

## Two physically separate databases

| Store             | Path                                                | Purpose                                                                       |
| ----------------- | --------------------------------------------------- | ----------------------------------------------------------------------------- |
| **GAME STORE**    | `training/data/canonical/game_store.db`             | Replayable Titanium games for self-play ingestion, WDL training, pool imports |
| **TEACHER STORE** | `training/data/canonical/position_teacher_store.db` | Isolated labeled positions (friend, search-pressure, zero-teacher, LMR)       |

Both stores share the same `MOVE_SCHEMA_VERSION`, `POSITION_SCHEMA_VERSION`, packed position encoding, and canonical position hash. They are **never physically merged** unless you explicitly run a mixed export.

Policy payloads for teacher data live in compact binary sidecars:

```text
training/data/canonical/teacher_sidecars/
```

The legacy combined database (`position_store_v2.db`) is a **migration artifact only** — preserved under `training/data/canonical/migration_artifacts/` after split migration.

## Teacher training dataset (immutable, read-optimized)

| Artifact | Path | Role |
| -------- | ---- | ---- |
| **Teacher dataset** | `training/data/teacher_dataset/` | Immutable Parquet positions/labels/observations + sparse policy sidecars |
| **Teacher catalog** | `training/data/canonical/teacher_catalog.duckdb` | Read-only DuckDB views over Parquet (no duplicated payload tables) |
| **SQLite reference** | `training/data/canonical/teacher_sqlite_reference/` | Correctness reference only — `training_active=false` |

```text
game_store.db              → replayable Titanium games (mutable ingestion)
teacher_dataset/           → immutable training records (Parquet + policies)
teacher_catalog.duckdb     → read-only catalog and analytical views
position_teacher_store.db  → archived SQLite correctness reference (do not train from)
```

Build:

```powershell
python training/position_store.py freeze-teacher-reference
python training/position_store.py verify-teacher-policies
python training/position_store.py build-teacher-dataset --compression zstd
python training/position_store.py benchmark-teacher-readers
```


```text
default:              game_store only  (train.py, self-play, pool imports)
teacher distillation: teacher_store only  (--include-teacher-labels)
mixed training:       explicit export-mixed-training only
```

## Schema versions (frozen)

| Key                       | Version |
| ------------------------- | ------- |
| `DATABASE_SCHEMA_VERSION` | 1       |
| `POSITION_SCHEMA_VERSION` | 1       |
| `MOVE_SCHEMA_VERSION`     | 1       |
| `LABEL_SCHEMA_VERSION`    | 1       |
| `SHARD_SCHEMA_VERSION`    | 1       |

## Authoritative commands

```powershell
Set-Location "C:\gitProjects\Quoridor best AI"

# Game store (normal pipeline)
python training\position_store.py stats-game-store
python training\position_store.py audit-game-store
python training\position_store.py import-games training\data\all_games.db
python training\position_store.py ingest-shards training\data\selfplay_shards\inbox
python training\position_store.py export-game-training training\data\exports\game_training_export.jsonl

# Teacher store (explicit only)
python training\position_store.py stats-teacher-store
python training\position_store.py audit-teacher-store
python training\position_store.py import-teacher-positions
python training\position_store.py import-friend-rust --threads 8
python training\position_store.py export-teacher-training training\data\exports\teacher_export.jsonl --include-teacher-labels

# Legacy Python friend importer (single-threaded reference only):
python training\position_store.py import-friend-shards

# Mixed export (explicit join/dedupe)
python training\position_store.py export-mixed-training training\data\exports\mixed_export.jsonl

# Split recovery (preserve combined artifact, restore game store, populate teacher store)
python training\position_store.py split-migration

# NNUE WDL training — reads game store by default
python training\train.py
```

## Environment variables

| Variable                        | Default                                             |
| ------------------------------- | --------------------------------------------------- |
| `TI_GAME_STORE_DB`              | `training/data/canonical/game_store.db`             |
| `TI_TEACHER_STORE_DB`           | `training/data/canonical/position_teacher_store.db` |
| `TI_TEACHER_SIDECARS`           | `training/data/canonical/teacher_sidecars`          |
| `TI_POSITION_STORE_DB`          | alias → `TI_GAME_STORE_DB`                          |
| `TI_POSITION_STORE_SHARD_INBOX` | `training/data/selfplay_shards/inbox`               |
| `TI_POSITION_STORE_EXPORTS`     | `training/data/exports`                             |
| `TI_POSITION_STORE_ARCHIVE`     | `training/data/archive/legacy_sources`              |

## Data placement rules

**Game store:** replayable games, edges, game paths, WDL, Titanium search labels on replay-derived positions.

**Teacher store:** KaAiData friend snapshots, search-pressure, zero-teacher, pathless LMR candidates, policy/value sidecars.

**Never:** invent game paths, fake edges, or parent-child ancestry for isolated teacher snapshots.

## Rust importer (friend shards)

The Rust micropool importer (`tools/position_store_importer/`) handles bulk friend-shard imports at ~10× Python speed. Build once:

```powershell
cd tools\position_store_importer
cargo build --release
```

Then import via the Python wrapper:

```powershell
python training\position_store.py import-friend-rust --threads 8
```

The legacy `import-friend-shards` Python path is kept for reference only.

## Migration status (2026-06-20)

- `game_store.db` and `position_teacher_store.db` are active and validated.
- `position_store_v2.db` is a migration artifact — do not train from it. Scheduled for removal after full validation.
- `teacher_dataset/` contains the active immutable Parquet snapshot.
- `teacher_dataset_candidate/` holds the in-progress next build.

## For future agents

1. Do **not** recreate a single combined canonical database.
2. Friend / pathless JSONL imports go to `position_teacher_store.db` only.
3. `train.py` defaults to `game_store.db` — do not point it at the teacher store without an explicit training-mode change.
4. Mixed training requires `export-mixed-training` — no automatic mixing.
5. Never touch `teacher_dataset_candidate/` while a build is running (check for running Python PID first).
