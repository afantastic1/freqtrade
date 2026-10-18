# Alpha Lab candidate research

Run one complete candidate study with the stable momentum(4, 12) baseline:

```bash
python -m alpha_lab.candidate_cli run path/to/candidate
```

The command validates the candidate, creates a run-local immutable snapshot, evaluates the candidate and baseline in one run, writes the detailed JSON/CSV artifacts, and indexes the compact result in `user_data/research/alpha_lab/experiments.sqlite3`.

Use `--no-baseline` for an explicit single-factor study, `--baseline-config PATH` for a complete experiment JSON with explicit factors, and `--force` to create a new execution even when an identical trustworthy completed run exists. Without `--force`, reuse requires matching research identity plus an intact summary, manifest, and candidate snapshot.

Query and repair the research index with:

```bash
python -m alpha_lab.research_cli list --limit 20
python -m alpha_lab.research_cli list --candidate intrabar_pressure_reversal --json
python -m alpha_lab.research_cli show --run-id RUN_ID --json
python -m alpha_lab.research_cli backfill --dry-run --json
python -m alpha_lab.research_cli backfill --json
```

Every new `summary.json` contains an objective `evidence_report`: run and
candidate-source identity, factor quality and IC/RankIC, model metrics,
similarity and incremental results when applicable, Bootstrap output, and the
data/Walk-Forward context. Alpha Lab does not classify the candidate, propose
an economic mechanism, or choose the next research action.

Research Agent records that judgment separately. Create a JSON file containing
`supporting_evidence`, `opposing_evidence`, `conclusion`, `next_hypothesis`, and
`next_actions`, then append it with the exact Run ID and candidate source hash:

```bash
python -m alpha_lab.research_cli decide \
  --run-id RUN_ID \
  --source-hash CANDIDATE_SOURCE_SHA256 \
  --decision-file decision.json \
  --json
python -m alpha_lab.research_cli decisions --run-id RUN_ID --json
```

Decisions are append-only rows in `research_decisions`. Creating one never
rewrites the Evidence Report, `summary.json`, predictions, or an older decision.
The command rejects an incomplete/unindexed run or a mismatched source hash.

Backfill only trusts `runs/<run_id>/summary.json` when the same `run_id` has a completed parent row in `experiments`. Orphans, malformed summaries, and identity conflicts are reported and skipped. If a run finishes its files and experiment row but indexing fails, the run command exits with an error naming backfill; it never silently reports a fully indexed success.

`run_id` identifies an execution, `candidate_id` identifies candidate source plus parameters, and `config_hash` is the complete research-condition key. Detailed predictions, fold metrics, monthly tables, and Bootstrap intervals stay in the run directory; SQLite remains a compact, recoverable query index.

Legacy summaries may still contain `research_feedback`; it remains readable as
historical policy output and is never promoted into a Research Decision.
