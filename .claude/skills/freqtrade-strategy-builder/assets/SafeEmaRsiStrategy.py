"""Educational Freqtrade strategy template.

The rules are intentionally simple. They are not a claim of profitability.
Backtest, inspect for bias, test out of sample, and use dry-run before considering
real capital.
"""

from functools import reduce

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy


class SafeEmaRsiStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 210

    minimal_roi = {
        "0": 0.06,
        "240": 0.03,
        "720": 0.01,
        "1440": 0.0,
    }
    stoploss = -0.10
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    buy_rsi = IntParameter(25, 45, default=35, space="buy", optimize=True)
    sell_rsi = IntParameter(55, 80, default=68, space="sell", optimize=True)
    buy_volume_factor = DecimalParameter(
        0.8, 2.0, default=1.0, decimals=1, space="buy", optimize=True
    )

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(20).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            dataframe["ema_fast"] > dataframe["ema_slow"],
            dataframe["close"] > dataframe["ema_fast"],
            dataframe["rsi"] < self.buy_rsi.value,
            dataframe["rsi"] > dataframe["rsi"].shift(1),
            dataframe["volume"]
            > dataframe["volume_mean_20"] * self.buy_volume_factor.value,
            dataframe["volume"] > 0,
        ]
        dataframe.loc[reduce(lambda left, right: left & right, conditions), "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            (dataframe["rsi"] > self.sell_rsi.value)
            | (dataframe["ema_fast"] < dataframe["ema_slow"]),
            dataframe["volume"] > 0,
        ]
        dataframe.loc[reduce(lambda left, right: left & right, conditions), "exit_long"] = 1
        return dataframe

