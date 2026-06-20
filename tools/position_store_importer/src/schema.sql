PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS positions (
    position_id INTEGER PRIMARY KEY,
    canonical_hash BLOB NOT NULL,
    fast_hash INTEGER NOT NULL,
    packed_state BLOB NOT NULL,
    side_to_move INTEGER NOT NULL,
    ply_min_seen INTEGER,
    ply_max_seen INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    total_visits INTEGER NOT NULL DEFAULT 0,
    source_flags INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL,
    UNIQUE(canonical_hash, packed_state)
);

CREATE TABLE IF NOT EXISTS edges (
    parent_position_id INTEGER NOT NULL REFERENCES positions(position_id),
    move_code_u8 INTEGER NOT NULL,
    child_position_id INTEGER NOT NULL REFERENCES positions(position_id),
    visit_count INTEGER NOT NULL DEFAULT 0,
    p0_win_count INTEGER NOT NULL DEFAULT 0,
    p1_win_count INTEGER NOT NULL DEFAULT 0,
    draw_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(parent_position_id, move_code_u8, child_position_id)
);

CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY,
    start_position_id INTEGER NOT NULL REFERENCES positions(position_id),
    result INTEGER,
    move_count INTEGER NOT NULL,
    generator_engine_hash TEXT,
    generator_trunk_hash TEXT,
    search_config_hash TEXT,
    random_seed TEXT,
    worker_id TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    game_metadata TEXT
);

CREATE TABLE IF NOT EXISTS game_paths (
    game_id INTEGER PRIMARY KEY REFERENCES games(game_id),
    packed_u8_move_sequence BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    label_id INTEGER PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    label_type TEXT NOT NULL,
    value REAL,
    score REAL,
    bound TEXT,
    best_move_u8 INTEGER,
    nodes INTEGER,
    completed_depth INTEGER,
    selective_depth INTEGER,
    is_proven INTEGER NOT NULL DEFAULT 0,
    engine_hash TEXT,
    trunk_hash TEXT,
    search_config_hash TEXT,
    label_schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    quality_rank INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    source_cohort TEXT NOT NULL,
    visit_count INTEGER NOT NULL DEFAULT 0,
    p0_wins INTEGER NOT NULL DEFAULT 0,
    p1_wins INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    evaluation_summary TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(position_id, source_cohort)
);

CREATE TABLE IF NOT EXISTS relabel_queue (
    queue_id INTEGER PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    requested_label_type TEXT NOT NULL,
    requested_node_budget INTEGER,
    priority INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    required_engine_hash TEXT,
    required_trunk_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    import_id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    format TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    importer_version TEXT NOT NULL,
    status TEXT NOT NULL,
    error_report_path TEXT,
    UNIQUE(source_hash, format)
);

CREATE TABLE IF NOT EXISTS store_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_fast_hash ON positions(fast_hash);
CREATE INDEX IF NOT EXISTS idx_labels_position_type ON labels(position_id, label_type, trunk_hash, engine_hash);
CREATE INDEX IF NOT EXISTS idx_observations_source ON observations(source_cohort, visit_count DESC);
