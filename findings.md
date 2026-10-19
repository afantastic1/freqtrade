# Alpha Lab 施工审计发现

## 工作树
- 当前分支 `my_dev`，HEAD `e20c404d02`。
- 已有未提交修改：`runner.py`、`incremental.py`，新增 `feedback.py`、`research_history.py` 及对应测试、`docs/work.md`。
- 这些修改视为用户资产，后续只做兼容整合，不回滚。

## 当前实现要点
- `candidate_cli run` 仍硬编码 `factors=[]`，没有 `--no-baseline`、`--baseline-config`、`--force`。
- `runner._execute_experiment` 已实现候选+基线时复用 `evaluate_incremental(return_model_results=True)` 的三模型结果，避免第四次训练；纯内置/纯候选仍走一次 `evaluate_model`。
- Runner 当前直接从原始 `candidate_dir` 加载，直到运行末尾才再次检查原源码哈希；尚无 snapshot/manifest，也无法保证执行来自快照。
- `config_hash` 已纳入候选基本身份和增量 Bootstrap 参数，但没有数据指纹、评价代码版本等严格研究键内容，也没有复用/force/并发占用。
- Runner 只写 `summary.json` 和 CSV，然后将 `experiments` 标 completed；尚未统一写 ResearchStore。
- `research_history.py` 直接扫描 JSON，违背文档要求“SQLite 为主索引”；需要在保留用户工作基础上由 `research_store.py`/`research_cli.py` 取代或兼容。
- `ExperimentRegistry` 只拥有 `experiments` 表，连接尚未启用 `PRAGMA foreign_keys=ON`；可增加只读/占用支持，但不能复制建表逻辑。
- `evaluate_incremental` 已实现共同有限值掩码、三模型预测的时间/target/train_time/train_size 对齐、24/72/168 Bootstrap 以及返回原始模型结果。
- `feedback.py` 当前分类规则较克制：只有 robust_positive 且两个损失点估计均改善才 PROMISING，否则 INCONCLUSIVE；仍需补充局限性与 baseline/解释元数据。

## 待审计
- 核心模块接口与测试现状。
- `experiments.sqlite3` 表、列、计数及历史 run 关联。
- `runs/*` 与 incremental reports 的实际结构。
- 当前未提交实现与 `docs/work.md` 的差距。

## 数据库与历史产物
- 现库 `user_version=0`、连接默认 `foreign_keys=0`，只有 `experiments` 表，共 4 行且均 completed。
- `candidates`、`evaluations` 均不存在，首次真实迁移前必须先备份数据库。
- 四个有父记录的 run：`18fb...`、`7582...`（volume_divergence），`612d...`（intrabar 单因子），`f912...`（intrabar 增量）。
- 磁盘另有 `runs/0c13.../summary.json`，但 `experiments` 无父记录；backfill 必须报告并跳过。
- `612d...` 无增量评价，应该落库为 `evaluation_mode=single_factor`、`incremental_status=not_evaluated`。
- `f912...` 有完整三模型、相似度、24/72/168 Bootstrap 和 `INCONCLUSIVE`，可作为真实补录断言。

## 测试基线
- 改造前命令：`python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/ -q`
- 结果：`107 passed in 14.88s`。

## 实施裁定
- `--baseline-config` 被定义为完整的既有 `ExperimentConfig` JSON；显式文件中的 factors/alias 不被 CLI 默认值覆盖。
- CLI 默认基线由 `config.default_baseline_factors()` 集中定义为 momentum periods 4 和 12，仍序列化为原有 FactorSpec JSON schema。
- 快照目录严格保留候选名层级，使既有 `submit_candidate()` 的“目录名等于 manifest name”校验继续生效；Runner 将原始路径和快照相对路径同时写入 manifest。
- 新候选身份显式使用 `candidate_manifest_version=1`；任何旧表中已有的 candidate_id 原样保留，迁移只补列不重算历史主键。
- SQLite 旧表无法通过 `ALTER TABLE` 增补表级 FK，但每个连接仍启用 `foreign_keys=ON`；新建 evaluations 表具备到 experiments/candidates 的双 FK。
- 研究键的数据身份使用目标 Feather 文件的解析路径、size、mtime_ns 加请求的 symbol/timeframe/start/end；这样去重前不读取保护期数据，同时文件替换会改变研究键。实际运行仍由 Loader 读取限定的 `[start,end)`。
- 并发占用由 `BEGIN IMMEDIATE` 下的 running 检查实现；默认 6 小时后视为 stale 并保留为 failed，再允许新 run。
- 生命周期采用“summary/CSV → experiments completed → ResearchStore index”；索引失败向调用者报错并明确 backfill，completed 真值和文件保留。

## 真实迁移与验收
- 迁移前备份：`user_data/research/alpha_lab/experiments.sqlite3.backup-20261018-pre-research-store`，与当时源库 SHA-256 均为 `8d57bd9f749120e84c1c2afbd5a445553602b5f25a3874aadf75624213cef666`，experiments=5。
- 真实 backfill 首次 indexed=5；第二次 indexed=0/already_indexed=5。后续真实运行后再次 backfill 为 indexed=0/already_indexed=7。
- 孤儿目录 `0c13cec2d08495fb` 无 experiments 父记录，现报告 `missing_parent=1`，不建立伪索引。
- 历史 `612d...`：single_factor/not_evaluated，增量字段 NULL；`f912...`：ΔAUC=0.0008927780962197929、max_abs_spearman=0.45718532368535936、INCONCLUSIVE。
- 最终代码真实 force run `bd4260f5df084ca583d9b7f51c8e845e`：默认 mom4+mom12、三模型、24/72/168 Bootstrap、feedback、快照、manifest、全部预测/折 CSV 与 SQLite evaluation 均存在。
- 随后无 force 同命令复用 `bd4260...`，输出 `reused=true`；force、并发和损坏产物行为另有回归测试覆盖。
- 临时副本源文件改动后，既有快照 hash 仍为 `2bd9ca...b4c1`；数据库 `foreign_key_check` 为空。
- 最终研究结论仍为 `INCONCLUSIVE`；未修改评价数学定义，也未读取 2025/2026 区间。

## 独立审查修复
- 独立审查指出 7 项 Important：损坏产物复用、索引并发错绑、索引失败后复用绕过修复、旧表缺少 FK 等价约束、completed lookup/claim 竞态、坏指标终止 backfill、研究代码指纹覆盖不足。
- 每项均新增失败回归测试并修复：复用校验两份快照 hash 与摘要结构；复用前确保索引；索引身份读取前 `BEGIN IMMEDIATE`；旧表增加非破坏性约束触发器；claim 在事务内侦测新 completed；backfill 隔离坏指标；代码版本纳入 momentum/factor report/IC/feedback/builder。
- 修复后全量：`139 passed in 11.74s`。

## Alpha Lab v1.0 施工前审计（2026-10-19）
- 当前分支仍为 `my_dev`，HEAD 已前进到 `e7833285ef69264d8cf0a10168c011a92408c3c3`。
- 工作树有 12 个 Alpha Lab 修改/新增文件；其中 `regime_cli.py` 已尝试为 A-1/A-2 拆分 run，`prereg_record.py` 已尝试按 analysis_id 去重 JSONL，统计 selfcheck 与 RankIC 也有未提交性能/安慰剂修订。它们均视为用户资产。
- `ResearchStore` 已有 experiments/candidates/evaluations/research_decisions/statistical_reviews，但 statistical_reviews 当前 schema 未显式保存 analysis_id/config_id，必须继续审计 review_id 生成和冲突处理。
- 当前 bootstrap 使用未中心化 bootstrap 分布两侧比例构造 p 值；用户要求通过零效应与时间依赖 Monte Carlo 验证校准，失败时升级方法版本或降级用途。
- VerdictPolicy 已集中定义 ΔAUC=0.005、ΔLogLoss=0.0005、ΔBrier=0.00025；仍需追踪 regime 持久化是否硬编码 0.005。
- 仓库虚拟环境可用，但 pytest 默认 addopts 依赖未安装的 pytest-xdist；基线需覆盖 addopts 后执行。
- OpenCode 可执行文件位于 `/home/von/.nvm/versions/node/v20.20.2/bin/opencode`，首次版本查询因默认日志目录只读失败，需用临时 XDG 目录重试并检查真实配置解析。
- OpenCode 实测为 v2.0.29；官方权限文档确认 `permission`、通配模式、last-match-wins、`task` 与 `external_directory` 语法。受限 Agent 已按默认拒绝加精确允许配置。
- Bubblewrap 基础 user/PID/IPC/UTS 隔离可运行；当前托管环境创建 network namespace 失败。现有 candidate loader 仍在可信进程导入任意 Python，因此安全验收不通过，必须禁止无人值守候选执行并保持 RC 状态。
