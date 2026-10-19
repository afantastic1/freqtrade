import pandas as pd
import pytest

from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.data.cross_sectional_dataset import CrossSectionalDataset
from alpha_lab.labels.cross_sectional import cross_sectional_labels


def _fixture():
    times = pd.date_range("2024-01-01", periods=8, freq="h", tz="UTC")
    index = pd.MultiIndex.from_product([times, ["A", "B", "C"]], names=["open_time", "symbol"])
    frame = pd.DataFrame({"open": [10 + i % 3 + i // 3 for i in range(len(index))],
                          "available_time": [t + pd.Timedelta(hours=1) for t, _ in index]}, index=index)
    frame["close"] = frame.open
    panel = MarketPanel(frame)
    member = pd.Series(True, index=index)
    labels = cross_sectional_labels(panel, member, timeframe="1h", horizon=2)
    dataset = CrossSectionalDataset(panel, member, {"feature": frame.close}, labels)
    return dataset, times


def test_global_utc_split_purges_unavailable_labels():
    dataset, times = _fixture()
    train, test = dataset.split(train_end=times[4], test_end=times[6])
    assert train.index.get_level_values("open_time").max() < times[4]
    assert test.index.get_level_values("open_time").min() >= times[4]
    assert (train.label_available_time <= times[4]).all()
    assert set(train.index.get_level_values("open_time")).isdisjoint(
        set(test.index.get_level_values("open_time")))
    assert len(test) == 6


def test_future_feature_availability_is_rejected():
    dataset, times = _fixture()
    late = dataset.panel.frame.copy()
    late.loc[(times[1], "A"), "available_time"] = times[5]
    with pytest.raises(ValueError, match="availability"):
        CrossSectionalDataset(MarketPanel(late), dataset.membership,
                              dataset.features, dataset.labels)
