---
name: alpha-research
description: Use when researching a bounded Alpha Lab time-series or cross-sectional hypothesis with existing development data and recording an evidence-grounded decision.
compatibility: opencode
metadata:
  opencode/slash: "true"
---

# Alpha Research

Complete one bounded research cycle. Record the hypothesis, direction, target, timeframe, horizon, Universe, date interval, confounders, and rejection criterion before submitting the experiment. Read SQLite history and existing decisions before selecting a hypothesis. Treat 2022–2024 as repeatedly used development data, never fresh out-of-sample evidence. Do not touch 2025+ data, live trading, Freqtrade execution, Alpha Lab source, tests, labels, thresholds, the database file, or historical artifacts.

## Current execution gate

The legacy single-asset `factor.py` loader imports arbitrary Python in the engine process. Its OS isolation has not passed adversarial tests. **Do not invoke `candidate_cli run` or execute candidate Python through any other tool.** You may write a proposed per-asset candidate for Maintainer review, but do not execute it unattended. The `time_series` interface and historical evidence remain readable.

The v1.1 cross-sectional entry point executes only engine-owned fixed transforms. Candidate config files contain data and transform names, never Python code. Use `relative_momentum`, `relative_volatility`, or `relative_volume` with positive lookback periods. Any new transform or evaluator requires a Maintainer change.

## Research procedure

1. Write `user_data/research/alpha_lab/candidates/current/request.json` with `{"action":"history"}` and invoke `.venv/bin/python -m alpha_lab.research_agent_cli` to inspect prior runs. For one run, set `{"action":"show","run_id":"..."}` and invoke the same exact command. Do not use a deduplicated run to avoid an existing multiple-testing family.
2. State one falsifiable mechanism, predicted sign, target, timeframe (5m, 15m, 1h, or 4h), horizon, fixed or dynamic Universe, date interval, known confounders, and rejection criterion before running.
3. For cross-sectional research, write `user_data/research/alpha_lab/candidates/current/config.json`. Select a Maintainer-prepared panel by ID; do not read or grep the panel file. The gateway is the only Agent interface to panel data. Use the exact v1.1 config contract in `docs/alpha_lab/research-protocol-v1.1-draft.md`.
4. Write `{"action":"cross_sectional_run","panel_id":"<snapshot_stem>"}` to the fixed request file and invoke `.venv/bin/python -m alpha_lab.research_agent_cli`. Do not pass shell arguments, operators, or extra commands.
5. Read run Evidence through `{"action":"show","run_id":"..."}` and a declared campaign through `{"action":"family_review"}`. Do not open `runs/**` artifacts directly. Inspect Universe membership, valid sections, coverage, CS-IC/RankIC, bootstrap, HAC, MDE, quantile spread, turnover, cost sensitivity, and baseline comparison returned by the gateway. `NOT_EVALUABLE` and `INCONCLUSIVE` are evidence states, not zero scores. Two-asset ranks are insufficient for this evaluator.
6. Write `{"action":"decide","run_id":"...","source_hash":"...","decision":{"supporting_evidence":[],"opposing_evidence":[],"conclusion":"INCONCLUSIVE","next_hypothesis":"...","next_actions":["ARCHIVE"],"agent_identity":"opencode-alpha-research"}}` to the fixed request file and invoke the same exact command. Never insert or update SQLite directly.

## Interpretation rules

- The research label uses each asset's next open and later close as reference prices. With delayed candle availability, the next open can precede the decision time. This is a prediction proxy, not executable PnL.
- Each UTC time section is one inference observation. Multiple assets at one time are not independent time samples. A significant unadjusted p value is not a final supported claim; include the declared development-set multiple-testing family.
- A frozen fixed pool can have survivorship bias. A dynamic pool must use only prior available candles, listing age, turnover, and coverage. Do not fill absent candles or put a newly listed asset into earlier sections.
- Distinguish objective Evidence from the Research Decision. Report opposing results and the exact run and config hashes. If the required evidence or decision command is missing, stop and report the actual blocker.

## Handoff

Report the hypothesis, config and panel IDs, timeframe, horizon, Universe, mode, run ID, reuse status, data and code hashes, supported and opposing evidence, decision ID if available, and unresolved limitations. Stop after one cycle.
