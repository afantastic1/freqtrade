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

# Fundamental Data Adapter 施工计划（2026-10-19）

# Data Engineering v0.1 收尾与 PIT 治理计划（2026-10-19）

## 范围
- [x] 1. 审计全部未跟踪文件、包配置、数据忽略规则、快照目录元数据与基础测试来源
- [ ] 2. 评审 PIT 状态合同、sidecar schema、strict PIT 对 CONSERVATIVE 的适用规则
- [ ] 3. 建立独立发行元数据、manifest/PIT schema、验证器和审计清单
- [ ] 4. 补齐来源证据缺项审核表与 PIT 正/负向测试
- [ ] 5. 从干净 Git 导出安装包并运行全部 Data Engineering 测试
- [ ] 6. 只提交源码、schema、文档与测试；确认数据和历史记录未变更

## 审计要点
- v0.1 核心源码和核心测试目前均未跟踪；`data_engineering_starter` 与安装目录相同文件完全重复，保留 starter，不把重复副本作为运行源码。
- 根 setuptools package finder 排除 `data_engineering*`；需要 standalone `data_engineering/pyproject.toml`，并让干净 checkout 能安装轻量 DE 包。
- `.gitignore` 已排除 `user_data/*`、SQLite、Python 和 pytest 缓存；本次保留所有工作区数据原样。
- Catalog 和 PIT 目前无正式 JSON Schema；现有 sidecar 为弱松散合同，需要 schema/version/state/evidence/revision/time semantics。
- 现有快照仍无历史发布时间与历史修订证明，必须维持 UNVERIFIED；用 2026 ingest/retrieval time 不能反推历史 available_at。

---

## 目标
在 `codex/fundamental-data-adapter` 单独实现只读 Catalog Adapter、严格 PIT as-of join 与 Alpha Lab feature bridge，不碰原始快照、Research SQLite、交易数据通路或 Alpha Lab 冻结评价模块。

## 阶段
- [x] 1. 审查当前分支、Data Engineering Catalog、PIT 覆盖和 MarketPanel/Dataset 接口
- [x] 2. 编写并经用户确认架构设计；设计提交 `b78b156fca`
- [x] 3. 编写实施计划并自审
- [x] 4. 用户复核实施计划
- [x] 5. 以 RED→GREEN 实现注册表、Adapter、严格 as-of join 和 feature bridge
- [x] 6. 文档、全量测试、冻结/交易路径差异检查与独立提交（`0982c1f8c4`）
- Ruling: 仅新增 adapter 说明文档，不修改已存在的未跟踪 `docs/data_engineering.md`，避免提交时新追踪整份 v0.1 用户基础文件。
- Ruling: PIT 认证使用内容寻址 sidecar，保持 Catalog manifest 和历史 snapshot 不变；同一 dataset_id 的多个版本不可在一次 read_many 中混用。

## 约束
- 数据范围固定为 `[2022-01-01, 2025-01-01)` UTC。
- 当前基本面全部 PIT-unverified；strict_pit 当前必须拒绝。
- available_time 只能来自实际数据，缺失保留空值。
- 14 个快照、历史数据库和 Freqtrade OHLCV/Funding/OI 路径只读且不变。
- 新代码与本任务测试独立提交；已有未跟踪 Data Engineering v0.1 文件不得被带入提交。

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

---

# Alpha Lab v1.1 多资产横截面扩展（2026-10-19）

## 目标
先完成 v1.0 遗留 P0/P1，再在保持单资产接口和历史证据兼容的前提下，增加多资产 point-in-time MarketPanel、Universe、独立横截面评价/模型/Runner、专用 append-only SQLite 结果与受限 Agent 接口。

## 阶段
- [x] 0. 修复 v1.0 遗留 P0/P1：证据冲突、事务备份、方向/MDE、身份与产物复用
- [x] 1. 写入 v1.1 协议草案、示例配置与实施/维护说明
- [x] 2. TDD 实现 MarketPanel、数据质量和 point-in-time Universe
- [x] 3. TDD 实现固定 CS 变换、相对标签、时间 purge/embargo
- [x] 4. TDD 实现 CrossSectionalEvaluator、统计功效与合成 Golden Tests
- [x] 5. TDD 实现 Ridge、独立 Runner/CLI、append-only SQLite schema 和身份复用
- [x] 6. 更新 Research Agent 网关、协议、权限解析检查及兼容性说明
- [ ] 7. 完成扩样统计校准、五资产 2022–2024 只读烟雾、OS 隔离测试、完整冻结和独立审查

## 当前状态
阶段 7：blocked on environment/data/security acceptance; v1.1 remains draft and is not frozen.

## 约束
- 不读取 2025+，不下载新数据，不开展因子挖掘，不触碰 Freqtrade 交易执行。
- v1.0 time_series 接口、历史实验、统计结果和数据库行保持兼容。
- 横截面统计按时间截面推断，资产数不冒充独立时间样本。
- 初版跨资产逻辑仅允许冻结变换；不开放任意跨资产 Python。
- 全部验收通过前不宣称 v1.1 冻结。

## 本轮约束
- 不丢弃或回滚当前 12 个修改/新增文件；重叠修改先辨明意图再兼容整合。
- 不读取 2025+ 保留数据，不开展新因子研究，不修改 Freqtrade 交易执行引擎。
- 数据库迁移前备份并在事务内执行；历史证据只追加或兼容读取，不原地改写。
- 生产代码遵循 TDD；统计定义变更提升 method_version 并记录 Golden 差异。
- 只有全部安全与统计验收满足后才建议 v1.0；否则明确列为未完成。
- v1.1 当前完成的是合成数据引擎施工，不等于有足够历史 Universe、校准推断、OS 沙箱或可发布基线。

## 本轮错误记录
| 错误 | 次数 | 处理 |
|---|---:|---|
| `python` 不存在 | 1 | 改用仓库 `.venv/bin/python` |
| pytest 自动参数 `--dist` 不受支持 | 1 | 使用 `-o addopts=''` 运行基线，并记录环境缺少 pytest-xdist |
| `opencode --version` 无法写只读 home 日志 | 1 | 后续把 XDG 数据/缓存目录指向 `/tmp` 后重试 |
