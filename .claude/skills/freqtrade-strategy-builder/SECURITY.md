# Security Policy

## Supported version

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Do not open a public issue for:

- Credential exposure
- Command injection
- Live-trading gate bypass
- Unsafe secret handling
- A flaw that could place unintended orders

Use GitHub's private vulnerability reporting feature for this repository. If
that feature is unavailable, contact the repository owner privately through
their verified GitHub profile.

Do not include real API keys, secrets, account numbers, wallet addresses, or
private trading logs in a report. Use synthetic values and the minimum
reproduction needed.

## Operational warning

This repository cannot eliminate trading, exchange, market, or infrastructure
risk. Review every script and configuration before use. Start with backtesting,
then dry-run, and use the smallest separately approved capital if a strategy is
eventually promoted to live trading.

