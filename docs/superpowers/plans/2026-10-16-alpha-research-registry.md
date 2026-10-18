# Alpha Research Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a working SQLite research registry and import two deterministic sample time-series factors through the OpenCode skill CLI.

**Architecture:** Keep the first delivery self-contained in the project-local skill. A small Python module owns schema migration and parameterized registry operations; `alpha.py` exposes JSON commands and writes only below `user_data/research`. Sample definitions are static metadata and do not read market data.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, `argparse`, JSON, SQLite WAL.

**Spec:** `docs/freqtrade_alpha_research_skill_design.md`

## Global Constraints

- Never access BTC 2026 locked data or enable live trading.
- SQLite history is append-only; use foreign keys, transactions, migrations, and parameterized statements.
- CLI writes are restricted to `user_data/research` and every command emits JSON.
- Factor formulas use an auditable AST and never execute through `eval`.
- Sample imports preserve provenance, license, availability rules, and separate parameter-instance hashes.

## Review Focus

- Repeated initialization must leave one schema version and preserve records.
- Repeated sample import must return existing factors without duplicate rows.
- A database path outside `user_data/research` must be rejected.
- Search input containing SQL metacharacters must remain data, not executable SQL.
- A failed transaction must not leave a hypothesis without its paired sample factors.

---

### Task 1: SQLite registry and schema migration

**Files:**
- Create: `.opencode/skills/freqtrade-alpha-research/scripts/registry.py`
- Create: `.opencode/skills/freqtrade-alpha-research/scripts/migrations/001_initial.sql`
- Test: `.opencode/skills/freqtrade-alpha-research/tests/test_registry.py`

**Interfaces:**
- Produces: `resolve_db_path(raw: str | None) -> Path`, `connect(db_path: Path) -> sqlite3.Connection`, `migrate(connection) -> int`, `registry_summary(connection) -> dict[str, object]`.
- Produces tables for schema migrations, hypotheses, factors, research runs, experiments, evaluations, datasets, data exposures, selection history, and audit events.

- [ ] Write focused failing tests for path confinement, migration idempotence, WAL/foreign keys, uniqueness, and rollback.
- [ ] Run those tests and confirm they fail because registry code is absent.
- [ ] Implement the versioned initial schema and registry helpers with explicit transactions.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Sample catalog and CLI

**Files:**
- Create: `.opencode/skills/freqtrade-alpha-research/scripts/catalog.py`
- Create: `.opencode/skills/freqtrade-alpha-research/scripts/alpha.py`
- Create: `.opencode/skills/freqtrade-alpha-research/scripts/__init__.py`
- Modify: `.opencode/skills/freqtrade-alpha-research/SKILL.md`
- Test: `.opencode/skills/freqtrade-alpha-research/tests/test_cli.py`

**Interfaces:**
- Consumes: registry functions from Task 1.
- Produces: `sample_catalog() -> tuple[dict[str, object], ...]`, `import_samples(connection, dry_run: bool) -> dict[str, object]`.
- Produces CLI commands `init`, `inspect`, `catalog import-samples`, and `catalog search`, each with JSON output and nonzero exit on failure.

- [ ] Write failing CLI tests for initialization, dry-run, first import, idempotent repeat import, safe search, and invalid external database paths.
- [ ] Run those tests and confirm the expected missing-command failures.
- [ ] Add two source-attributed sample factors: 24-bar close-return momentum and 24-bar realized volatility, expressed as canonical JSON AST metadata.
- [ ] Implement the CLI, atomic import, structured errors, and skill usage examples.
- [ ] Run focused tests, initialize `user_data/research/alpha.sqlite`, import samples, and inspect/search the resulting registry.
- [ ] Run formatting/static checks applicable to the new files and inspect the final diff.
