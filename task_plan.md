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

---

# Alpha Lab v1.0 修复与冻结计划（2026-10-19）

## 目标
在保护当前工作树与历史证据的前提下，修复研究身份、append-only 完整性、统计推断、实验复用与候选执行隔离，随后建立可验证的 v1.0 冻结基线、受限 Research Agent 接口和维护流程。

## 阶段
- [ ] 1. 完成施工前审计：测试、schema、CLI、统计链路、OpenCode 实际权限语法与现有修改
- [ ] 2. 写入并自审 v1.0 设计说明与逐任务实施计划
- [ ] 3. Commit A：以 RED→GREEN 修复 P0/P1 数据、统计、身份和复用缺陷
- [ ] 4. Commit B：以 RED→GREEN 实现冻结检查、权限边界、候选执行隔离和版本化协议
- [ ] 5. 运行 Golden、Monte Carlo、SQLite、并发、安全绕过、端到端和完整验收
- [ ] 6. 独立整分支审查，修复 Critical/Important，形成交付报告与发布建议

## 当前状态
阶段 5：in_progress；阶段 4 的强隔离验收 blocked，禁止宣称 v1.0 完成

## 本轮约束
- 不丢弃或回滚当前 12 个修改/新增文件；重叠修改先辨明意图再兼容整合。
- 不读取 2025+ 保留数据，不开展新因子研究，不修改 Freqtrade 交易执行引擎。
- 数据库迁移前备份并在事务内执行；历史证据只追加或兼容读取，不原地改写。
- 生产代码遵循 TDD；统计定义变更提升 method_version 并记录 Golden 差异。
- 只有全部安全与统计验收满足后才建议 v1.0；否则明确列为未完成。

## 本轮错误记录
| 错误 | 次数 | 处理 |
|---|---:|---|
| `python` 不存在 | 1 | 改用仓库 `.venv/bin/python` |
| pytest 自动参数 `--dist` 不受支持 | 1 | 使用 `-o addopts=''` 运行基线，并记录环境缺少 pytest-xdist |
| `opencode --version` 无法写只读 home 日志 | 1 | 后续把 XDG 数据/缓存目录指向 `/tmp` 后重试 |
