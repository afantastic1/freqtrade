
from pathlib import Path

import pandas as pd

import alpha_lab.cross_sectional_runner as runner
from alpha_lab.data.market_panel import MarketPanel


def test_label_code_change_creates_new_run(tmp_path, monkeypatch):
    # 1. 构造五资产合成行情
    symbols = tuple("ABCDE")
    times = pd.date_range(
        "2024-01-01",
        periods=32,
        freq="h",
        tz="UTC",
    )

    index = pd.MultiIndex.from_product(
        [times, symbols],
        names=["open_time", "symbol"],
    )

    prices = [
        100 + (i // 5) * (i % 5 + 1) + i % 5
        for i in range(len(index))
    ]

    data = pd.DataFrame(
        {
            "open": prices,
            "close": prices,
            "volume": 100.0,
            "available_time": [
                t + pd.Timedelta(hours=1, seconds=1)
                for t, _ in index
            ],
        },
        index=index,
    )

    panel = MarketPanel(data)

    # 2. 构造固定研究配置
    config = {
        "mode": "cross_sectional",
        "hypothesis": "code identity test",
        "symbols": list(symbols),
        "timeframe": "1h",
        "horizon": 2,
        "start": times[0].isoformat(),
        "end": (
            times[-1] + pd.Timedelta(hours=1, seconds=2)
        ).isoformat(),
        "train_end": times[16].isoformat(),
        "test_end": times[30].isoformat(),
        "universe": {
            "kind": "fixed",
            "id": "identity-test",
            "symbols": list(symbols),
        },
        "features": [
            {"name": "relative_momentum", "periods": 2}
        ],
        "n_boot": 19,
        "block_size": 2,
    }

    # 3. 第一次运行
    first = runner.run_cross_sectional(
        config, panel, root=tmp_path
    )

    original_sha = runner._sha

    label_path = (
        Path(runner.__file__).resolve().parent
        / "labels"
        / "cross_sectional.py"
    ).resolve()

    # 4. 只模拟 Label 文件指纹发生变化
    def changed_sha(path):
        if Path(path).resolve() == label_path:
            return "f" * 64
        return original_sha(path)

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_sha", changed_sha)

        second = runner.run_cross_sectional(
            config, panel, root=tmp_path
        )

    # 5. 验证源码身份变化触发新实验
    assert first["run_id"] != second["run_id"]
    assert first["config_hash"] != second["config_hash"]

    # 6. 恢复源码指纹后应复用第一次实验
    third = runner.run_cross_sectional(
        config, panel, root=tmp_path
    )

    assert third["run_id"] == first["run_id"]
