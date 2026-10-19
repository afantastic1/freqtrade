# Alpha Lab 施工进度

## 2026-10-19 — Fundamental Data Adapter
- 用户要求从 Data Engineering v0.1 Catalog 接入只读基本面数据，并明确确认架构设计；当前工作在 `codex/fundamental-data-adapter`。
- 使用 `superpowers:brainstorming` 完成并提交设计（`b78b156fca`），正在遵循 `writing-plans` 与 TDD 流程。
- 初步审计：Catalog 14/14 snapshots，30 series，0 errors，均 PIT-unverified；Adapter 不能伪造 available_time，严格 join 必须基于 audited availability timestamp。
- 当前接口：MarketPanel 的决策时间 `available_time`；CrossSectionalDataset 接受 MultiIndex 对齐的 feature Series，无需改评价模块。
- 完成：注册表、只读适配器、追加式 PIT sidecar 校验、`read_many`、as-of join 和 Alpha Lab Dataset 输入桥接已实现并提交 `0982c1f8c4`。
- 验收：Adapter/Registry/coverage 与 MarketPanel/Dataset 集成测试 56 passed；Alpha Lab 全套 297 passed；compileall、diff check 通过；只读审计 14/14 snapshots、30 series、0 errors。
- 限制：所有既有历史基本面仍 PIT-unverified；strict_pit 对现有快照全部拒绝。Data Engineering v0.1 核心是用户已有的未跟踪基础文件，不包含在 adapter commit 中，干净 checkout 需要先独立安装/版本化该前置包。

## 2026-10-18
- 读取 `docs/work.md`，确认范围覆盖 CLI、Runner、快照、研究索引、历史补录、去重、解释质量与五道验收 Gate。
- 读取并启用 `using-superpowers`、`brainstorming`、`writing-plans`、`planning-with-files`。
- 检查仓库状态，识别用户已有未提交修改并记录保护约束。
- 开始阶段 1 现状审计。
- 审阅 `config.py`、`candidate_cli.py`、`runner.py`、`experiment_registry.py`、候选加载、增量评价、反馈与现有 JSON 历史入口，记录首批差距。

## 测试日志
- 改造前全量：`107 passed in 14.88s`。
- 数据库只读审计完成：experiments=4，candidates/evaluations 尚不存在。
- Task 1 默认基线/CLI 合同：先见证 5 个测试失败，再实现 mom4+mom12、`--no-baseline`、`--baseline-config`、`--force` 与紧凑 JSON；聚焦测试 11 passed，全量 112 passed。
- Task 2 不可变快照：先见证 3 个测试失败，再实现 copy/hash/revalidate、`candidate_snapshot/<name>/`、原子 manifest，以及从快照路径执行；聚焦测试 8 passed，全量 115 passed。
- Task 3 ResearchStore：先见证 5 个测试失败，再实现 additive schema migration、FK、索引、候选身份冲突保护、signed IC 与 NULL 增量字段；聚焦测试 11 passed，全量 120 passed。
- Task 4 研究键/生命周期：先见证 4 个测试失败，再实现数据文件 stat 指纹、评价代码版本、原子 running claim、stale 恢复、可信产物复用、force 与 completed-but-unindexed 可补录语义；聚焦测试 8 passed，全量 125 passed。
- Task 5 评价模式/诚实解释：先见证 4 个测试失败，再加入 baseline source/feature 映射、single/incremental 状态、共同样本元数据、有符号指标断言与 Bootstrap/开发集/机制假说限制；聚焦测试 9 passed，全量 128 passed。
- Task 6 SQLite CLI/backfill：先见证 4 个测试失败，再实现 parent-authoritative dry-run/apply、missing/invalid/conflict 分类、两遍幂等与 list/show JSON/文本查询；聚焦测试 9 passed，全量 132 passed。
- Task 7 真实验收：先备份数据库；真实迁移/补录、历史查询、force 完整研究、默认复用、快照不变性与 SQLite FK 检查均已执行。验收中发现并用 RED→GREEN 修复 orphan 分类、CLI force 透传及候选 manifest 元数据升级。
- 独立最终审查：7 项 Important 全部在单轮修复中完成 RED→GREEN；最终全量 139 passed，最终真实 run 为 `bd4260f5df084ca583d9b7f51c8e845e`，随后默认命令复用同一 run。
- 最终 fresh 命令：`compileall` exit 0；全量 `139 passed in 11.15s`；`git diff --check` exit 0；真实数据库 experiments=8/candidates=3/evaluations=8，`foreign_key_check` 为空。

## 2026-10-19 — Alpha Lab v1.0 修复与冻结
- 启用并读取 using-superpowers、planning-with-files、brainstorming、writing-plans、systematic-debugging、TDD、executing-plans、verification-before-completion 与 finishing-a-development-branch 流程。
- 完成初始 Git 审计：`my_dev` / `e7833285`，识别并保护 12 个未提交 Alpha Lab 文件。
- 指定的 `python -m pytest` 因系统无 `python` 失败；改用 `.venv/bin/python` 后又发现项目 addopts 需要当前环境未安装的 pytest-xdist。下一步以 `-o addopts=''` 取得测试基线。
- 开始读取 ResearchStore、ExperimentRegistry、Runner、Bootstrap 与 VerdictPolicy，确认需进一步追踪身份、冲突、复用与统计校准。
- Commit A `770f27a19c`：统计身份冲突保护、A/B 独立落库、VerdictPolicy 阈值、方向冲突、中心化 bootstrap p 值、内容数据哈希及回归测试。
- Commit A 前全量：259 passed；冻结校验 RED→GREEN：2 passed。
- OpenCode v2.0.29 权限矩阵、协议 RC、CODEOWNERS/CI、冻结 manifest 与维护文档已建立。
- 候选隔离验收因当前宿主无法创建 network namespace 且现有 loader 为进程内导入而判定失败；记录为发布阻塞，不宣称 v1.0 完成。

## 2026-10-19 — Alpha Lab v1.1 多资产横截面扩展
- 前置 Git 审计：`my_dev` / `e49aa244ae`，工作树干净。
- 基线全量：`262 passed in 19.89s`，2 个既有 pytest 插件配置警告。
- 数据清单确认 BTC/ETH 有 5m、15m、1h、4h Feather；不实现下载器。
- 按用户前置条件先进入 v1.0 P0/P1 遗留修复，再开展 v1.1 架构施工。
- P0：统计复评身份含 `analysis_id/config_id`；同 run 不同 evidence 冲突拒绝；数据库任何 schema 补列/建表前通过 SQLite backup API 备份，迁移在 `BEGIN IMMEDIATE` 中完成；evaluation/candidate/decision/review 使用冲突保护，cs evidence 独立 append-only 表。
- P1：总体与月度方向冲突回 INCONCLUSIVE，方向判定提升 `prereg-a-v3`；RankIC MDE 不再借用 LogLoss ESS，提升 `paired-mbb-centered-null-v3` 并将缺少 RankIC 序列的 MDE 明确置空；实验复用验证 summary/manifest/全部产物 hash，candidate manifest 与 hypothesis 进入 time_series research identity。
- v1.1：增加 MarketPanel、固定/历史/动态 PIT Universe、固定相对因子变换、per-symbol gap 校验、raw/relative proxy labels、按 UTC 时间切分及 label-availability purge、训练数据 Ridge 和分截面评价。
- v1.1：增加专用 `cs_evaluations` / `cs_research_decisions`、内容指纹/代码/config/Universe 研究身份、受损产物不复用、并发幂等、开发集 campaign Holm/BH 只读复核。
- Research Agent 改用无参数 `research_agent_cli` 固定请求网关；OpenCode v2.0.29 的解析权限为 shell 默认拒绝、仅精确 gateway 命令允许、subagent 拒绝、edit 仅 candidates 允许。任意候选 Python 的进程级沙箱仍未通过，因此 Agent 的候选执行保持关闭。
- 新增协议草案、示例配置、CLI/维护指南；本机只有 BTC/ETH，且 Feather loader 会先读文件再筛时间，故未读取历史行情做 smoke，避免意外触及 2025+。真实五资产、2025-safe panel smoke 仍未完成。
- Append-only 完整性补强：ExperimentRegistry 对实验身份和状态转换安装 SQLite 触发器；ResearchStore 对 candidates/evaluations/research_decisions/statistical_reviews 安装禁止 UPDATE/DELETE 触发器；CrossSectionalStore 对横截面结果/决策安装同类约束。新增触发器前先通过 SQLite backup API 备份，并在事务内安装。
- 本轮最新完整回归：`./.venv/bin/python -m pytest -o addopts='' -q alpha_lab/tests/`，`297 passed, 2 warnings in 25.60s`；`compileall -q alpha_lab` 与 `git diff --check` 均通过。
- 冻结检查仍按预期失败：旧 v1.0 manifest 报出维护变更和新增核心文件。保留原 manifest，不以不完整验收覆盖旧基线。
- 仍未满足 v1.1 发布门槛：任意 legacy Python candidate 的 OS 级隔离；具备日期证明的五资产 2022–2024 历史 panel smoke；cross-sectional 推断校准及 SUPPORTED 决策合同；受保护分支设置核验。当前状态仍是 draft/unfrozen。
