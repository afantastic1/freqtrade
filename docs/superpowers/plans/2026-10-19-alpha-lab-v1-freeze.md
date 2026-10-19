# Alpha Lab v1.0 Repair and Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair Alpha Lab evidence integrity and inference, isolate candidate execution, and establish a tested v1.0 freeze baseline.

**Architecture:** A trusted engine owns identity, evaluation, artifacts, and SQLite; candidate Python executes through a fail closed Bubblewrap worker. Canonical hashes connect configuration, code, bounded data, artifacts, and append only evidence.

**Tech Stack:** Python 3.13, pytest, SQLite, pandas/numpy/scikit-learn, Bubblewrap, OpenCode v2.0.29, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-19-alpha-lab-v1-freeze-design.md`

## Global Constraints

- Preserve all current work and historical evidence.
- Never inspect 2025+ held out data during acceptance.
- Use RED→GREEN for production behavior changes.
- Produce Commit A for repairs and Commit B for freeze, isolation, permissions, and docs.
- Do not push, tag, trade, or modify the Freqtrade execution engine.

## Review Focus

- Same review identity with changed evidence must raise and preserve the original row.
- Same size and mtime data replacement must invalidate reuse through content hashing.
- Missing prediction/evidence artifacts must invalidate reuse.
- Opposite overall/monthly RankIC direction must never produce SUPPORTED.
- Sandbox attempts to write engine/SQLite, read private files, execute commands, or connect to the network must fail.

---

### Task 1: Append only persistence and review identity

**Files:** Modify `alpha_lab/research_store.py`, `alpha_lab/prereg_record.py`, `alpha_lab/regime_cli.py`; test `alpha_lab/tests/test_research_store.py`, `test_prereg_record.py`, `test_regime_cli.py`.

- [ ] Write and witness failing identity, conflict, A-1/A-2, concurrency, FK, backup, duplicate index, and recovery tests.
- [ ] Add transactional compatible migration, explicit analysis/config identity, canonical equality checks, and policy sourced thresholds.
- [ ] Run focused and full Alpha Lab suites; commit as part of Commit A.

### Task 2: Statistical inference and mechanism direction

**Files:** Modify `alpha_lab/stats/paired_bootstrap.py`, `rank_ic.py`, `audit.py`, method versions; add statistical and Golden tests.

- [ ] Write failing direction matrix and null Monte Carlo tests.
- [ ] Implement centered null p values, RankIC specific uncertainty, conflict verdicts, and method version bumps.
- [ ] Record old/new Golden differences and run focused/full tests.

### Task 3: Complete research identity and artifact reuse

**Files:** Modify `alpha_lab/runner.py`, `experiment_registry.py`, `config.py`; tests in runner suites.

- [ ] Write failing same size/mtime data, source, statistics, missing predictions, corrupt manifest, and failed completion tests.
- [ ] Hash data contents and all participating code/config; generate artifact hashes and validate every required artifact before reuse.
- [ ] Run focused/full tests and create Commit A.

### Task 4: Candidate execution sandbox

**Files:** Create worker/sandbox modules; modify candidate loader/runner; add malicious candidate integration tests.

- [ ] Write failing tests for engine/SQLite writes, private reads, commands, network, and unavailable sandbox.
- [ ] Implement Bubblewrap fail closed worker and trusted result validation.
- [ ] Run focused security and full tests.

### Task 5: Freeze, permissions, protocol, and CI

**Files:** Add protocol/version/freeze manifest/checker, GitHub workflow/CODEOWNERS, update OpenCode agent/skill and Alpha Lab guide.

- [ ] Verify OpenCode v2.0.29 permission parsing with a repository config and default deny matrix.
- [ ] Add freeze checker tests and Golden fixtures; document hosted manual controls.
- [ ] Generate manifest, run acceptance suite, and create Commit B.

### Task 6: Final verification and review

- [ ] Run compileall, full Alpha Lab pytest, diff check, SQLite checks, concurrency, malicious sandbox, Golden, Monte Carlo, and end to end smoke commands.
- [ ] Package and obtain a fresh whole branch review; fix Critical/Important findings with RED→GREEN.
- [ ] Report exact passed results, remaining gaps, two commits, and conditional release recommendation.
