import pytest

from alpha_lab.data.timeframe import timeframe_to_seconds
from freqtrade.exchange import timeframe_to_seconds as freqtrade_timeframe_to_seconds


@pytest.mark.parametrize(
    "timeframe",
    ["1s", "1m", "5m", "15m", "30m", "1h", "2h", "4h", "12h", "1d", "3d", "1w", "1M", "1y"],
)
def test_timeframe_conversion_matches_freqtrade_for_supported_units(timeframe):
    assert timeframe_to_seconds(timeframe) == freqtrade_timeframe_to_seconds(timeframe)


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h", "4h"])
def test_timeframe_conversion_matches_factor_research_periods(timeframe):
    assert timeframe_to_seconds(timeframe) == freqtrade_timeframe_to_seconds(timeframe)
