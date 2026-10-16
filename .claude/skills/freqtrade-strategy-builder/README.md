# Freqtrade Strategy Builder

[![Validate](https://github.com/dieumerci8/freqtrade-strategy-builder/actions/workflows/validate.yml/badge.svg)](https://github.com/dieumerci8/freqtrade-strategy-builder/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-compatible-blue.svg)](SKILL.md)

A Docker-first Agent Skill for building, reviewing, backtesting, optimizing, and
safely operating Freqtrade strategies with Claude Code, Codex, and other
Agent Skills-compatible coding agents.

## What it does

- Translates a trading hypothesis into explicit Freqtrade rules
- Generates and reviews `IStrategy` implementations
- Downloads historical market data
- Runs backtests and creates concise Markdown summaries
- Runs Hyperopt only after a valid baseline
- Checks lookahead bias and recursive indicator variance
- Enforces in-sample and out-of-sample evaluation
- Starts in dry-run mode by default
- Detects likely plaintext secrets in JSON configs
- Double-gates every live-trading command

This repository is an agent workflow and tooling package. It is not a trading
signal service and does not promise profitability.

## Safety by default

The included configuration:

- Uses `"dry_run": true`
- Contains no exchange credentials
- Starts the bot in a stopped state
- Uses a small static example pair list

The wrapper refuses live mode unless both of these are present:

```bash
export FREQTRADE_I_UNDERSTAND_LIVE_RISK=YES
./scripts/ft.sh live --confirm-live ...
```

An agent using this skill must also obtain explicit approval before running that
command. Do not give exchange withdrawal permissions to a bot API key.

## Installation

### Claude Code

Install as a repository-local skill:

```bash
git clone https://github.com/dieumerci8/freqtrade-strategy-builder.git \
  .claude/skills/freqtrade-strategy-builder
chmod +x .claude/skills/freqtrade-strategy-builder/scripts/*.sh
```

### Codex and other agents

Clone the repository into the directory where your client discovers Agent Skills:

```bash
git clone https://github.com/dieumerci8/freqtrade-strategy-builder.git
cd freqtrade-strategy-builder
chmod +x scripts/*.sh
./scripts/smoke_test.sh
```

Repository-local installation is the most portable approach when a client does
not provide a global skill library.

## Safe quickstart

Requirements:

- Docker with Compose v2
- `curl`
- Python 3.10 or later for the local helper scripts

Initialize a Freqtrade workspace:

```bash
./scripts/bootstrap.sh ./ft_userdata
```

The bootstrap script downloads the official stable Freqtrade Compose file,
creates `user_data`, installs the example strategy and dry-run config, and stops
without launching a bot.

Validate the environment:

```bash
cd ./ft_userdata
../freqtrade-strategy-builder/scripts/ft.sh doctor
python3 ../freqtrade-strategy-builder/scripts/validate_config.py \
  user_data/config.json
```

## Example agent request

```text
Use the freqtrade-strategy-builder skill to create a long-only BTC/USDT and
ETH/USDT strategy on the 1h timeframe. Start from the included EMA/RSI template,
download two years of data, run a baseline backtest, run lookahead-analysis and
recursive-analysis, then test the final parameters out of sample. Do not start
live trading.
```

## Manual workflow

Download data:

```bash
./scripts/ft.sh download-data --config user_data/config.json \
  --pairs BTC/USDT ETH/USDT --timeframes 1h --days 730
```

Backtest:

```bash
./scripts/ft.sh backtest --config user_data/config.json \
  --strategy SafeEmaRsiStrategy --timerange 20240101-20251231 \
  --export trades
```

Check bias:

```bash
./scripts/ft.sh lookahead-analysis --config user_data/config.json \
  --strategy SafeEmaRsiStrategy --timerange 20240101-20251231

./scripts/ft.sh recursive-analysis --config user_data/config.json \
  --strategy SafeEmaRsiStrategy --timerange 20240101-20251231 \
  -p 20 50 100 200
```

Start dry-run only:

```bash
./scripts/ft.sh dry-run --config user_data/config.json \
  --strategy SafeEmaRsiStrategy
```

## Repository structure

```text
.
├── SKILL.md
├── assets/
│   ├── SafeEmaRsiStrategy.py
│   └── config.dry-run.example.json
├── references/
│   ├── commands.md
│   ├── research-workflow.md
│   └── safety-and-risk.md
└── scripts/
    ├── analyze_backtest.py
    ├── bootstrap.sh
    ├── ft.sh
    ├── smoke_test.sh
    └── validate_config.py
```

## Validation

Run all local checks:

```bash
./scripts/smoke_test.sh
bash -n scripts/*.sh
```

The smoke test validates the example config, Python syntax, report generation,
secret detection behavior, and the dry-run/live safety gate.

## Documentation

- [Skill instructions](SKILL.md)
- [Command reference](references/commands.md)
- [Research workflow](references/research-workflow.md)
- [Security and operational risk](references/safety-and-risk.md)
- [Official Freqtrade documentation](https://www.freqtrade.io/en/stable/)

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening
a pull request. Report security issues using [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
