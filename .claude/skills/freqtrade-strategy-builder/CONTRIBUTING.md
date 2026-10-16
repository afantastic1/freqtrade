# Contributing

Thank you for helping improve Freqtrade Strategy Builder.

## Before opening a pull request

1. Do not include exchange keys, tokens, account identifiers, private configs,
   trading logs, or proprietary market data.
2. Preserve `dry_run: true` in all examples.
3. Do not weaken the live-trading gate in `scripts/ft.sh`.
4. Keep `SKILL.md` focused and move detailed material into `references/`.
5. Add or update tests for behavioral changes.
6. Run:

   ```bash
   chmod +x scripts/*.sh
   ./scripts/smoke_test.sh
   bash -n scripts/*.sh
   agentskills validate .
   ```

## Strategy examples

Every contributed strategy must:

- Use the current Freqtrade strategy interface
- Be described as educational, not profitable or guaranteed
- Avoid lookahead bias
- Use vectorized dataframe operations
- Include positive-volume guards
- Declare a sufficient startup candle count
- Contain no credentials or account-specific values

## Pull request description

Explain:

- What changed
- Why the change is needed
- How it was tested
- Any safety or compatibility impact

Changes that remove confirmations, encourage secret sharing, or start live
trading by default will not be accepted.

