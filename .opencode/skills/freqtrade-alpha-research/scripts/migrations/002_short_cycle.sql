-- 002: 短周期（5m-1h）增量字段与可交易性记录表。
--
-- 兼容原则：全部为「新增列 / 新增表」，不修改 001 的任何列、约束或既有数据。
-- v1 payload 仍可继续写入（新列可空）；旧记录读取不受影响。

-- K 线周期（与预测窗口解耦）。factors 记录该因子计算所用周期。
ALTER TABLE factors ADD COLUMN timeframe TEXT;

-- 实验层：K 线周期 / 预测窗口（时长口径，如 15m/30m/1h/2h/4h）/ 本金口径。
ALTER TABLE experiments ADD COLUMN timeframe TEXT;
ALTER TABLE experiments ADD COLUMN horizon_duration TEXT;
ALTER TABLE experiments ADD COLUMN capital_usdt REAL;

-- 假设层：记录提出假设时的目标 K 线周期（可选）。
ALTER TABLE hypotheses ADD COLUMN frequency TEXT;

-- 300U 级可交易性评价（append-only：只 INSERT，不 UPDATE/DELETE）。
CREATE TABLE tradability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    timeframe TEXT NOT NULL,
    horizon_duration TEXT,
    capital_usdt REAL NOT NULL CHECK (capital_usdt > 0),
    verdict TEXT NOT NULL CHECK (verdict IN ('PASS', 'FAIL', 'INSUFFICIENT')),
    checks_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX tradability_experiment_idx ON tradability(experiment_id);
CREATE INDEX experiments_timeframe_idx ON experiments(timeframe);
CREATE INDEX factors_timeframe_idx ON factors(timeframe);
