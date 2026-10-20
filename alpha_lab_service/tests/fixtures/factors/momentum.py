import pandas as pd


def compute_factor(history, *, as_of, params):
    close = history["close"].unstack("symbol")
    lookback = int(params.get("lookback", 3))
    if len(close) <= lookback:
        return pd.Series({symbol: 0.0 for symbol in close.columns})
    return close.iloc[-1] / close.iloc[-lookback - 1] - 1.0
