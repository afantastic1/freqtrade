"""Universe readiness and payload-budget audit.

Additive: this module reads files and computes arithmetic. It changes no frozen
evaluator, cost model, S1 gate, campaign policy or Evidence record, and it
imports nothing from the service.

Two questions it answers, both of which must be settled *before* widening the
cross-section:

1. Readiness -- which symbols actually have local history, from when to when, and
   with what gaps. A symbol that starts late is not evidence of a factor, it is
   evidence of a listing.
2. Payload budget -- the Worker frames are canonical JSON, so bytes scale with
   ``steps * lookback_bars * symbols * bytes_per_row``. The frozen service limits
   are 16 MiB per chunk, 512 MiB of input per task and 512 steps per chunk.
   More symbols therefore shrinks both the steps-per-chunk cap and the window a
   single task can cover. This is arithmetic on measured bytes, not a guess.

Eligibility itself is *not* reimplemented here: ``alpha_lab.data.universe``
already owns the causal membership rules (DynamicUniverse / HistoricalUniverse).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

# Mirrors alpha_lab_service.protocol_v2.FactorServiceLimitsV2 (frozen v1).
MAX_INPUT_BYTES_PER_CHUNK = 16 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES = 512 * 1024 * 1024
MAX_STEPS_PER_CHUNK = 512
MAX_TOTAL_STEPS = 100_000

BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}


def scan_directory(root: Path, timeframes=("5m", "15m", "1h", "4h")) -> pd.DataFrame:
    """Spot is ``{BASE}_USDT-{tf}.feather``; futures is ``{BASE}_USDT_USDT-{tf}-futures.feather``.

    Only the timestamp column is read, so this is cheap even for a wide universe.
    """
    records: list[dict] = []
    for timeframe in timeframes:
        for market, directory, suffix in (
            ("spot", root, f"-{timeframe}.feather"),
            ("futures", root / "futures", f"-{timeframe}-futures.feather"),
        ):
            if not directory.exists():
                continue
            for path in sorted(directory.glob(f"*{suffix}")):
                stem = path.name[: -len(suffix)]
                symbol = (stem.replace("_USDT_USDT", "USDT")
                          if market == "futures" else stem.replace("_USDT", "USDT"))
                stamp = pd.read_feather(path, columns=["date"])["date"]
                if stamp.empty:
                    continue
                times = pd.to_datetime(stamp, unit="ms", utc=True)
                days = (times.max() - times.min()).total_seconds() / 86400.0
                expected = days * BARS_PER_DAY[timeframe]
                records.append({
                    "symbol": symbol, "market": market, "timeframe": timeframe,
                    "bars": int(len(times)),
                    "first": str(times.min()), "last": str(times.max()),
                    "calendar_days": round(days, 1),
                    "gap_fraction": round(max(0.0, 1.0 - len(times) / expected), 4) if expected else 0.0,
                })
    return pd.DataFrame(records)


def budget(n_symbols: int, lookback_bars: int, bytes_per_row: float,
           steps: int) -> dict:
    """Worker-frame arithmetic against the frozen service limits."""
    per_step = lookback_bars * n_symbols * bytes_per_row
    steps_by_chunk_bytes = int(MAX_INPUT_BYTES_PER_CHUNK / per_step) if per_step else 0
    effective_steps_per_chunk = max(1, min(steps_by_chunk_bytes, MAX_STEPS_PER_CHUNK))
    steps_by_total_bytes = int(MAX_TOTAL_INPUT_BYTES / per_step) if per_step else 0
    capped_steps = max(0, min(steps, MAX_TOTAL_STEPS, steps_by_total_bytes))
    chunks = -(-capped_steps // effective_steps_per_chunk) if effective_steps_per_chunk else None
    return {
        "n_symbols": n_symbols,
        "lookback_bars": lookback_bars,
        "bytes_per_step": int(per_step),
        "max_steps_per_chunk": effective_steps_per_chunk,
        "default_512_steps_per_chunk_fits": bool(
            per_step * MAX_STEPS_PER_CHUNK <= MAX_INPUT_BYTES_PER_CHUNK),
        "max_steps_by_total_bytes": steps_by_total_bytes,
        "requested_steps": steps,
        "feasible_steps": capped_steps,
        "chunks": chunks,
        "total_bytes": int(capped_steps * per_step),
    }


def main() -> None:
    root = Path("user_data/data/binance")
    table = scan_directory(root)
    if table.empty:
        print("no local binance data found")
        return

    print("=== local inventory ===")
    print(f"{'tf':>5s}{'market':>9s}{'symbols':>9s}{'bars(min,max)':>18s}"
          f"{'first':>22s}{'last':>22s}{'max_gap':>9s}")
    for (timeframe, market), group in table.groupby(["timeframe", "market"]):
        print(f"{timeframe:>5s}{market:>9s}{group.symbol.nunique():9d}"
              f"{group.bars.min():9d},{group.bars.max():<8d}"
              f"{group.first.min()[:19]:>22s}{group.last.max()[:19]:>22s}"
              f"{group.gap_fraction.max():9.4f}")
    print(f"\ndistinct assets: {len(set(table.symbol.unique()))} -> "
          f"{sorted(table.symbol.unique())}")

    bytes_per_row = 349.4   # measured on r6c1h6m10 canonical JSON frames
    print(f"\n=== payload budget (measured {bytes_per_row:.1f} B/row, canonical JSON) ===")
    print("days/task = how much calendar history ONE request can cover "
          "(512 MiB total input, lookback 48)")
    print(f"{'sym':>4s}{'tf':>5s}{'KiB/step':>10s}{'steps/chunk':>12s}"
          f"{'steps<=512MiB':>14s}{'days/task':>11s}{'chunks/month':>13s}")
    for n_symbols in (5, 10, 20, 30, 50):
        for timeframe in ("4h", "1h", "15m", "5m"):
            steps_month = BARS_PER_DAY[timeframe] * 30
            result = budget(n_symbols, 48, bytes_per_row, steps_month)
            days = result["feasible_steps"] / BARS_PER_DAY[timeframe]
            chunks_month = result["chunks"] if result["feasible_steps"] >= steps_month else None
            print(f"{n_symbols:4d}{timeframe:>5s}"
                  f"{result['bytes_per_step'] / 1024:10.1f}"
                  f"{result['max_steps_per_chunk']:12d}"
                  f"{result['max_steps_by_total_bytes']:14d}"
                  f"{days:11.1f}"
                  f"{(chunks_month if chunks_month else '-'):>13}")

    output = Path("user_data/research/alpha_lab_rounds/universe_readiness.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "inventory": table.to_dict("records"),
        "distinct_symbols": sorted(table.symbol.unique()),
        "bytes_per_row_measured": bytes_per_row,
        "bytes_per_row_source": "r6c1h6m10 worker frame, canonical JSON, 12 columns",
        "budget": [budget(n, 48, bytes_per_row, BARS_PER_DAY[tf] * 30)
                   for n in (5, 10, 20, 30, 50)
                   for tf in ("4h", "1h", "15m", "5m")],
        "limits": {"max_input_bytes_per_chunk": MAX_INPUT_BYTES_PER_CHUNK,
                   "max_total_input_bytes": MAX_TOTAL_INPUT_BYTES,
                   "max_steps_per_chunk": MAX_STEPS_PER_CHUNK,
                   "max_total_steps": MAX_TOTAL_STEPS},
    }, indent=2))
    print(f"\nwrote {output}")


if __name__ == "__main__":
    main()
