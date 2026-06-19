These files are preserved as immutable migration sources.
They are not the active training database.

Canonical production store:
  training/data/canonical/position_store_v2.db

See training/CANONICAL_DATASTORE.md for authoritative commands.

Legacy game DB (pool ingest only — set TI_ALLOW_LEGACY_GAME_DB=1):
  training/data/all_games.db

After pool games land here, import into the canonical store:
  python training/position_store.py import-legacy-games training/data/all_games.db
