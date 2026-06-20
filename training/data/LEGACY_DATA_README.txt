These files are preserved as immutable migration sources.
They are not the active training database.

=== CANONICAL PRODUCTION STORES (as of 2026-06-20) ===

  GAME STORE (normal pipeline):
    training/data/canonical/game_store.db

  TEACHER STORE (friend/zero/LMR labels — explicit flags only):
    training/data/canonical/position_teacher_store.db

  TEACHER SIDECARS (compact binary policy payloads):
    training/data/canonical/teacher_sidecars/

  MIGRATION ARTIFACT (do not train from — preserved for reference):
    training/data/canonical/position_store_v2.db
    training/data/canonical/migration_artifacts/

See training/CANONICAL_DATASTORE.md for authoritative commands.

=== LEGACY GAME DB ===

  training/data/all_games.db  (pool ingest — still used by run_swiss_overnight.py)

After pool games land in all_games.db, import into the canonical game store:
  python training/position_store.py import-legacy-games training/data/all_games.db

=== ARCHIVED LEGACY SOURCES ===

  training/data/archive/legacy_sources/   — original JSONL import sources
  training/data/archive/backups/          — DB snapshots taken before migrations
