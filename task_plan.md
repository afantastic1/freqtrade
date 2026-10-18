# Alpha Lab 研究闭环施工计划

## 目标
严格按 `docs/work.md` 完成现状审计、P0/P1 实现、测试、历史补录与真实验收，不覆盖用户已有修改或历史研究产物。

## 阶段
- [x] 1. 审计代码、测试、SQLite schema、现有产物及工作树修改
- [x] 2. 形成逐项实施计划并映射 `docs/work.md` 全部要求
- [x] 3. TDD 实现默认基线、三分支运行与单次三模型评价
- [x] 4. TDD 实现不可变候选快照与执行身份
- [x] 5. TDD 实现 ResearchStore 迁移、索引、backfill 与查询 CLI
- [x] 6. TDD 实现研究键去重、force、并发/失败恢复
- [x] 7. 修正反馈语义、样本元数据与报告合同
- [x] 8. 运行完整测试、迁移、真实研究及 Gate 1–5 验收
- [x] 9. 汇总修改、迁移、命令、日志、查询结果与遗留问题

## 约束
- 不改变 IC/RankIC/标签/Walk-Forward/purge/模型/Bootstrap 数学定义。
- 不读取或触碰 2025/2026 行情数据。
- 不删除或覆盖旧报告、候选源码、SQLite 历史行。
- SQLite 仅新增/迁移 `candidates` 与 `evaluations`，`experiments` 仍由 `ExperimentRegistry` 管理。
- 所有指标必须来自真实报告；缺失值使用 NULL，不伪造为 0。
- 当前工作树已有用户修改，必须审阅并保留，不擅自回滚。

## 当前状态
全部阶段：complete

## 错误记录
| 错误 | 次数 | 处理 |
|---|---:|---|
| `using-superpowers/references/codex-tools.md` 初次路径拼错 | 1 | 已定位到 skill 子目录，下一步读取正确路径 |
