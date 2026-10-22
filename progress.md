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

## 2026-10-22 — Alpha Lab 第 1 轮横截面研究
- 只读审计后选定 5 标的 × 4h × spot/futures/funding 方向，预注册 F1–F4，并按预注册跑完 12 个 S1 任务（4 候选 × 3 个 2 月请求）。
- 修复 pinned Worker 字段白名单漂移（重建镜像 `sha256:f644ff10…`）与 `asof_join` 微秒/纳秒合并键冲突；新增两项回归测试。两次修复都早于任何候选 Evidence。
- 12 个任务全部 `EXPLORATORY`、`formal_inference_status=BLOCKED`、`source_sha256=a04e9ea2…`、`worker_image_digest=sha256:f644ff10…`；覆盖率 1.0。
- 判定：F1/F2/F3 方向与预注册相反，F4 方向对但 `|RankIC|<0.01`；四项均 `FALSIFIED`，按规则不进入 S2/V1。
- 反向异常：F2 funding 水平对 12h 相对收益为正向 RankIC `+0.0538`，但换手 0.05–0.13，疑似静态篮子效应，列入下一轮重新预注册（水平/变化分解 + 换手下限）。
- 产物见 `user_data/research/alpha_lab_rounds/REPORT_ROUND01.md` 与 `FACTOR_LIBRARY.md`。

## 2026-10-22 — Alpha Lab 第 2 轮横截面研究（水平 vs 偏离）
- 写死 `ROUND02_PREREG.md`（G1–G4、h=6、换手下限 0.30、S1/S2/V1 切分）后才开始运行；创建 `r2-s1a/b/c`、`r2-s2a/b/c`、`r2-v1a/b/c` 共 9 个 `AUTHORIZED` 请求。
- 串行跑完 12 个 S1 任务；全部 `EXPLORATORY`、`formal_inference_status=BLOCKED`、覆盖率 1.0、`source_sha256=4f93b422…`、`worker_image_digest=sha256:f644ff10…`。
- 判定：G1 `-0.0662`（换手 0.087，静态篮子确认）、G2 `-0.0043`（换手 0.381，净收益 0/3）、G3 `+0.0043`（G2 镜像）、G4 `-0.0285`（换手 0.538）。四项 `FALSIFIED`；S2/V1 按规则未运行，2026 未访问。
- 发现并记录第 1 轮符号约定缺陷（`factor=-S` 与 `expected_sign=-1` 矛盾），在 `REPORT_ROUND01.md` 追加勘误、在 `FACTOR_LIBRARY.md` 标注；判定不变、不追认 F2。
- 产物：`REPORT_ROUND02.md`、`round02_specs.py`、`round02_metrics.py/json`，并更新 `FACTOR_LIBRARY.md`。

## 2026-10-22 — Alpha Lab 第 3 轮（BTC 单资产时间序列）
- 写死 `ROUND03_PREREG.md`（T1–T4、w=42、h=6、变号门槛）后才创建 `r3-s1a/b/c` 三个 `time_series` 请求并串行运行 12 个任务。
- 结果：T1 `+0.0171`（子区间 -/+ /+）、T2 `+0.0307`（-/+ /+，其余门槛全过）、T3 `-0.0043`、T4 `+0.0367`（-/+ /+，月度 5/6）。全部 `FALSIFIED`；S2/V1/ETH 复制未运行，2026 未访问。
- 诊断：2024Q1 趋势市使全部反转/拥挤信号同时失效；funding 未超过 1-bar 反转基准；T2 与 T4 因子相关 0.049，T1/T2/T3 共享拥挤度成分（0.46–0.54）。
- 产物：`REPORT_ROUND03.md`、`round03_specs.py`、`round03_metrics.py/json`，`FACTOR_LIBRARY.md` 扩到 12 个候选并补充时间序列门槛。

## 2026-10-22 — Alpha Lab 第 4 轮（T4 时间外确认）
- 写死 `ROUND04_PREREG.md`（单假设、C1 2024 H2、门槛沿用第 3 轮 S2）后创建 `r4-c1a/b/c`，运行 3 个确认任务。
- `r4-c1c`（→2025-01-01）被拒：标签延伸越过 discovery 边界；改用 `r4-c1c2`（→2024-12-31），修正早于任何该窗口数据。
- 结果：C1 合计 RankIC `+0.0008`，子区间 `-0.0114 / -0.0065 / +0.0209`，Q5−Q1 `+0.0006` < 成本 `0.0014` → `RETIRED`。V1/ETH 复制未运行。
- 结论：第 3 轮 T4 结果是选择偏差，不可外推。产物：`REPORT_ROUND04.md`、`round04_metrics.py/json`，因子库同步。

## 2026-10-22 — Alpha Lab 第 5 轮（2022–2023 广谱筛选）
- 只读覆盖审计：2022–2024 的 5m/15m/1h/4h 全部可用；mark_price 仅 1h，open_interest 无。
- 写死 ROUND05_PREREG.md（8 候选 × 2 窗口）；提交时发现 16 MiB/chunk 上限，逐月切分并写修正案（含一次 2022-06 校准运行的披露）。
- 执行 19 个请求、58 个正式 Evidence 任务，source 统一 136a368f…，全部 BLOCKED。
- 结果：8 个候选全部 SCREEN_REJECTED；X2 仅月度一致性失败，X6 仅净成本失败，X4 月度 2/4 且 2022 价差为 0。
- 未进入 C1、未运行 2025、未访问 2026。产物：REPORT_ROUND05.md、round05_metrics.*，因子库扩到 20。

## 2026-10-22 — Alpha Lab 第 6 轮（信号层重判 + 衰减扫描 + 2024 确认）
- 先写 `ROUND06_PREREG.md`（S0′ 规则、finalist ≤2、2024 确认、2025 单次、2026 不访问），再用已有 Evidence 重判第 5 轮 8 个候选，无新数据：X4/X2/X6 通过，其余拒绝。
- 对 3 个通过者在同窗口跑 h=18/42（16 请求 / 48 任务，`source_sha256=136a368f…`，`worker_image_digest=sha256:f644ff10…`，覆盖率 1.0，全部 BLOCKED）；换手由 `factor_values` 在滞后 h 只读重建。
- 衰减结论：X6 随持有期衰减且换手不降 → `NOT_TRADABLE`；X4 仅 h=18 双窗净 >0 且 2022 毛收益 ≈0；X2 三个持有期均为正，h=6 最优。
- 2024 确认 24 任务：X2@h6 H1 +0.037 / H2 −0.017，X4@h18 H1 +0.107 / H2 +0.0137（<0.015）且 H2 毛收益为负 → 两者 `C1_FAILED` → `RETIRED`；2025 H1 未消耗、2026 未访问。
- 关键发现：5 资产五分位 = 1 资产/组，2024 出现 RankIC 与极差价差反号，排序信号无法变现；这是分辨率问题，下一轮需改问题形态而非阈值。
- 产物：`REPORT_ROUND06.md`、`round06_{rescore,collect,decay,c1}.py/json`，`FACTOR_LIBRARY.md` 同步（20 候选 / 3 过 S0′ / 0 过确认）。

## 2026-10-22 — Alpha Lab 第 7A 轮（组合构建敏感性）
- 先写 `ROUND07A_PREREG.md`（四种权重、七个指标、重建硬门、R1/R2/R3 判定），随后只读已有 Evidence 与服务标签数据集，未新增模拟、未触碰 2025/2026。
- 重建校验：36 个 extreme×h=6 单元与服务对齐，gross/turnover 误差 0、rank IC 误差 2.8e-17。
- 结果：X2 基线已最优（连续权重 −6%/−22%）；X4 的 2022 A 窗毛收益 0.000001→0.000107（zscore），净 5bps −0.000072→+0.000057；X6 只有 λ=0.15 平滑有效。
- 披露预注册 R1 的表述缺陷（未与基线比较），按规则意图判定 X2→R3、X4→R1、X6→另行预注册。
- 更正第 6 轮三处过度解读；记录标签入场 30 秒前视与 5 资产集中度限制。
- 产物：`REPORT_ROUND07A.md`、`round07a_portfolio.py`、`round07a_raw.json`、`round07a_results.json`，因子库与 `REPORT_ROUND06.md` 同步更正。

## 2026-10-22 — Alpha Lab 第 7A1.5 轮（执行可行性审计，旁路）
- 按用户指令插入在 7A2 之前：30 秒入场前视未解决，X4 的改善不能直接解释为可交易 alpha。先写 `ROUND07A15_PREREG.md`，再计算。
- 数据事实确认：可用最细粒度只有 5m（15m/1h/4h 也在），**无 1m**；5m 覆盖 5 标的全部 4h 边界，`reindex` 无 NaN。结论只能是 5 分钟粒度的可行代理。
- 旁路实现：只替换标签入场一端（出场仍冻结 4h 收盘 t+6，`entry_time + 20h` 处 4h close），成本模型与权重全部不动。入口 2×2（`F0`/`F1`/`F1s`/`F2`/`F3`），主判定用严格信号后口径 `F1s`/`F3`；`F1`/`F2` 因所在 5m bar 含 30s 前视行情只作对照。
- 校验门：G2 = 0.0、G3 = 4.76e-11、G4 = 4.84e-11 全部通过；**G1 未通过**（最大 6.0e-3），4 条违反全在 2024-10-28 20:00 UTC 一个边界，A/B 判定窗内为 0.0。按预注册三字段记录（`original_verdict` / `corrected_interpretation` / `amended_rule`），未降门槛、未改判定规则。
- 结果：`F0→F1s` 毛收益变动 ≤4% 且方向不一致 → 30 秒前视是二阶效应、非系统性乐观；X2 基线净收益变动 <6%，口径不变；X4 A 窗 `zscore` 净 5bps `+0.000061`、RankIC 0.1096、相对 `extreme` 的差距逐月 4/4 为正 → **映射改善不是前视造成的**。
- 但 `F3`（不利触界）下无任何方案为正；不利触界代价 30–116 bps/单位换手，是 5bps 手续费（10 bps）的 3–12 倍 → 约束是成交质量而非手续费。
- 新增派生量 `break_even_bps`：X2 A/B 62.7–82.7 bps、2024 22.1 bps；**X4 A `zscore` +11.9 bps**（`extreme` −9.9 bps）→ 改善真实但执行余量极薄；X4 B +48.4 bps。
- 用户提出的两条限制均已确认并写入报告：改善不等于统计显著（月级 t ≤ 2.91 但 n=4，p≈0.09，且标签重叠）；X4 的 A 窗改善属探索性发现（2022 为选择数据）。
- 缺口记录：X4 在 2024 的 h=6 未验证（7A 只跑了 h=18），本审计无该行。
- 下一步：7A2 目标函数改 `F1s` 净收益 + 「可承受滑点 ≥30 bps」硬约束；7B 双口径报告。2025 H1 未消耗、2026 未访问。
- 产物：`REPORT_ROUND07A15.md`、`round07a15_execution.py`、`round07a15_analysis.py`、`round07a15_{raw,results,gates,derived}.json`，`REPORT_ROUND07A.md`（§4 三字段化 + §5/§6 更新）、`FACTOR_LIBRARY.md`（新增第 7A1.5 轮一节）同步。

## 2026-10-22 — Round 7A2 完成
- 先写 `ROUND07A2_PREREG.md`（在任何计算之前），三处设计选择经确认：门槛保持 30/15 bps 不动、`mc ≥ 3/4` 保留并允许 `WEAK_MONTHLY`、h=18 保留在 Part A。
- `round07a2_execution.py`：40 个数据集、两套成本口径、Part A(60 配置)/B(容量)/C(4 新点)、G1a/G1b/G2/G3/G4/G5a/G5b/G6 全部通过。运行约 3 分钟。
- `round07a2_analysis.py`：派生量 + R-7A2 判定落实 → `round07a2_derived.json`。
- 判定：**X4 `EXECUTION_MARGINAL`**，16 配置无一 `SURVIVES`；`g3∧g5` 0/16、`g6` 0/16。
- 核心方法学产出：新增 `COST-P`（每 bar 换手）口径，证明「延长持有摊薄成本」是 `COST-F` 摊销口径的伪影，且该口径差异会把 h=18 `zscore` 从 `BE_P` +1.6 bps 误读为「通过 30 bps 严格线」。
- 三字段披露 3 项：G5 阈值缺陷（1e-12 不可达，改 G5a/G5b + json.dumps）、G5b 校验脚本自身 bug（F3 回放漏 +5min，已修）、报告实现偏差（`levels_hit` 全集）。
- 可复现：两次运行 6 个产物 md5 一致。
- 已更新 `REPORT_ROUND07A2.md`、`FACTOR_LIBRARY.md`（新增第 7A2 轮）、`findings.md`。
- 2025 H1 未消耗；2026 未访问。

## 2026-10-22 — Round D0 完成（因子冗余与增量预测信息审计）
- 先写 `ROUND_D0_PREREG.md` 并冻结阈值 → 再跑 D0.1 可用性审计 → 最后才计算 D0.2–D0.5。中途发现 §6.2 未规定判定用哪个 `h`，在**任何结果计算之前**以 §6.2.1 补正为「两个 h 须同时成立」。
- `round_d0_audit.py`（D0.1）→ `round_d0_compute.py`（D0.2–D0.5）→ `round_d0_nulls.py`（置换零分布）。
- 门禁通过：X2/X4 的 h=6 原始 RankIC 复现第 5 轮记录值，最大差 4.4e-5。
- 核心方法学产出：n=5 单期 `E|ρ| = 0.42`、零假设 `E[R²] = 0.25`（残差份额零期望 0.75），据此发现预注册的 `residual_var_share` 门槛无判别力，已三字段披露并提出修订。
- 实质结论：X4 与 X2 不重复（有符号相关 ≈0），但增量残差 IC 只在选择窗为正（A h18 +0.068、B h18 +0.055，月度 t 2.45/3.04），2024 衰减（+0.008/+0.037），且 2024 h=6 由单月驱动。
- 判定：整轮 `PAIR_COMPLEMENTARY_CANDIDATE`；B 窗因 +0.0199 差 0.0001 未过 0.02 保持 `INCONCLUSIVE`，拒绝事后调整阈值。
- 已更新 `REPORT_ROUND_D0.md`、`FACTOR_LIBRARY.md`（新增第 D0 轮）、`findings.md`。
- 多因子路线暂不启动；建议下一步先解决 n=5 判别力问题（跨时间合并面板）。2025 H1 未消耗、2026 未访问。

## 2026-10-22 — 动态 Universe 工程（准备宽截面战役）
- 决策：标的数直接上 30–50；保留 5m；授权修改 runner 的 `historical` 分支与服务 chunk 配置。
- 评估结论：Universe 抽象（Fixed/Historical/Dynamic）**已存在并有测试**，已接进 `dataset_builder`/`authorization_v2`/`pit_labels`。真正阻塞是「本地无新增标的数据」+「策略只授权 5 标的」+「载荷预算」。
- 新增 `alpha_lab/data/universe_readiness.py`（只读）：标的覆盖扫描 + 载荷预算计算 → `universe_readiness.json`。
- 改动 1：`alpha_lab/cross_sectional_runner.py::_universe` 增加 `historical` 分支（JSON listings → `pd.to_datetime(utc)` → `HistoricalUniverse`）。已验证合法/非法 spec。
- 改动 2：`alpha_lab_local/factory.py` 新增 `_max_steps_per_chunk()`，读 `ALPHA_LAB_MAX_STEPS_PER_CHUNK`（默认 512，范围 1..512），传给 `ResearchServiceV2`。已验证：下调 chunk 只改 `plan_hash`，不改 `identity_hash`，不失效已有 Evidence。
- 回归：`test_cross_sectional_runner` / `test_market_panel` / `test_cross_sectional_dataset` 18 项全通过（全量 8 个 collection error 是 venv 缺 sklearn/statsmodels，既有问题）。
- 关键量化发现：帧每步重发整段 trailing lookback，故成本由 **lookback × 标的数** 驱动。50 标的 5m 下：lb=48 → 单请求 2.2 天；lb=288（24h 记忆）→ **0.37 天、3 步/chunk，4 个月需约 325 个请求，不可用**。
- 由此修正方案：改为**按机制选粒度**——5m 只承载 ≤2–4 小时记忆；1–3 天用 1h；3–14 天用 4h。同样 24h 经济记忆在 4h 只需 6 根 bar，成本差 48 倍。
- 待 operator：下载 30–50 标的（`end=2025-01-01`，让 holdout 物理缺失）、新建 policy v3、设置 `ALPHA_LAB_MAX_STEPS_PER_CHUNK`、Catalog 注册。
- 未消耗 2025 H1、未访问 2026。未修改成本模型、S1 门禁或策略文件。
