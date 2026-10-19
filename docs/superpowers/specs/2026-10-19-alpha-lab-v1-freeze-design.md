# Alpha Lab v1.0 Repair and Freeze Design

## Scope and success contract

Alpha Lab becomes a versioned research engine whose evidence is append only, whose experiment identity covers every input that can change a result, and whose candidate Python runs outside the trusted writer process. The 2022–2024 window remains development research data; this work does not inspect 2025+ data, create factors, or touch trading execution.

Release status is conditional. The engine may be called v1.0 only after persistence, statistical, reuse, isolation, Golden, SQLite, concurrency, and end to end checks pass. Branch protection and hosted CODEOWNERS enforcement are documented as manual repository settings when they cannot be enabled locally.

## Evidence identity and storage

Every statistical review has an explicit `analysis_id` and `config_id`. Its deterministic identity includes those fields plus run, method version, bootstrap count, and seed. Repeating byte equivalent canonical evidence is idempotent; repeating an identity with different evidence raises a conflict. Existing rows remain untouched and readable. Schema changes use a transaction and the migration helper creates a timestamped database backup before altering an existing database.

All append only tables reject conflicting identities. Foreign keys are enabled on every connection and integrity checks are exposed for acceptance tests. Experiments use a complete protocol hash rather than candidate/timeframe/horizon alone, so separate hypotheses on the same development set remain separate members of their declared multiple testing family.

## Statistical contract

`VerdictPolicy` is the only source for practical thresholds: AUC 0.005, log loss 0.0005, and Brier 0.00025. Reports and persisted reviews copy values from that policy.

Bootstrap confidence intervals continue to use paired moving blocks. Significance uses a null centered bootstrap distribution (`draw - observed`) and the finite sample `(extreme + 1)/(B + 1)` p value. The old uncentered tail proportion remains historical under its old method versions and cannot support new conclusions. New results use bumped method versions. Monte Carlo tests cover independent and autocorrelated nulls with explicit tolerances.

Mechanism support requires agreement among preregistered direction, overall RankIC sign, and the direction tested across valid nonzero months. Opposite overall and monthly signs produce a conflict and cannot be SUPPORTED. RankIC uncertainty derives from the RankIC bootstrap distribution itself, never from log loss effective sample size.

## Experiment identity and reuse

The canonical identity records engine and protocol versions, full candidate manifest and source hashes, content SHA-256 of the bounded data source, date range, timeframe, horizon, baseline, labeling, walk forward and purge rules, model settings, evaluator code hash, and all bootstrap/statistical settings. Reuse requires a completed registry row plus a manifest whose hashes match `summary.json`, all prediction and fold files, candidate snapshot, and evidence files. Missing or corrupt artifacts create a new run. Failed execution is recorded failed.

## Candidate isolation

The trusted runner serializes only the allowed OHLCV frame and parameters into a private run workspace and invokes a dedicated worker through Bubblewrap on Linux/WSL. The sandbox has a read only minimal runtime, a read only candidate snapshot, a writable private scratch directory, no network namespace, no inherited secrets, and no writable view of the repository, database, prior runs, home directory, or arbitrary user paths. The worker cannot spawn unrelated commands because only the Python runtime and required libraries are visible and process creation is blocked through resource and syscall controls where available. The trusted parent validates the returned Series and alone writes artifacts and SQLite.

If Bubblewrap/user namespaces are unavailable, untrusted candidate execution fails closed with an actionable message. An explicit trusted maintenance override may exist for tests and already reviewed built in code, but unattended Research Agent runs never use it.

## Freeze and agent boundary

The frozen manifest enumerates data, labels, evaluation, models, stats, validation, runner, store, registry, config, tests, built in factors, dataset builders, and public evaluation entry points with SHA-256 hashes. CI compares changes against the manifest and requires the maintainer workflow marker plus review ownership for engine changes.

OpenCode v2.0.29 agent permissions use default deny and exact allows for reading documentation/history, editing one candidate subtree, and invoking named research CLI commands. Shell, delegation, direct SQLite tools, network, and writes to engine/runs/history are denied. The candidate runtime sandbox is the security boundary; prompt permissions are defense in depth.

## Versioned protocol and operations

The protocol document fixes allowed dates, 5m/15m/1h/4h timeframes, explicit horizon, schemas, signal timing, labels, walk forward and purge, baseline, metrics, inference, multiple testing families, MDE, verdicts, evidence versus decisions, identity, and reuse. Every run records `engine_version`, `protocol_version`, `data_fingerprint`, `candidate_source_hash`, and `config_hash`.

Maintainer changes start from an issue, update tests first, bump method/protocol/engine versions when semantics change, regenerate Golden outputs and the freeze manifest, run the full acceptance suite, obtain CODEOWNER review, and publish only after the protected workflow passes.
