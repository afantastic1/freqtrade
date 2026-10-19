#!/usr/bin/env python3
"""Read-only integration acceptance for the local development catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_engineering.local_acceptance import audit_local_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("user_data/data_engineering"))
    args = parser.parse_args(argv)
    print(json.dumps(audit_local_catalog(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
