# Freqtrade command reference

Run the wrapper from the directory containing `docker-compose.yml`, or set
`FT_COMPOSE_FILE`.

## Project

```bash
./scripts/ft.sh doctor
./scripts/ft.sh version
```

## Data

```bash
./scripts/ft.sh download-data \
  --config user_data/config.json \
  --pairs BTC/USDT ETH/USDT \
  --timeframes 1h \
  --days 730

./scripts/ft.sh list-data --show-timerange
```

For futures data, use a futures configuration and the appropriate trading-mode
options supported by the installed Freqtrade version.

## Backtest

```bash
./scripts/ft.sh backtest \
  --config user_data/config.json \
  --strategy SafeEmaRsiStrategy \
  --timerange 20240101-20251231 \
  --export trades
```

Do not use cached results after strategy, configuration, fee, pair-universe, or
data changes unless you have verified the cache key is still valid.

## Bias checks

```bash
./scripts/ft.sh lookahead-analysis \
  --config user_data/config.json \
  --strategy SafeEmaRsiStrategy \
  --timerange 20240101-20251231

./scripts/ft.sh recursive-analysis \
  --config user_data/config.json \
  --strategy SafeEmaRsiStrategy \
  --timerange 20240101-20251231 \
  -p 20 50 100 200
```

## Hyperopt

```bash
./scripts/ft.sh hyperopt \
  --config user_data/config.json \
  --strategy SafeEmaRsiStrategy \
  --hyperopt-loss SharpeHyperOptLossDaily \
  --spaces buy sell roi stoploss trailing \
  --epochs 200 \
  --timerange 20240101-20241231

./scripts/ft.sh hyperopt-list --best
```

## Dry-run

```bash
./scripts/ft.sh dry-run \
  --config user_data/config.json \
  --strategy SafeEmaRsiStrategy
```

## Live

Live mode requires explicit conversational approval and two local gates:

```bash
export FREQTRADE_I_UNDERSTAND_LIVE_RISK=YES
./scripts/ft.sh live --confirm-live \
  --config /absolute/path/to/private-live-config.json \
  --strategy StrategyName
```

## Official documentation

- Docker quickstart: https://www.freqtrade.io/en/stable/docker_quickstart/
- Configuration: https://www.freqtrade.io/en/stable/configuration/
- Strategy customization: https://www.freqtrade.io/en/stable/strategy-customization/
- Data download: https://www.freqtrade.io/en/stable/data-download/
- Backtesting: https://www.freqtrade.io/en/stable/backtesting/
- Hyperopt: https://www.freqtrade.io/en/stable/hyperopt/
- Lookahead analysis: https://www.freqtrade.io/en/stable/lookahead-analysis/
- Recursive analysis: https://www.freqtrade.io/en/stable/recursive-analysis/
- Leverage: https://www.freqtrade.io/en/stable/leverage/

