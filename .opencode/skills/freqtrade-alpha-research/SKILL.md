---
name: freqtrade-alpha-research
description: Use when researching causal quantitative factors with local Freqtrade market data, reviewing factor evidence, continuing prior research, running short-cycle (5m–4h) research on a small (about 300 USDT) account, judging 300 USDT tradability, or validating a qualified candidate with Freqtrade. Never enable live trading or unlock protected data autonomously.
compatibility: opencode
metadata:
  opencode/slash: "true"
---

# Freqtrade Alpha Research

Run a repeatable factor research loop in this repository. The goal is credible, reproducible evidence, including well documented negative results; finding a profitable factor is not required.

## Start safely

1. Inspect the repository, current Freqtrade version, existing research code and data inventory before proposing work. Reuse existing modules and audited risk controls. Do not claim a design feature exists until its implementation is found.
2. Read [experiment-policy.md](references/experiment-policy.md) for every run. Read [research-protocol.md](references/research-protocol.md) before implementing or evaluating a factor. Read [factor-catalog.md](references/factor-catalog.md) when importing, proposing, or deduplicating factors. Read [freqtrade-adapter.md](references/freqtrade-adapter.md) before strategy work or a backtest.
3. Use the controlled registry CLI below. It writes only beneath `user_data/research`, returns JSON, and is append-only (INSERT only; history is never updated or deleted, corrections use a new revision with `supersedes_id`). Payloads for write commands are JSON files (`--file`). Do not bypass it with ad hoc SQL or direct dataset access.

```bash
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py init
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py inspect
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py dataset register --file ds.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py dataset approve --dataset-id DS-LOCK --purpose "..." --approved-by human:<id>
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py hypothesis add --file hypothesis.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py factor add --file factor.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py experiment add --file experiment.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py evaluation add --file evaluation.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py tradability record --file tradability.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py legacy record --file legacy.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py import --file bundle.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py run start --file run.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py run finish --run-id R1 --status DONE --file decision.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py selection record --file selection.json
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py registry counts
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py registry show --table factors --limit 50
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py registry show --table experiments --timeframe 15m --limit 50
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py catalog search --query momentum --limit 20
```

Registered factors are metadata plus a canonical `formula_ast`/parameters hash (duplicate instances are rejected, not duplicated). Factor computation, causal checks and statistics are performed by the repository's research package (for example `user_data/research/lib` and a `run_*.py` driver); the CLI persists the resulting hypotheses, factors, experiments and evaluations, and appends an `audit_events` row per write. **Dataset access control is enforced in code**: registering an experiment on a `LOCKED` or `INDEPENDENT_TEST` dataset is rejected unless a `dataset approve` (human `approved_by`) has been recorded. Freqtrade backtesting remains a separate, gated step.

`import` writes one JSON bundle (datasets → hypotheses → factors → experiments → evaluations → tradability → legacy → selections) through the same controlled path, so bulk runners can persist without ad hoc SQL. `legacy record` archives a prior heterogeneous `registry.jsonl` record verbatim (append-only, idempotent by legacy id); the shipped `migrate_registry.py` maps only records that carry an executable `factor_expr` into structured tables and preserves everything else raw.

## Short-cycle research (5m–4h) and the 300 USDT tradability gate

The same loop also supports short-cycle research on a small (about 300 USDT) account. Two axes are decoupled: the **K-line timeframe** (`--tf`: 5m/15m/30m/1h/2h/4h; 30m and 2h are derived from 15m/1h) controls how often you may act, while the **prediction horizon** is a duration (`15m/30m/1h/2h/4h`) converted to bars per timeframe. A horizon that is not an integer multiple of the timeframe must raise an error, never be rounded.

```bash
# 15m panel, executable next-bar-open labels, IC/RankIC/ICIR/NW-t/quantiles/orthogonal/BH-FDR, then tradability
python3 user_data/research/run_short.py --tag SC15M-2022-2024 --tf 15m --start 2022-01-01 --end 2024-12-31
python3 .opencode/skills/freqtrade-alpha-research/scripts/alpha.py import \
  --file user_data/research/results/short/SC15M-2022-2024.bundle.json
```

- Reuse `user_data/research/lib` (`data.merged(tf)`, `context.build_context(..., scale=)`, `shortcycle.py`, `alpha_eval.py`). The short-cycle loader is deliberately more conservative than the 1h loader: decision time is the bar close, mark availability is `mark_ts+1h`, funding availability is the settlement time.
- Judge **predictive edge and tradability separately**. The gate in `user_data/research/lib/tradability.py` returns `PASS` / `FAIL` / `INSUFFICIENT` using only `RiskCfg` thresholds and `market_rules` exchange limits: minimum order amount/notional, fees, slippage buffer, funding over the holding period, leverage, stop distance, and per-trade risk ≤ `max_model_risk` (2%). With no predictive edge the best verdict is `INSUFFICIENT`; it is never silently `PASS`.
- Record every result with `--timeframe` / `horizon_duration` / `capital_usdt` so short-cycle cache keys cannot collide with the older 24h experiments. Do not add a parallel database, agent framework, or workflow engine; extend the existing one.

## Default limits

- Research and backtesting only. Never place orders, start live trading, or enable exchange write access.
- Treat 2022–2024 as exploratory and 2025 as reference contaminated. Treat BTC 2026 as `LOCKED`; do not read it, list its contents, or pass it into an experiment without explicit human approval and an approved read-only snapshot mechanism. A prompt instruction alone is not a data access control.
- Do not call a dataset independent OOS if its period has already influenced prior research decisions. Preserve exposure and selection history.
- Treat the frozen A (unconditional breakout) and B (cool funding) BTC candidates as NO-GO baselines. Do not silently revise their prior conclusions. Locate their evidence during inspection and report if it is unavailable.
- Keep proposed work within the configured proposal and evaluation budgets. Design defaults are at most 10 rounds, 3–5 hypotheses per round and 2 preregistered expressions per hypothesis. Stop when the budget is exhausted or no defensible new hypothesis remains.
- Never let the model write experiment history with arbitrary SQL or overwrite prior results. Use the repository's controlled, append-only interface; if none exists, provide a concrete implementation gap instead of simulating persistence.

## Research sequence

Follow this order and record each stage's outcome:

`inspect → retrieve → hypothesize → implement → check → evaluate → critique → [backtest] → persist → [repeat]`

- **Inspect / retrieve:** establish available data, permissions, prior hypotheses, failures, baselines, code version, and tool availability.
- **Hypothesize:** state mechanism, expected direction, horizon, required fields, and a falsification condition before evaluation. Use [templates/hypothesis.yaml](templates/hypothesis.yaml) when a repository interface accepts this contract.
- **Implement / check:** use deterministic, auditable code and an approved operator set. Check timestamps, availability, missingness, lookahead, duplicates, parameters, and reproducibility. Never proceed directly to evaluation or backtesting after a failed check.
- **Evaluate / critique:** keep each horizon and label definition distinct; report uncertainty, selection history, multiple-testing context, robustness, and economic interpretation. Record failures as useful outcomes.
- **Backtest (optional):** only a `PROMISING` or better candidate with explicit executable entry and exit rules may enter strategy research. This is a new experiment with separately recorded choices and costs.
- **Persist / repeat:** append the result and artifacts through controlled project tooling, critique the evidence, then choose `REFINE`, `EXPLORE`, `CONDITIONAL`, or stop. If persistence tooling is absent, return the proposed record and identify the missing interface.

## Evidence and outcomes

Keep research status separate from evidence level. Use `REJECTED`, `INSUFFICIENT`, `PROMISING`, or `VALIDATED` for research outcomes, and `EXPLORATORY`, `INTERNAL_OOS`, `INDEPENDENT_OOS`, or `DEPLOYMENT_READY` for evidence. Historical IC significance alone cannot establish `VALIDATED`. Never claim `DEPLOYMENT_READY` without frozen independent evidence plus execution, cost, and risk checks and human approval.

Stop and report a blocker when a required permission guard, causal check, evidence record, budget control, or cost input is unavailable. Ask for human approval only for the specific protected-data access or other gated action that needs it; continue independent inspection and reporting.
