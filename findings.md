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

## Alpha Lab 第 1 轮：5 标的横截面 basis/funding（2026-10-22）
- 首次使用 5 标的（BTC/ETH/SOL/BNB/XRP）× spot/futures × funding 的 4h 横截面研究；预注册 4 个候选，全部在 S1 被证伪，未运行 S2/V1。
- 执行链路发现两个真实缺陷：① pinned Worker 镜像的 `V2_ALLOWED_FIELDS` 与服务端 `input_schemas_v2.py` 漂移（9 缺失/3 旧名），导致所有含 spot/funding 辅助列的请求在 Worker 内 `ValueError` 并被报为 `EXECUTION_BLOCKED`；② funding 可用时间 `datetime64[us]` 与行情面板 `datetime64[ns]` 在 `asof_join` 的 `merge_asof` 冲突导致 `INVALID_REQUEST`。两者均在任何 Evidence 产生前修复。
- 修复：Worker 字段表对齐服务端并重建镜像（digest `f644ff10…`）；`asof_join` 统一合并键到 `datetime64[ns, UTC]`；新增 worker schema parity 与 mixed-resolution join 回归测试；启动脚本增加端口预检与真实 Broker pid 记录。
- 结果：F1 basis_z_rev `+0.0049`、F2 funding_rev `+0.0538`（与预期相反，换手仅 0.05–0.13）、F3 rev_1b `+0.0077`、F4 basis_resid_rev `-0.0097`（低于 0.01）。全部 `EXPLORATORY` / `formal_inference_status: BLOCKED`。
- 关键解释：5 资产单截面 + h=3 标签重叠使朴素 t 值不可用；F2 的低换手提示其 IC 可能来自静态横截面水平差而非时变信号，不能当作策略依据。
- 产物：`user_data/research/alpha_lab_rounds/{ROUND01_PREREG.md,REPORT_ROUND01.md,FACTOR_LIBRARY.md,round01_specs.py,round01_metrics.py,round01_metrics.json}`。

## Alpha Lab 第 2 轮：funding/basis 水平 vs 偏离（2026-10-22）
- 预注册 4 个候选（G1 funding 水平、G2 funding 偏离、G3 偏离取负、G4 basis 水平），固定 h=6（24h）、w=6、k=3，新增换手下限 0.30；S1 用 2024 H1 的三个 2 月请求，12 个任务全部 `EXPLORATORY` / `BLOCKED` / 覆盖率 1.0 / `source_sha256=4f93b422…`。
- 结果：G1 合计 RankIC `-0.0662`（子区间 -0.112/-0.110/+0.023），换手仅 0.087；G2 `-0.0043`（+0.049/-0.050/-0.009），换手 0.381，三个子区间毛利全部低于成本；G3 为 G2 镜像 `+0.0043`；G4 `-0.0285`，换手 0.538。四项均 `FALSIFIED`，S2/V1 未运行。
- 关键结论：funding“水平”是静态横截面暴露（换手 ≈1/11.5），不是因子；funding“时变偏离”换手达标但符号在相邻窗口翻转；basis 在水平与偏离两种形态下均无稳定预测力。5 标的截面 + 4h/24h 的 funding/basis 方向到此为止。
- 重要勘误：第 1 轮四个候选均为 `factor=-S` 但 `expected_sign=-1`，符号约定与反转机制自相矛盾。实测 F2 `+0.0538` 的正确读法是“高 funding → 低未来相对收益”，与机制文字一致，而非原报告所称的“动量”。判定按预注册保留，不追认；第 2 轮 G1 进一步证明该信号即使方向正确也会被换手门槛淘汰。
- 执行完整性：早期提交的源码在空行/docstring 上有差异，导致两个 `source_sha256`；已统一用与冻结 `SOURCE` 逐字节一致的文本重跑，报告只采纳 canonical 任务。

## Alpha Lab 第 3 轮：BTC 单资产时间序列（2026-10-22）
- 按第 2 轮建议改用 `time_series` 模式（单资产 BTCUSDT，4h，w=42，k=3，h=6），预注册 T1 funding 7d z、T2 funding 24h 偏离、T3 basis 7d z、T4 1-bar 反转基准，均预期 RankIC>0；新增「变号次数 ≥24/6 个月」替代截面换手门槛。
- 12 个 S1 任务全部 `EXPLORATORY`/`BLOCKED`/覆盖率 1.0/`source_sha256=9575827e…`/`worker_image_digest=f644ff10…`。
- 结果：T1 `+0.0171`、T2 `+0.0307`、T3 `-0.0043`、T4 `+0.0367`；子区间分别为 `-/+ / +`、`-/+ /+`、`+/+ /-`、`-/+ /+`。四项均 `FALSIFIED`，S2/V1 与 ETH 复制未运行。
- 关键解释：四个因子在 2024-01/02 同时为负（含基准），是趋势市 regime 而非四个独立证伪；T2/T4 其余 5 项门槛全部通过，T4 月度 5/6。funding 未跑赢反转基准；T2 与 T4 因子相关仅 0.049，funding 偏离不是反转伪装但信息更弱。
- T1/T2/T3 因子两两相关 0.46–0.54，共享拥挤度成分；T3 在 6 月 `-0.283` 崩坏。
- 产物：`ROUND03_PREREG.md`、`REPORT_ROUND03.md`、`round03_specs.py`、`round03_metrics.py/json`，并更新 `FACTOR_LIBRARY.md`（12 个候选）。

## Alpha Lab 第 4 轮：T4 反转假设的时间外确认（2026-10-22）
- 采用「选择后确认」设计：第 3 轮从 4 个候选中选出 T4（1-bar 反转），本轮用同源码同参数、单假设、单次检验，在未参与选择的 2024 H2 上确认。
- 结果：合计 RankIC `+0.0008`（门槛 0.015），子区间 `-0.0114 / -0.0065 / +0.0209`（要求全正），Q5−Q1 `+0.0006` < 成本 `0.0014`；月度 4/6，变号 598。C1 失败 → `RETIRED`，V1 与 ETH 复制未运行，2026 未访问。
- 结论：第 3 轮 T4 的 `+0.0367` 是 4 选 1 的选择偏差，换到时间外窗口后归零；「BTC 4h 反转」在 2024 全年不稳定。
- 执行修正：`2024-11-01 → 2025-01-01` 请求因 `label_horizon_bars=6` 的标签延伸越过 discovery 右边界被拒（INVALID_REQUEST，无 Evidence），改为 `→ 2024-12-31`（`r4-c1c2`）；修正早于任何该窗口数据。
- 产物：`ROUND04_PREREG.md`、`REPORT_ROUND04.md`、`round04_metrics.py/json`，`FACTOR_LIBRARY.md` 中 T4 标记 `RETIRED`。

## Alpha Lab 第 5 轮：2022–2023 广谱筛选（2026-10-22）
- 覆盖审计：5 标的 futures/spot 5m/15m/1h/4h 在 2022–2024 全量可用（spot 仅一个约 80 分钟缺口），funding 完整；mark_price 仅 1h、open_interest 无数据。
- 服务 16 MiB/chunk 上限：5 标的 + spot + lookback 48 在 1 个月窗口即超限 → 横截面改逐月请求；basis 类降到 lookback=22、w=12。已在预注册第 8 节记录，早于正式筛选。
- 8 个候选（24h 反转、7 天动量、低波动、funding 水平/偏离、2 天基差、成交量冲击、BTC 7 天动量）× 两窗口（2022-02..06、2023-09..12，各 4 个月）共 58 个 Evidence 任务，全部 SCREEN_REJECTED。
- 关键负结果：X2 动量两窗口同号（+0.040/+0.024）、成本最低，但 2023 月度仅 2/4（正号全来自 10 月）；X4 funding 水平 RankIC 最高（+0.110/+0.059）但 2022 分位价差为 0、2023 月度 2/4；X6 基差最稳定（3/4、3/4）但换手 0.76 → 成本 0.0015 远高于毛收益 0.0006/0.0002。
- 未进入 C1、未运行 2025、未访问 2026。产物：ROUND05_PREREG.md、REPORT_ROUND05.md、round05_specs.py、round05_source.py、round05_metrics.py/json，因子库扩到 20 个候选。

## Alpha Lab 第 6 轮：信号层重判 + 持有期衰减（2026-10-22）
- 用 S0′（双窗 RankIC ≥0.02 同号、月度中位数 >0、覆盖 ≥0.95、非退化换手；成本只报告）重判第 5 轮 8 个候选：X4 funding 水平、X2 动量、X6 基差通过，其余 5 个 `SIGNAL_REJECTED`。规则写于看到 2022–23 结果之后，故该年只作选择、不作确认。
- 衰减扫描（h=18/42 新 Evidence，48 任务；换手由 `factor_values` 在滞后 h 重建）：X6 的 RankIC 随持有期单调衰减（0.052/0.021 → 0.004/0.013）且换手恒在 0.77–0.83，成本不随持有摊薄 → `NOT_TRADABLE`，「真实但不可交易」是结构性的。X4 仅在 3 天持有双窗净 >0（+0.000014/bar，2022 毛/bar ≈0）；X2 三个持有期双窗净都 >0，24h 最优。
- 2024 确认（24 任务）：X2@h6 H1 `+0.037`、H2 `−0.017` → 失败；X4@h18 H1 `+0.107`、H2 `+0.0137`（门票 0.015）且 H2 毛收益 `−0.000513/bar` → 失败。两者 `C1_FAILED` → `RETIRED`；按预注册不消耗 2025 H1。
- 核心诊断：2024 出现 RankIC 与 1v1 价差反号（X2 H1 IC 正/毛负、H2 IC 负/毛正；X4 H2 IC 正/毛负）。5 资产五分位 = 每分位 1 资产，排序信息无法传导为极值价差。这是**分辨率**问题，不是阈值问题。
- 结构约束：政策只授权 5 标的，任何 `|RankIC| < 0.1` 的横截面排序信号都难以稳定变现；下一轮应改问题形态（funding 离散度做 regime 条件、组合权重评价、或时序化），而不是继续调门槛。
- 产物：`ROUND06_PREREG.md`、`REPORT_ROUND06.md`、`round06_{rescore,collect,decay,c1}.py` 与对应 JSON；`FACTOR_LIBRARY.md` 增第 6 轮小结，2025/2026 未访问。

## Alpha Lab 第 7A 轮：组合构建敏感性（2026-10-22）
- 对第 6 轮的三处过度解读做更正：`RETIRED` 只是「当前组合规则下退出候选」；「|RankIC|<0.1 无法变现」不是普遍规律；2024 预测失败记为对机制的反证，不作 regime 叙事。
- 方法：不改因子/数据、不新增模拟；用已有 Evidence 的 `factor_values` × 服务已构建的 `labels-*.parquet` 在本地重算四种权重（extreme / zscore / volscaled / turnpen）。重建校验与服务逐位一致：gross 误差 0、turnover 误差 0、rank IC 误差 2.8e-17。
- 结果：X2 的 `extreme` 已是最优（连续权重每个月都略差），映射不是问题；X4 在 2022 A 窗的「RankIC 0.11 但价差≈0」确认为**组合构建伪影**——连续权重把毛收益从 0.000001 抬到 0.000107/bar，净 5bps 从 −0.000072 转为 +0.000057，逐月看是削掉两个负月而非依赖单月；X6 的权重形状无效，只有 λ=0.15 的 EMA 平滑才转正。
- 判定规则缺陷披露：预注册 R1 只要求「某个非基线方案为正」，对 X2 这种基线已为正的情形会误触发；按规则意图（相对基线的改善）判 X2→R3、X4→R1、X6→需重新预注册。
- 关键认知：λ=0.15 的平滑等价于换了一个更慢的信号，不能算作「同一因子的另一种权重」。
- 限制：所有数据集 `entry_time_feasible=False`，标签按 bar 收盘价入场而信号在 close+30s 才可知，存在系统性 30 秒前视（冻结口径，本轮无法修正）；5 资产下即使连续权重单资产依赖仍 0.39–0.59。
- 下一步：X2→条件有效性（7B），X4→权重/成本优化（7A2），X6→慢速版本单独预注册；2025/2026 未访问。
- 产物：`ROUND07A_PREREG.md`、`REPORT_ROUND07A.md`、`round07a_portfolio.py`、`round07a_raw.json`、`round07a_results.json`。

## Alpha Lab 第 7A1.5 轮：执行可行性审计（旁路，2026-10-22）
- 数据事实：可用最细粒度**只有 5m**（另有 15m/1h/4h），**没有 1m**，故无法解析 30s；5m 网格覆盖全部 4h 边界（5m 的 `open` 与同边界 4h `open` 基本逐位相同，`reindex` 无 NaN）。因此结论只能是「5 分钟粒度上的可行代理」。
- 方法：不改冻结引擎、不新增模拟、不改权重。用 freqtrade 原始 5m/4h feather 在旁路只替换标签的**入场一端**，出场仍为冻结 4h 收盘（t+6）。入场设计 2×2：`F0` 冻结、`F1`（5m close `[00:00,00:05)`）、`F1s`（5m close `[00:05,00:10)`，严格信号后可行）、`F2`/`F3`（high/low 不利触界）。`F1`/`F2` 各含 30 秒前视行情，仅作对照；**主判定口径是 `F1s`/`F3`**。
- 校验门：G2（重建 `raw_return` vs 冻结存储）误差 **0.0**；G3（`F0` 毛收益 vs 7A）4.76e-11；G4（`F0` RankIC vs 7A）4.84e-11 → 全部通过，本地重建与 7A 等价。G1（4h/5m 同边界 `open` <1e-9）**未通过**，实测最大 6.0e-3，但 4 条违反全部集中在 **2024-10-28 20:00 UTC** 一个边界（52,544 个边界对中 4 条；A/B 判定窗内为 0.0）。已披露、未降门槛；下一轮把 G1 拆为 G1a（冻结一致性，须精确）与 G1b（跨粒度对齐，违规须列明且不得落在判定窗）。
- 核心结论一：**30 秒前视是二阶效应**。`F0→F1s` 毛收益变动 X2 A +2.4%、B +0.8%、2024 −3.8%（X4 zscore A +3.7%、B +1.8%），方向不一致，不是系统性乐观偏差；X2 基线净收益变动全部 < 6% → **基线口径不变**。
- 核心结论二：**X4 A 窗的映射改善不是入场前视造成的**。`F1s` 下 `zscore` 净 5bps `+0.000061`（F0 `+0.000057`），RankIC 0.1096（F0 0.1100），相对 `extreme` 的毛差距 F0 `+1.06e-4` / F1s `+1.10e-4`，**逐月 4/4 为正**。→ 改善来自排序信息，可进入 7A2。
- 核心结论三：**约束是成交质量，不是手续费**。不利触界代价 30–116 bps/单位换手，是 5bps 手续费（10 bps/单位换手）的 3–12 倍；`F3` 下**没有任何方案为正**（X4 A `zscore` −0.000270、`extreme` −0.000399；X2 A `extreme` −0.000096）。映射优势方向保留，但绝对收益不具备可成交保证。
- 新增派生量：`break_even_bps = gross·h/T_h − 2·fee`（每单位换手可承受的额外滑点）。X2 A/B 窗 62.7–82.7 bps、2024 仅 22.1 bps；**X4 A `zscore` 只剩 +11.9 bps**，`extreme` 为 **−9.9 bps** → 改善真实但几乎没有执行余量；X4 B `zscore` +48.4 bps 是相对可执行的一格。
- 统计限制（用户提出，确认有效）：月级 RankIC t ≤ 2.91 但 **n=4**（A/B 窗各 4 个月，双尾 p≈0.09），且 24h 持有期高度重叠，有效样本远小于名义 672 个时间戳 → 只能说方向一致，不能说统计显著。`formal_inference_status: BLOCKED`。
- 缺口：**X4 在 2024 的 h=6 未验证**（7A 对 X4 的 2024 只跑了 h=18，本审计无 X4/2024 行）；现有 2024 证据仍是第 6 轮 C1（h=18，`C1_FAILED`）。
- 下一步建议：7A2 的目标函数改为 `F1s` 口径净收益，并把「可承受滑点 ≥ 30 bps」设为硬约束（7A 的 `F0` 最优方案在 `F3` 下代价最高）；7B 同时报告 `F0` 与 `F1s`。2025 H1 未消耗、2026 未访问。
- 产物：`ROUND07A15_PREREG.md`、`REPORT_ROUND07A15.md`、`round07a15_execution.py`、`round07a15_analysis.py`、`round07a15_{raw,results,gates,derived}.json`。

## Alpha Lab 第 7A2 轮：组合与成本优化（旁路，2026-10-22）
- 目标：把目标函数换成 `F1s` 口径净收益，并把「可承受滑点 ≥ 30 bps」设为硬门槛，回答「已声明参数空间内是否存在同时满足净收益为正与滑点余量足够的配置」。判定问题，不是搜索问题。
- 参数空间：Part A 是 60 个已声明配置（2 候选 × 6 方案 × h∈{6,18} × 3 窗）的口径替换，0 新点；Part C 是唯一新增的 4 个点（`cadence ∈ {2,3}` × `{zscore, turnpen(0.30)}` × X4 h=6），选择规则先在预注册里声明且输入取自已完成的 7A1.5。
- **成本口径修正（本轮最重要产出）**：7A 的毛收益是重叠成交口径（每根 bar 按 `w_t` 建仓持 h 根），成本却用摊薄口径 `2·fee·T_h/h`，两者不自洽。实测 `T_1` 与 `T_h/h` 比值 **1.15–5.10**。新增 `COST-P = 2·fee·T_1`（每 bar），两套都算都进判定。
- **「延长持有摊薄成本」是 `COST-F` 伪影**：`turnpen15` h=6→h=18，`T_1` 完全不动（0.0332），`T_h` 0.165→0.288，于是 `BE_F` B 窗虚增 87%（112.1→209.7），而 `BE_P` 只升 12%（91.3→102.3）；A 窗两者都转负。
- **成本口径改变判定**：h=18 `zscore` 是唯一在两个判定窗都清过 30 bps 严格线的 X4 配置（`BE_F` 33.7/98.9），同配置 `COST-P` 下 A 窗只剩 **+1.6 bps**。只看冻结口径会误判为「通过严格滑点检验」。
- 判定：16 个 X4 配置 **`EXECUTION_MARGINAL`**，无一 `SURVIVES`。`g3`(`BE_F≥30`) 2/16（均 h=18）、`g5`(`BE_P≥15`) 4/16、**`g3∧g5` 0/16**、**`g6`(`mc≥3/4`) 0/16**。最佳 h=6 为 `turnpen` c=1/2/3（`WEAK_MARGIN`+`WEAK_MONTHLY`，`KNIFE_EDGE`），A 窗 `BE_F` 22.3–22.8、`BE_P` 15.2–17.3。
- **约束窗是 A 窗不是 B 窗**：每方案 A 窗 `BE_F` 都远低于 B 窗（`turnpen` 22.3 vs 46.4；`turnpen15` 20.4 vs 112.1），A 窗 `mc` 恒 0.50 vs B 窗 0.75–1.00。A 窗逐月净5（`turnpen` c=1）：`a02 −3.1e-5`、`a03 −1.5e-4`、`a04 +3.6e-4`、`a05 +1.8e-4` → 池化正收益由 2022 年 4–5 月承担。与 2024 全年为负方向一致：**三个窗只有一个强**。
- **`cadence` 与 EMA 平滑是替代品，不是互补品**：`turnpen`（已平滑）c=1→3 的 `T_1` 只降 8.8%，`T_h` 持平 → `BE_F` 不动；`zscore`（未平滑）`T_1` 降 20.2%，但起点 +3.7 bps，降完仅 +8.9。换手杠杆不能把 X4 推过门槛。
- 对照组**框架有效性检验**：同一门槛、同一管线、同一容量口径下，X2 的 `extreme`(A `BE_P` 15.02，`KNIFE_EDGE`)、`turnpen15`(69.5)、`turnpen50`(38.5)、`zscore`(15.75) 稳定 `SURVIVES` → X4 的失败不是门槛过严或管线有偏。
- 容量（Part B）：约束标的是 **BNB**，`N_max` @0.5% 单一 5m bar = **11,417 USDT**（@4h ~534,556，差 42–47 倍）。无订单簿深度数据，份额上限只是粗略代理，**不能解决冲击成本**。
- 数据事实修正：`xs_funding_level` 在 `r6c1h6m*` 系列下**根本没有证据**（只有 `r6c1h18m*`）→ X4 h=6 的 2024 缺口不是漏跑，而是该请求族从未采集。另：`factors.index` 对齐的是 `available_time`（close+30s），不是 `open_time`；`volscaled` 的 rolling vol 必须先在**完整** worker 区间上算再 reindex（先 reindex 会丢掉 48 行、改变窗口）。
- 门禁：G1a 2.18e-16、G1b 6.01e-3（4 条违规全在 2024-10-28 20:00，A/B 内 0 条）、G2 0.0、G3 4.95e-11、G4 4.84e-11、G5a 4.97e-11/4.28e-11、**G5b 逐位回放 42/42 相等**、G6 0.0 → `all_pass`。
- **G5 规则缺陷披露**：原阈值 1e-12 不可达——7A/7A1.5 产物用 `DataFrame.to_json`（pandas 默认 `double_precision=10`）写出，对 ~1e-4 值的量化硬界正好 **5e-11**，实测 4.97e-11 落在界内；G5b 逐位回放证明计算无差异。修订为 G5a（≤5e-11）+ G5b（逐位相等），并要求今后一律用 `json.dumps`（float64 精确往返）写产物。另：G5b 首跑 21/42 失配是**校验脚本自身 bug**（F3 回放漏了 +5 分钟，用了 F2 的窗口），修好后 42/42。
- 可复现：连续两次运行，6 个产物 md5 完全一致。
- 限制：`mc` 为唯一被用作门槛的统计量且 **n=4**；A/B 是选择窗、2024 是已用数据，**本轮没有独立确认样本**；`formal_inference_status: BLOCKED`。
- 下一步：7B 不再围绕 X4 的组合/成本调参（该声明空间已饱和），改问「A 窗 `a02`/`a03` 的亏损能否被事先知道的变量区隔」（条件有效性）。2025 H1 未消耗、2026 未访问。
- 产物：`ROUND07A2_PREREG.md`、`REPORT_ROUND07A2.md`、`round07a2_execution.py`、`round07a2_analysis.py`、`round07a2_{raw,results,gates,capacity,margins,derived}.json`。

## Alpha Lab 第 D0 轮：因子冗余与增量预测信息审计（只读，2026-10-22）
- 问题：Funding（X4）是否提供动量（X2）无法解释的独立预测信息。不建多因子策略、不产生 `S1_PASSED`、不拟合交易权重。7 个横截面候选 × 3 窗 × h∈{6,18}。
- 接受两处方法学纠正：(1)「截面去均值后 4 维 → 最多 4 个因子」只在单期成立，不能推及跨时堆叠模型；真正障碍是估计噪声与暴露集中，不设因子数硬上限。(2)「单因子必须先过执行门槛才能参与多因子」不是定理；严格门槛只是当前样本不足下的工程选择。
- **D0.1 关键数据事实**：`factor_values` 是标签期无关的——`xs_mom`/`xs_funding_level` 在 h=6 族与 h=18 族下**逐位相同（max diff 0.0）**，故跨族对齐合法。2024 的 `r6c1h6m*`（X2）与 `r6c1h18m*`（X4）**12 个月时间戳全同**，`pair_coverage=1.0000`；A/B 的 basis 族与 main 族零孤儿；7 候选在 A 窗均 5 资产、零缺失、零重复、零常数截面。**限制来自截面规模，不是数据缺口。** 2024 只采集了 X2/X4，其余 5 个候选记 `NOT_COMPARABLE`。
- **零假设基准（本轮最重要产出）**：精确枚举 n! 排列得，n=5 单期 `E|ρ| = 0.4200`、`sd(ρ)=0.5021`、`P(|ρ|≥0.5)=0.45`；单期 1 元回归零假设 `E[R²] = 1/(n−1) = 0.2500`，故**残差方差比例的零期望是 0.7500**（n=4 时为 0.5000/0.6667）。→ 相关性要与 0.42 比不是与 0 比；残差份额要与 0.75 比不是与 1 比。
- **D0.2**：43 个 (pair,window) 组合**没有一个达 `mean|ρ| ≥ 0.70`**。X2–X4 有符号 `mean(ρ)` = −0.025(A)/+0.120(B)/+0.038(2024)，`mean|ρ|` = 0.399/0.508/0.434 → 与独立零假设不可区分，**X4 不是 X2 的重复**。意外收获：**X1(24h 反转) 与 X2(7d 动量) 稳定负相关**（−0.253/−0.267，两窗一致），二者是同一根价格延续轴的两端；X2↔X3 符号翻转（+0.062→−0.290）是反例。其余配对 `|mean(ρ)|<0.12`。**n=5 下即使真实关系存在 `mean|ρ|` 也只到 0.5–0.6，故「0/43 冗余」不能读作「全部互相独立」。**
- **D0.3**：逐期 `β` 基本是噪声（A mean +0.0014、`sd` 0.5094、`sd/|mean|`=370；B +0.116/0.607；2024 +0.018/0.495；`β>0` 占比≈0.5）。残差方差比例 A 0.741/B 0.619/2024 0.755 对零期望 0.750 → **只有 B 窗低于零假设**（`R²` 0.381 vs 0.25），存在真实但有限的线性关联；A 与 2024 与「完全无线性关系」不可区分。
- 残差 RankIC（`SAME-T`）：A +0.0442(h6)/+0.0678(h18)、B +0.0199/+0.0547、2024 +0.0082/+0.0367；`ROLL-42` 同号。月度 t：A +1.36/+2.45、B +0.77/+3.04、2024 +0.49/+1.17。**h=18 明显强于 h=6**（X4 是慢变量，`k=6` 平滑）。
- **2024 是共同失效年，不只是增量衰减**：两个因子原始 IC 一起塌（X2 h=6 0.0397→0.0087，X4 0.1100→0.0229），与第 6 轮 `C1_FAILED`、7A2 `EXECUTION_MARGINAL` 方向一致——三条独立证据都指向该机制在 2024 失效。
- 稳定性：2024 h=6 残差 IC 由**单月**驱动（去掉 2024-03 翻号为 −0.0024）；2024 h=18 不翻号、8/12 为正但 t 仅 1.17；A h=18 与 B h=6 出现 leave-one-asset-out 符号翻转。
- **D0.5 判定**：A 窗 X4 `POTENTIALLY_COMPLEMENTARY`；B 窗 `INCONCLUSIVE`（**h=6 残差 IC +0.0199，差 0.0001 未过 0.02，禁止事后调阈值**）；2024 `INCONCLUSIVE`；整轮 `PAIR_COMPLEMENTARY_CANDIDATE`——该标签实质是「不冗余」，不是「已证明有独立增量」。X3 在 A 窗残差 IC 最高（+0.093/+0.182）却 B 窗符号翻转，作为反例保留。
- **规则缺陷（三字段披露）**：预注册的 `residual_var_share ≥ 0.30 / < 0.10` 门槛未做零假设校正，在 n=5 下前者恒真、后者几乎恒假，对判定无贡献。修订：改用 `R²_excess = (1−share) − 1/(n̄−1)`；相关性改用对 n=5 置换零分布的位置比较；推断层级固定为**月**；`n̄ ≤ 5` 时任何「不冗余」结论必须附「判别力不足」标注。
- 另两处披露：`h` 判定口径的歧义已在**任何结果计算之前**以 §6.2.1 补正为「两个 h 须同时成立」；脚本拆为 audit/compute 两个（纯实现细节）。
- 门禁：X2/X4 的 h=6 原始 RankIC 复现第 5 轮记录（0.0397/0.0240/0.1100/0.0594），最大差 **4.4e-5** → 通过。全部产物 md5 可复现。
- 结论：**仍然没有可用于构建多因子模型的因子。** X4 与 X2 不重复，但增量信息只在选择窗成立、2024 衰减，且 n=5 的判别力不足以把「冗余/不冗余」问成一个良定义的问题。
- 下一步建议（按优先级）：①把逐期截面统计改为跨时间合并面板（2–3 个月合并秩相关），使 n 从 5 提到 ~50–100，重做 D0.2/D0.3——这是唯一能真正回答冗余问题的路径，且不消耗时间外样本；②转 7B 条件有效性（A 窗 `a02`/`a03` 是否为 X4 主要失血点）；③停止 X4 方向。2025 H1 未消耗、2026 未访问。
- 产物：`ROUND_D0_PREREG.md`、`REPORT_ROUND_D0.md`、`round_d0_audit.py`、`round_d0_compute.py`、`round_d0_nulls.py`、`round_d0_{availability,correlations.csv,incremental,raw,verdicts,nulls}`。
