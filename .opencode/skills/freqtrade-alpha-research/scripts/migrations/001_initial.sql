CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE hypotheses (
    id TEXT PRIMARY KEY,
    thesis TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    expected_sign INTEGER NOT NULL CHECK (expected_sign IN (-1, 0, 1)),
    horizon TEXT NOT NULL,
    data_requirements_json TEXT NOT NULL,
    falsification TEXT NOT NULL,
    semantic_family TEXT NOT NULL,
    parent_id TEXT REFERENCES hypotheses(id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE factors (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    name TEXT NOT NULL,
    formula_ast TEXT NOT NULL,
    formula_hash TEXT NOT NULL,
    instance_hash TEXT NOT NULL UNIQUE,
    semantic_family TEXT NOT NULL,
    source TEXT NOT NULL,
    license TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    lookback INTEGER NOT NULL CHECK (lookback > 0),
    frequency TEXT NOT NULL,
    availability_rule TEXT NOT NULL,
    direction INTEGER NOT NULL CHECK (direction IN (-1, 0, 1)),
    parameter_json TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX factors_formula_hash_idx ON factors(formula_hash);
CREATE INDEX factors_family_idx ON factors(semantic_family);

CREATE TABLE research_runs (
    id TEXT PRIMARY KEY,
    previous_run_id TEXT REFERENCES research_runs(id),
    purpose TEXT NOT NULL,
    proposal_budget INTEGER NOT NULL CHECK (proposal_budget >= 0),
    eval_budget INTEGER NOT NULL CHECK (eval_budget >= 0),
    used_budget INTEGER NOT NULL DEFAULT 0 CHECK (used_budget >= 0),
    decision_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE datasets (
    id TEXT PRIMARY KEY,
    access_class TEXT NOT NULL CHECK (
        access_class IN ('EXPLORATORY', 'REFERENCE_CONTAMINATED', 'LOCKED', 'INDEPENDENT_TEST')
    ),
    path TEXT NOT NULL,
    dataset_hash TEXT NOT NULL UNIQUE,
    schema_json TEXT NOT NULL,
    start_ts TEXT,
    end_ts TEXT,
    source_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    factor_id TEXT NOT NULL REFERENCES factors(id),
    run_id TEXT REFERENCES research_runs(id),
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    dataset_hash TEXT NOT NULL,
    split_role TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    code_commit TEXT NOT NULL,
    target_spec_json TEXT NOT NULL,
    target_spec_hash TEXT NOT NULL,
    test_family_id TEXT NOT NULL,
    experiment_type TEXT NOT NULL DEFAULT 'factor',
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    artifacts_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (factor_id, dataset_hash, config_hash, code_commit, target_spec_hash)
);

CREATE TABLE evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL UNIQUE REFERENCES experiments(id),
    horizon TEXT NOT NULL,
    metric_schema_version INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    intervals_json TEXT NOT NULL,
    q_value REAL,
    gate_decision TEXT NOT NULL,
    failure_reason TEXT,
    evidence_level TEXT NOT NULL,
    supersedes_id INTEGER REFERENCES evaluations(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE data_exposures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    run_id TEXT REFERENCES research_runs(id),
    purpose TEXT NOT NULL,
    approved_by TEXT,
    exposed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE selection_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    hypothesis_id TEXT REFERENCES hypotheses(id),
    factor_id TEXT REFERENCES factors(id),
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
