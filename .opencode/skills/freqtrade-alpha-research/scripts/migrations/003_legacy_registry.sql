-- 003: 旧 registry.jsonl 的 append-only 原始归档。
--
-- 背景：本项目在受控 SQLite registry 之前，用 user_data/research/registry.jsonl
-- 记录了 389 条异构实验（path A/B 预测型、策略层回测、执行/风险分析等 9 种 shape）。
-- 这些历史不应被丢弃，也不应被强行塞进 factors/experiments（很多记录没有因子公式，
-- 只有策略回测指标）。因此：
--   * 每条原始记录**逐字**归档到 legacy_registry.raw_json（append-only，按旧 id 幂等）；
--   * 对映射清晰者（含 factor_expr 的预测型/事件型）额外登记到结构化表，
--     并把结构化 id 作为**建议指针**写回 mapped_* 列（不设外键，避免顺序耦合）。
-- 不修改、不删除 registry.jsonl 原文件。

CREATE TABLE legacy_registry (
    id TEXT PRIMARY KEY,                 -- 旧 registry.jsonl 中的 id
    source TEXT NOT NULL,                -- 归档来源（相对路径）
    shape TEXT NOT NULL,                 -- 记录形态指纹（字段集合的短哈希标签）
    alpha_type TEXT,                     -- 旧 alpha_type（可能为空）
    legacy_ts TEXT,                      -- 旧 ts（可能为空）
    raw_json TEXT NOT NULL,              -- 原始记录逐字副本
    mapped_hypothesis_id TEXT,           -- 结构化映射（可空，建议指针）
    mapped_factor_id TEXT,
    mapped_experiment_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX legacy_registry_alpha_type_idx ON legacy_registry(alpha_type);
CREATE INDEX legacy_registry_source_idx ON legacy_registry(source);
