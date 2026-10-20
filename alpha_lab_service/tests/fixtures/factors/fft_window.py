import numpy as np
import pandas as pd
import pywt
from scipy.fft import rfft


def compute_factor(history, *, as_of, params):
    window = int(params.get("lookback", 3)) + 1
    result = {}
    for symbol, rows in history.groupby(level="symbol", sort=True):
        values = rows["close"].tail(window).to_numpy(dtype=float)
        spectrum = np.abs(rfft(values))
        coefficients = pywt.wavedec(values, "haar", level=1)
        result[symbol] = float(spectrum.sum() + np.abs(coefficients[-1]).sum())
    return pd.Series(result)
