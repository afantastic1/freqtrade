# Experiment policy

## Data access and evidence integrity

Classify every input as `EXPLORATORY`, `REFERENCE_CONTAMINATED`, `LOCKED`, or `INDEPENDENT_TEST`. Prefer a controlled loader with path allowlist, partition/date validation, permission checks, content hash, and an audit event. A prompt-level rule is not a sandbox: if raw files are directly accessible, report the access-control gap and do not claim the lock is enforced.

BTC 2026 is `LOCKED` by default. An independent test can be accessed only after explicit human approval and through a frozen, read-only snapshot. Freeze expression, code, thresholds, target, and cost protocol before that run. A viewed period cannot later be presented as untouched OOS evidence. Preserve failed attempts and selection history.

## Registration, budgets, and deduplication

Before evaluation, record the hypothesis, expected sign, horizon, falsification rule, input requirements, expression set, and test-family membership. Apply the configured budgets; suggested design caps are 10 rounds, 3–5 hypotheses per round, and 2 expressions per hypothesis. Do not increase the search budget or alter thresholds to rescue weak results without human review.

Check duplicates using canonical expression/parameter hashes, semantic-family search, correlation on a fixed exploratory window, then conditional incremental value against relevant baselines. Similarity flags are evidence for review, not proof of equivalence. Do not invent factors to satisfy a field/operator quota.

## Tradability on a small account (short-cycle)

Keep **predictive evidence** and **absolute tradability** separate; a statistically significant IC is not proof the trade is executable. For a small account in the low hundreds of USDT and short-cycle cadence, evaluate the candidate against the repository's single source of truth (`experiments/risk_model.RiskCfg` plus `lib/market_rules.py`) and record the verdict with `tradability record`:

- minimum order amount and minimum notional (which tighten as price rises), fees, slippage buffer, and funding paid over the actual holding duration;
- leverage, stop distance, and per-trade risk (must stay within `max_model_risk`, default 2%);
- the verdict is `PASS`, `FAIL`, or `INSUFFICIENT`; with an unknown or non-covering predictive edge the best obtainable verdict is `INSUFFICIENT`. Never relax a risk limit to satisfy a minimum order size.

When short-cycle, register `frequency` on the hypothesis and `timeframe` / `horizon_duration` / `capital_usdt` on the experiment so these keys cannot collide with the older 24h experiments, and keep each duration window a separate experiment.

## Persistence and stopping

Use parameterized project interfaces and append-only revisions. A cache identity should include factor, data hash, configuration, code version, and target specification; for short-cycle work also include the K-line timeframe and the horizon duration in the target specification. Separate horizons and target definitions. Never directly edit historical evaluations or overwrite a failed run.

Prior heterogeneous records (for example an older `registry.jsonl`) are archived verbatim through `legacy record` / `import`; only records with an executable factor definition are mapped into `factors`/`experiments`, and the raw record is always preserved. Do not discard or rewrite the source file.

Stop when a guard fails, sample or evidence is insufficient, the budget is exhausted, no defensible hypothesis remains, or required tooling is absent. Return the observed evidence, failure reason, remaining uncertainty, and next permitted action. Do not imply a successful research run when no controlled evaluator or persistence layer is present.
