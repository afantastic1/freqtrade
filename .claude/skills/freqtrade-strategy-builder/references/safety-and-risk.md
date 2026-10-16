# Safety, secrets, and operational risk

## Secrets

- Use a dedicated exchange API key.
- Disable withdrawals.
- Restrict the key to only the required market and operations.
- Use an IP allowlist where available.
- Prefer a dedicated sub-account with limited funds.
- Store secrets outside the repository.
- Add private config and environment files to `.gitignore`.
- Redact secrets from logs, screenshots, issue reports, and agent conversations.
- Rotate a key immediately if it appears in plaintext.

The example config intentionally contains blank credential fields. The bundled
validator fails when it detects non-placeholder plaintext values under common
secret keys.

## Live gate

An agent must not start live trading until the user has reviewed:

- Exact exchange and account/sub-account
- Spot or futures mode
- Pair whitelist
- Strategy and version
- Stake amount
- Maximum open trades
- Stop-loss and protections
- Expected drawdown and hard loss limit
- Monitoring and emergency stop procedure

After conversational approval, the wrapper still requires:

```bash
export FREQTRADE_I_UNDERSTAND_LIVE_RISK=YES
./scripts/ft.sh live --confirm-live ...
```

Do not remove, bypass, or automate these gates.

## Futures

Futures and leverage add liquidation, funding-rate, margin, mark-price, and
exchange-specific risks. Validate all of them independently. A strategy that
works in spot mode is not automatically suitable for futures.

## Operational controls

Before live deployment:

- Pin and record the Freqtrade image/version.
- Review release notes before upgrades.
- Test restoration and restart behavior.
- Verify time synchronization.
- Set disk, log, process, and connectivity monitoring.
- Configure alerts for rejected orders, repeated errors, stale data, and drawdown.
- Document how to stop the bot and cancel open orders.
- Keep a manual exchange login path for emergencies.

## Financial interpretation

Backtests are exposed to overfitting, regime changes, incomplete data, optimistic
fills, latency, slippage, fees, spread, liquidity, and exchange outages. Dry-run
also cannot fully reproduce queue position or live execution. Use the smallest
approved capital when moving to live and scale only after observed evidence.

