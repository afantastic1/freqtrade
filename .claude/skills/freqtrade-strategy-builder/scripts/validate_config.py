#!/usr/bin/env python3
"""Static safety checks for a Freqtrade JSON configuration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {
    "key",
    "secret",
    "password",
    "token",
    "api_key",
    "api_secret",
}
PLACEHOLDERS = {"", "changeme", "replace-me", "replace_me", "none", "null"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--require-dry-run", action="store_true")
    mode.add_argument("--require-live", action="store_true")
    parser.add_argument("config", type=Path)
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    return value


def find_plaintext_secrets(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            normalized = key.lower()
            if normalized in SENSITIVE_KEYS and isinstance(child, str):
                candidate = child.strip()
                if candidate.lower() not in PLACEHOLDERS and not candidate.startswith("${"):
                    findings.append(child_path)
            findings.extend(find_plaintext_secrets(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_plaintext_secrets(child, f"{path}[{index}]"))
    return findings


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    warnings: list[str] = []

    dry_run = config.get("dry_run")
    if not isinstance(dry_run, bool):
        errors.append("'dry_run' must be an explicit boolean")
    if args.require_dry_run and dry_run is not True:
        errors.append("dry-run launch requires 'dry_run': true")
    if args.require_live and dry_run is not False:
        errors.append("live launch requires 'dry_run': false")

    exchange = config.get("exchange")
    if not isinstance(exchange, dict) or not exchange.get("name"):
        errors.append("'exchange.name' is required")

    secrets = find_plaintext_secrets(config)
    if secrets:
        errors.append(
            "possible plaintext secrets found at: "
            + ", ".join(secrets)
            + "; use a private ignored config, environment variables, or a secret manager"
        )

    pairs = exchange.get("pair_whitelist", []) if isinstance(exchange, dict) else []
    if not isinstance(pairs, list) or not pairs:
        warnings.append("exchange.pair_whitelist is empty")

    max_open_trades = config.get("max_open_trades")
    if not isinstance(max_open_trades, int) or max_open_trades < 1:
        errors.append("'max_open_trades' must be a positive integer")
    elif max_open_trades > 10:
        warnings.append("max_open_trades is greater than 10")

    if config.get("stake_amount") == "unlimited":
        warnings.append("stake_amount is unlimited; verify position sizing and max_open_trades")

    if config.get("trading_mode") == "futures":
        warnings.append("futures mode: verify leverage, liquidation, margin mode, funding, and stop-loss")

    if config.get("api_server", {}).get("enabled") is True:
        listen = str(config.get("api_server", {}).get("listen_ip_address", ""))
        if listen in {"0.0.0.0", "::"}:
            warnings.append("API server listens on all interfaces; require authentication and firewall rules")

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if errors:
        return 1

    mode = "DRY-RUN" if dry_run else "LIVE"
    print(
        f"OK: {args.config} passed static checks "
        f"(mode={mode}, exchange={exchange.get('name')}, pairs={len(pairs)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

