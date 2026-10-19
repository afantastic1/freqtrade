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

## Alpha Lab v1.1 前置审计
- 起始分支 `my_dev`，HEAD `e49aa244ae`，工作树干净；Alpha Lab 基线 `262 passed`。
- v1.0 独立审查遗留会阻塞 v1.1：evaluations/candidates 仍 UPSERT、旧库迁移无自动备份、复用未校验全部产物、Direction A 总体 RankIC 符号未参与终判、RankIC MDE 复用 logloss ESS、候选完整 manifest/family 未进身份。
- 本地 Freqtrade Feather 可用资产为 BTC、ETH，覆盖 5m/15m/1h/4h；后续只读 smoke 使用 2022–2024 过滤结果，不接触 2025+。
- v1.1 必须新建独立 panel/dataset/evaluator/runner/schema；旧 `evaluate_factor` 不承担 CS-IC。

## v1.1 施工中追加结论
- 已有实现分别位于 `data/market_panel.py`、`data/universe.py`、`data/cross_sectional_dataset.py`、`labels/cross_sectional.py`、`evaluation/cross_sectional.py`、`evaluation/cross_sectional_evaluator.py`、`models/cross_sectional.py` 与 `cross_sectional_runner.py`；SQLite 结果与决策进入独立附属表。
- 任意候选 Python 未获得 OS 级隔离。Research Agent 不再允许调用 legacy `candidate_cli run`，只允许固定变换的无参数 gateway；gateway 在运行期严格验证请求字段、固定 config/panel 根目录和 symlink。
- OpenCode v2.0.29 实测将 agent YAML `bash` 归一为 `shell`、`task` 归一为 `subagent`；当前 Alpha Researcher 规则解析出 shell 全拒绝加单条精确 gateway allow、subagent 全拒绝、edit 限定 candidates。
- 本轮测试使用合成数据完成，无读取行情 Feather，因此没有触及 2025+。本地可见 BTC 与 ETH 两个标的，不够构造 `min_assets=5` 的有效横截面；真实 CS 历史烟雾被数据与安全日期边界阻塞。
- CS Bootstrap Monte Carlo 当前改为多种 label persistence、prediction AR 相关及变化方差结构；其结果必须先完成后写入结论。协议把 CS 结果限定为 diagnostic 状态，不生成 SUPPORTED/REJECTED。
- `alpha_lab.freeze` 仍针对 v1.0 基线，因此现有 Maintainer 修复和新模块会被报告为 changed/untracked。冻结清单需在安全与数据验收之后生成新的版本基线，当前不得更新旧 manifest 来暗示完成。

## Fundamental Data Adapter 审计（2026-10-19）
- 当前分支 `codex/fundamental-data-adapter`，设计文档已由用户确认并提交 `b78b156fca`；工作区中的 `data_engineering/`、starter、文档和测试是已有未跟踪基础，必须避免误加入提交。
- 只读 coverage audit 实测 14 manifests、30 series、14/14 expected datasets、0 errors；所有序列 `unverified`。XRP `chain_tvl_usd` 从 2024-03-23 开始。SOL/BNB 的指标集合不同于 BTC/ETH/XRP。
- Catalog `verify(root,dataset_id,digest)` 校验 CSV 哈希、字节数、schema 和 manifest quality；`validate_fundamental` 接受额外列但只将 `published_at` 评为 `declared_not_audited`，不独立验证发布时间。
- 基本面 CSV 已包含 `asset_id, metric, event_time, value`，`published_at` 可选；Coin Metrics token alias 是 btc/eth/sol/bnb/xrp，DefiLlama alias 是 `chain:Bitcoin/Ethereum/Solana/BSC/XRPL`。
- `MarketPanel` 已有 UTC `(open_time,symbol)` MultiIndex 与每行 `available_time`；`CrossSectionalDataset` 接受与该索引对齐的 `features: dict[str, pd.Series]`。当前 Alpha Lab runner 不消费外部因子，桥接限于产生此既有 Dataset 输入，不修改 runner/evaluator。
- PIT 实施注意：不能只信任未经审计的 `published_at`。为保持 manifest 不变，strict PIT 通过内容寻址的追加式 sidecar 认证；sidecar 绑定 reviewer、audit reference、snapshot SHA 和 availability 列。当前快照没有 attestation，因此 strict_pit 必须失败关闭。

## Data Engineering v0.1 收尾与 PIT 治理审计（2026-10-19）
- 当前分支是 `codex/fundamental-data-adapter`，HEAD `0982c1f8c4`。v0.1 基础包、README/安装脚本、原始测试、指南原先均为未跟踪文件；adapter 已提交的模块依赖这些基础模块。
- `data_engineering_starter/data_engineering/{__init__,__main__,catalog,defillama}.py` 与已安装 `data_engineering/` 四文件逐字节相同；starter 的 `tests/test_data_engineering.py` 与工作区测试也相同。starter 是复制式 bootstrap；版本控制主包和独立安装元数据比继续追踪重复源更清晰。不得删除现有 starter 目录。
- 根 `pyproject.toml` 的 setuptools include 仅 `freqtrade*`；现有 Data Engineering 未被 wheel 安装。审计建议在 `data_engineering/pyproject.toml` 建立轻量独立发行包，只依赖 pandas，安装入口提供 `data-engineering` CLI；Alpha Lab bridge 仅在完整 Freqtrade checkout 中使用。
- `.gitignore` 的 `user_data/*` 已排除快照、raw、processed、reports、数据库和市场数据；通用规则也排除 `__pycache__`、pytest cache 和 sqlite。当前可见 fundamental 数据文件全在 `user_data/data_engineering/`，但本轮不打开内容、不改名、不提交。
- Data Engineering 目前没有 manifest JSON Schema 或 PIT sidecar schema；Catalog manifest 只在代码动态构造。需要新增版本化 Catalog Manifest Schema 与 `pit-evidence-v1` JSON Schema，并以验证函数校验实际 snapshot/schema/证据绑定。
- PIT 状态建议：VERIFIED 要求逐序列/逐行可追溯原始发布证据及修订身份；CONSERVATIVE 只可依有文档证明的可用时间上界作为最晚可用时刻；UNVERIFIED 表示材料不足；REJECTED 表示冲突、错配或时间越界。strict PIT 仅接收通过校验的 VERIFIED/CONSERVATIVE，以 conservative bound 参与 join；其余 fail-closed。
- 现有 14 个快照的历史发布时间、逐行修订 ID、原始历史版本证据均缺失或未经核验；不能因 2026 年采集/入库时间推断 2022–2024 当时可用，也不据此升级状态。审核清单应按来源、数据集和指标列出缺失证据，并全部保持 UNVERIFIED。
