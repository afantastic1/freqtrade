
import pandas as pd

from alpha_lab.data.dataset import MarketData
from freqtrade.exchange import timeframe_to_seconds


def forward_returns(
    data: MarketData,
    horizon: int = 4,
) -> pd.DataFrame:
    """
    exact-h 收益标签。

    决策时刻：第 t 根 K 线完成且数据可用之后
    入场价格参考：O[t+1]
    离场价格参考：C[t+h]

    注意：
    这是研究用预测标签，不是可执行交易 PnL。
    """
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("horizon must be positive integer")

    df = data.frame

    step = pd.Timedelta(
        seconds=timeframe_to_seconds(data.timeframe)
    )

    # 1. 决策时间
    decision_time = df["available_time"]

    # 2. 未来参考入场与离场价格
    entry = df["open"].shift(-1)
    exit_price = df["close"].shift(-horizon)

    y = exit_price / entry - 1.0

    # 3. 检查未来 horizon 根 K 线是否连续
    gaps = df["open_time"].diff().ne(step)

    future_gap = pd.Series(
        False,
        index=df.index,
    )

    for k in range(1, horizon + 1):
        future_gap |= (
            gaps.shift(-k)
            .fillna(True)
            .astype(bool)
        )

    y = y.mask(future_gap)

    # 4. 标签时间
    next_open = df["open_time"].shift(-1)

    label_end = df["close_time"].shift(-horizon)

    label_available_time = (
        df["available_time"].shift(-horizon)
    )

    # 5. 无效收益对应的标签时间也必须无效
    valid_label = y.notna()

    label_end = label_end.where(valid_label)

    label_available_time = (
        label_available_time.where(valid_label)
    )

    # 6. 检查参考入场时间是否早于决策时间
    # True 仅表示时间上可行，不保证真实成交
    entry_time_feasible = (
        decision_time <= next_open
    ).fillna(False)

    return pd.DataFrame({
        "decision_time": decision_time,
        "entry_time": next_open,
        "label_end_time": label_end,
        "label_available_time": label_available_time,
        "reference_return": y,
        "entry_time_feasible": entry_time_feasible,
    }, index=df.index)
