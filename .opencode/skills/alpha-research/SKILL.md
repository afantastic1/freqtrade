---
name: alpha-research
description: Research one reproducible BTC futures alpha-factor hypothesis using the repository's Alpha Lab. Inspect SQLite history, submit a candidate factor, run walk-forward and incremental evaluation, and persist an evidence-grounded Research Decision. Use when investigating, refining, or comparing alpha factors; never treat predictive metrics as live trading PnL.
compatibility: opencode
metadata:
  opencode/slash: "true"
---

# Alpha Research — one bounded research cycle

## Mission and boundaries

Complete **exactly one** independently traceable research iteration per invocation. Generate an economically motivated hypothesis, implement one candidate in the existing Alpha Lab contract, obtain the deterministic evidence, and record a Research Decision. Use Alpha Lab as a black-box research engine: **do not modify its metrics, validation, labeling, walk-forward, runner, research store, or candidate protocol to make a candidate pass**. Do not execute live trades or change Freqtrade trading configuration.

The current supported experimental scope is **BTC/USDT:USDT**, timeframes **1h / 15m**, the existing OHLCV candidate input (`open`, `high`, `low`, `close`, `volume`) and only the research date range permitted by `alpha_lab/config.py`. No funding, OI, spot basis, or options inputs may be invented or loaded ad hoc into factor code. Labels and predictions are research proxies, not executable PnL.

## Step 0 — preflight

From the repository root, inspect the actual CLI documentation and current code before executing commands:

```bash
python -m alpha_lab.candidate_cli --help
python -m alpha_lab.candidate_cli run --help
python -m alpha_lab.research_cli --help
python -m alpha_lab.research_cli decide --help
```

If modules, data, database, virtual environment, or commands are missing, report the precise blocker. Do not silently create replacement implementations. On the first invocation, verify that Alpha Lab unit tests have already passed on the current revision, or run `python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/ -q` if practical.

## Step 1 — recover evidence and avoid repeats

```bash
python -m alpha_lab.research_cli list --limit 30 --json
```

For relevant existing runs, request details using `python -m alpha_lab.research_cli show --run-id RUN_ID --json` and look up their `report_path` and saved Research Decisions:

```bash
python -m alpha_lab.research_cli decisions --run-id RUN_ID --json
```

Read the corresponding `summary.json` and relevant historical notes if needed. Distinguish runs with `incremental_status=not_evaluated` from completed incremental studies. Source code version, parameters, baseline, horizon and timeframe matter: similarly named candidates or source revisions must not be treated as identical. If `list` is empty, say so and use an explicit cold-start hypothesis; don't invent prior studies.

Formulate **one** novel or justified revised hypothesis. State the economic reasoning, signal sign, permitted observable inputs, prediction horizon, expected market regime, confounders, and at least one falsification criterion *before* running the experiment. Existing studies may inspire hypotheses, not silently justify claims of statistical significance. Do not choose many parameterizations and report only the best. One candidate = one preregistered factor parameterization per cycle. Similarity checks without target labels may inform design, but record any selection based on observed returns as research selection.

## Step 2 — create candidate

Create the candidate in `user_data/research/alpha_lab/candidates/<unique_snake_case_name>/` with exactly two files:

- `candidate.json`: exact keys `name`, `hypothesis`, `parameters`, `required_columns`, `prediction_horizon`.
- `factor.py`: function `compute(frame, **params)` returning a numeric `pandas.Series` with the *same index* as `frame`.

Follow `alpha_lab/factors/candidate.py` for the exact required schema and parameter limits. Candidate name must match directory name. Use only validated fields available by the current completed candle; rolling transforms must be strictly backward-looking. Handle NaN and zero price ranges without filling missing future observations. Do not import or fetch future data; do not use labels, test outcomes, future-indexed data, or trade entry price as model inputs.

Before executing experiments:

```bash
python -m alpha_lab.candidate_cli validate user_data/research/alpha_lab/candidates/<name>
```

`validate` checks submission shape, not the full FactorValidator or alpha quality. Fix manifest/source defects if rejected, without changing evaluation rules. Ensure candidate hypothesis and chosen parameters are recorded.

## Step 3 — run exactly one complete experiment

Default entry point (includes momentum 4/12 baseline, three-way model comparison, incremental evidence and Bootstrap):

```bash
python -m alpha_lab.candidate_cli run user_data/research/alpha_lab/candidates/<name>
```

Do **not** run a separate candidate-only study first. Use `--no-baseline` only when the research purpose specifically requires a single-factor exploratory run, and label incremental evidence `not_evaluated`. If the default momentum baseline is inappropriate, use the existing `--baseline-config PATH` with an explicit, *previously defined* research configuration, not a post-hoc optimized baseline. Do not use `--force` except for an explicitly documented rerun need.

Record the CLI output (`run_id`, `reused`, `evaluation_mode`, `incremental_status`, `rank_ic`, `model_auc`, `delta_auc`). A reused run is **not a new independent experiment**; choose a genuinely different hypothesis or stop and explain.

## Step 4 — inspect deterministic evidence

```bash
python -m alpha_lab.research_cli show --run-id RUN_ID --json
```

Read the referenced run's `summary.json` and `evidence_report`, including:

- Candidate source SHA-256 and archived `candidate_snapshot` identity.
- Factor valid coverage; IC, RankIC, monthly/yearly sign and stability.
- Directional model AUC / log loss / Brier, with its comparison baseline.
- Similarity **against current baseline factors only**, and matched three-model evaluation where available.
- Delta AUC, log loss and Brier; paired evaluation and common sample coverage.
- Monthly incremental performance and Bootstrap block sensitivity / confidence intervals.
- Train/test dates, prediction horizon, label availability, and any reused run status.

Treat `robust_positive` as one diagnostic, not proof or the sole decision rule. A positive point estimate is not proof of incremental predictive value. Bootstrap positive ratios are resampling frequencies, **not** posterior probabilities that a hypothesis is true. A proxy such as CLV does not establish real aggressive order flow, inventory or price impact. The research development and previously consulted periods are **not untouched final out-of-sample data**. No AUC/IC improvement is equivalent to tradeable net returns after fees, slippage, funding, order sizing or liquidation risk.

## Step 5 — append a Research Decision

Write a JSON decision file in the candidate directory, for example `research_decision.json`, using the accepted CLI schema:

```json
{
  "supporting_evidence": [
    "run_id=... evidence_report.factor_evaluation.<column>.overall.rank_ic = ..."
  ],
  "opposing_evidence": [
    "run_id=... incremental_evaluation.stability bootstrap interval includes zero"
  ],
  "conclusion": "INCONCLUSIVE: ...",
  "next_hypothesis": "A single falsifiable follow-up hypothesis, or no follow-up justified",
  "next_actions": ["REFINE"],
  "agent_identity": "opencode-alpha-research"
}
```

Decisions are **interpretations**, separate from objective evaluations. Link each claim to actual observed field values and the exact Run ID; never invent a metric or insert a hard-coded mechanism. Actions can use human-readable values such as `REFINE`, `EXPLORE_NEW`, `RETEST`, `ARCHIVE`, or `REQUEST_MORE_EVIDENCE` but are *not* deterministic acceptance labels. If the experiment fails, report failure and do not save a decision pretending it completed.

Append the decision using the **source hash from the completed run**, not an assumed value from a potentially modified working candidate:

```bash
python -m alpha_lab.research_cli decide \
  --run-id RUN_ID \
  --source-hash SOURCE_SHA256 \
  --decision-file user_data/research/alpha_lab/candidates/<name>/research_decision.json \
  --json
python -m alpha_lab.research_cli decisions --run-id RUN_ID --json
```

The second command verifies persistence. Do not edit historical decision rows or objective evidence to match your preferred narrative.

## Required final handoff

Report: hypothesis and motivation; candidate dir and source SHA-256; run ID and whether reused; precise timeframe/date/horizon/baseline; validation outcome; essential metrics with both supporting and conflicting evidence; limitations and actual Research Decision ID; one suggested next hypothesis. Explicitly report absent/unevaluated measurements. Stop after one cycle. Do not start another experiment on your own.
