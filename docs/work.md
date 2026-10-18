# Alpha Lab 研究落库与研究闭环修复施工计划

> 执行对象：OpenCode / 编码 Agent  
> 日期：2026-10-18  
> 工作目录：Freqtrade 仓库根目录  
> 定位：**在现有 Alpha Lab 上最小增量施工**，禁止重建研究平台、重复开发评价器或引入 Docker、PostgreSQL、向量数据库和新的多 Agent 框架。

## 0. 执行要求（先阅读）

1. **先审计现状再改代码**。逐文件检查 `alpha_lab/runner.py`、`candidate_cli.py`、`experiment_registry.py`、`research_store.py`、`research_cli.py`、`config.py`、`evaluation/incremental.py`、`evaluation/evidence.py`、`factors/candidate.py`、`factors/candidate_loader.py`、`validation/factor_validator.py`、相关测试，以及已有 `experiments.sqlite3` 表结构。此前曾提供 ZIP/草稿代码，不等于用户本地一定已经合并，**以工作树为准**。
2. 不更改现有 IC、RankIC、标签、Walk-Forward / purge、LogisticRegression、Bootstrap 数学定义来改善得分。不重新读取/触碰 2025 或 2026 数据。
3. 不删除、覆盖旧报告或候选源码；数据库迁移和历史补录必须可重复运行且不重复计数。
4. Agent 可以自主选择 `candidate_dir`，但一次提交应拥有规范的 `candidate.json`、`factor.py`；单次命令完成**完整研究**，默认有可比较的基线，支持显式覆盖或禁用基线。
5. 研究假设、源码指纹、实验配置、数据范围、基线组、评价结果和反馈必须可追溯。结构化数据入 SQLite，完整预测与详细报告保留文件；SQLite 不存长时间序列。
6. 不要求在本轮实现全自动无限循环；先把一轮研究的可靠提交、检索、复现做扎实。
7. 交付必须包含修改文件列表、迁移记录、真实运行命令、测试日志、数据库查询结果及遗留问题。严禁报告未执行的测试为通过。

## 1. 背景与真实问题

### 1.1 已实现能力（先在本地核实）

- `CandidateSpec` / `submit_candidate()` 验证 `candidate.json` 与 `factor.py`，生成 SHA-256。
- Agent 动态因子通过 `FactorValidator`、`build_features()`、`evaluate_factor()`；模型通过现有 `evaluate_model()`、`make_walk_forward()`。
- `evaluate_incremental()` 可比较 baseline、candidate、combined：相似度、ΔAUC、ΔLogLoss、ΔBrier、按月稳定性、Bootstrap block=24/72/168；`build_evidence_report()` 只组织客观实验结果与溯源信息。
- `ExperimentRegistry` 管理 SQLite `experiments` 生命周期，Runner 输出 `runs/<run_id>/summary.json`、预测与折指标。
- 可能已有 `ResearchStore`（`candidates`、`evaluations`）和 `research_cli backfill/list`，必须核实安装/迁移状态，**不可假设已有完整数据库**。

### 1.2 本轮自主研究证据：intrabar_pressure_reversal

- 路径：`user_data/research/alpha_lab/candidates/intrabar_pressure_reversal/`。
- 源码 SHA-256：`2bd9cae01cd22532966be65433b7d23428a8d2b4698eaafa7dc1165fd489b4c1`；参数 `pressure_window=12`。
- 信号：`-rolling_mean(CLV,12)`，其中 `CLV=(2*close-high-low)/(high-low)`；**这是收盘区间位置指标，不是已证实的主动成交净压力/做市商库存**。
- 独立 Candidate Run `612d083a617c401f91b8c6a28611a35a`：缺基线，增量评价为空。
- 增量 Run `f91255f125a14d70a19c706612bdb13e`：baseline=`momentum_4 + momentum_12`；RankIC≈+0.0486，单因子 AUC≈0.5356；与两基线 Spearman≈−0.317/−0.457；baseline AUC≈0.54628，combined AUC≈0.54717，ΔAUC≈+0.00089，ΔLogLoss≈+0.00095，ΔBrier≈+0.00047；16/24 月改善，Bootstrap 多个区间跨零。结论 `INCONCLUSIVE`，不能认定统计显著或已成立的交易 Alpha。
- 以前 `volume_divergence` 与 `momentum_4` Spearman≈0.954，存在高度冗余。
- 研究/开发集截至 2024-12-31；本轮未访问 2025，但**不能仅凭本轮未访问断言 2025 在整个研究历史中保持未触碰**。

### 1.3 必修实现问题（P0/P1）

| 优先级 | 问题 | 后果 | 目标修复 |
|---|---|---|---|
| P0 | `candidate_cli run` 默认 `factors=[]` | Agent 需分别跑独立与增量两次；缺增量证据 | 默认合并基线与候选、单次运行；仅指定 `--no-baseline` 才纯候选 |
| P0 | 仅存 `source_hash`、路径，未冻结源码 | Agent 修改 `factor.py` 后不能复现旧实验 | 每次 Run 创建不可变源码与 manifest 快照、保存快照相对路径/哈希 |
| P0 | 研究结果写 JSON 与 SQLite 不一致 / 未索引 | Agent 查询历史缺失、旧实验不完整 | 统一 `ResearchStore` 入库、失败可恢复、幂等补录、完整度状态 |
| P0 | 同一候选/同配置重复执行 | 浪费训练、污染候选排行榜、增加选择偏差 | 使用可复现研究键查重并允许 `--force` 显式复跑 |
| P1 | 增量 baseline 手工指定 | 不一致、难比较 | 本阶段默认 `momentum(4,12)`，提供明确覆盖参数；自动基线选择以后再做 |
| P1 | `rank_ic` 符号不固定、实验层混入研究结论 | 错把负相关当无效，客观结果被策略判断污染 | 保存 IC 有符号值与稳定性；Research Decision 由 Agent 单独追加 |
| P1 | 只记录成功实验，失败/被拒绝尝试查询不到 | Agent 重复犯错 | 研究查询区分 completed / failed / unindexed；失败归档原因并可检索 |
| P1 | 样本检验假设/数据可用性信息缺失 | 误判显著性或未来泄漏 | 保存训练/测试区间、数据指纹、block 参数、共同样本与模型版本 |

## 2. 目标使用体验

### 2.1 单命令完整研究

```bash
python -m alpha_lab.candidate_cli run \
  user_data/research/alpha_lab/candidates/intrabar_pressure_reversal
```

默认使用配置中的 BTC 1h、训练区间及 baseline=`momentum(4), momentum(12)`，运行 IC、RankIC、三模型增量评价和 Bootstrap，并将 Evidence Report 保存到同一个 Run。baseline 要在报告中明确标注来源、参数和对应的 `feature_column` 名称。

可选命令（具体选项以项目惯例实现，保持兼容）：

```bash
python -m alpha_lab.candidate_cli run /agent/path --baseline-config experiments/configs/btc_1h_baseline.json
python -m alpha_lab.candidate_cli run /agent/path --no-baseline
python -m alpha_lab.candidate_cli run /agent/path --force
python -m alpha_lab.research_cli list --limit 20
python -m alpha_lab.research_cli show --run-id f91255f125a14d70a19c706612bdb13e
python -m alpha_lab.research_cli backfill --dry-run
python -m alpha_lab.research_cli backfill
```

- Agent 自主决定 `candidate_dir`；只将其当输入路径，不固定特定文件夹。
- 未提供 baseline 时**不应静默把增量标成 0 或假装执行过**。显式纯候选实验字段使用 `evaluation_mode=single_factor`、`incremental_status=not_evaluated`。
- 默认已存在相同研究键的成功记录时返回既有 `run_id` 与摘要，标记 `reused=true`；`--force` 则生成新 `run_id`，但研究键一致。

### 2.2 单次输出结构（示意，不伪造指标）

```text
user_data/research/alpha_lab/runs/<run_id>/
  manifest.json                 # 完整输入、版本/数据/基线与快照哈希
  candidate_snapshot/
    candidate.json
    factor.py
  summary.json                  # 单因子+增量+稳定性+Evidence Report
  predictions.csv               # 默认最终模型预测
  fold_metrics.csv
  predictions_baseline.csv      # 增量模式
  predictions_candidate.csv
  predictions_combined.csv
  fold_metrics_baseline.csv
  fold_metrics_candidate.csv
  fold_metrics_combined.csv
  factors/<feature_name>/{monthly,yearly,deciles}.csv
```

保留现有产物命名和路径约定；若已有差异，应在不破坏旧消费者的情况下向后兼容。

## 3. 数据库方案：复用现有 experiments.sqlite3

数据库位置沿用 `alpha_lab/runner.py` 的 `DB_PATH`：`user_data/research/alpha_lab/experiments.sqlite3`。**只增加 `candidates` 和 `evaluations` 两张研究索引表**；原 `experiments` 由 `ExperimentRegistry` 独占管理。若两张表已存在则做兼容检查/增量迁移，不要 DROP 重建。

### 3.1 candidates：不可变研究候选版本

- `candidate_id TEXT PRIMARY KEY`：`SHA256(canonical_json({name, source_hash, parameters, candidate_manifest_version}))`。若旧实现用 `{name,source_hash,parameters}` 指纹，**保持既有公式或者显式版本化迁移，禁止偷偷改变导致重复**。
- `name TEXT NOT NULL`, `source_hash TEXT NOT NULL`, `parameters_json TEXT NOT NULL`, `hypothesis TEXT`, `source_path TEXT`（原始提交地址，仅参考），`snapshot_path TEXT`（可复现快照位置），`manifest_json TEXT`（至少包含 required_columns、prediction_horizon），`created_at`。
- 唯一性应按 `candidate_id`；同名不同源码/参数不得覆盖；同一版本多次运行复用候选元数据。
- 不在表中保存源码正文，只保存快照路径、SHA-256 和元数据。

### 3.2 evaluations：每个 run 的研究摘要

- `run_id TEXT PRIMARY KEY`，FK 指向 `experiments.run_id`；`candidate_id` FK 指向 `candidates`。
- 保存：`rank_ic`, `ic`, `mean_monthly_rank_ic`, `valid_months`, `auc`（**明确这是 candidate 单因子 AUC 还是 combined AUC**：分别 `candidate_auc`, `baseline_auc`, `combined_auc`，兼容旧 `auc` 列）, `max_abs_spearman`, `delta_auc`, `delta_logloss`, `delta_brier`, `positive_months`, `total_months`, `robust_positive`, `evidence_json`, `report_path`, `evaluation_mode`, `incremental_status`, `indexed_at`。旧 `classification`、`tags_json`、`feedback_json` 列只用于读取历史记录，新实验保持 `NULL`。
- `research_decisions` 与 `evaluations` 分表保存；每条决策必须引用 completed/indexed Run、candidate_id 与完全一致的 `candidate_source_hash`，并以 INSERT-only 方式追加支持证据、反对证据、结论、下一轮假设及动作。
- 复杂的各月表、Bootstrap 区间、模型预测保留在 `summary.json` / CSV；SQLite 可按查询需求索引关键字段，不得冗余复制整个巨型 JSON。
- 不适用/缺失指标使用 SQL `NULL`，**不能写 0**。
- 对失败实验：保留 `experiments.status=failed`、错误原因；无成功评价则不伪造 `evaluations` 行。CLI 提供可检索失败历史或明确支持仅查成功。

### 3.3 SQLite 规范

- `PRAGMA foreign_keys=ON`、参数化 SQL、事务、必要索引：candidate name、run finished_at、evaluation candidate_id/classification。
- 迁移版本管理最小化：在 ResearchStore 初始化时检查列并做受控 `ALTER TABLE ADD COLUMN`，或用 `PRAGMA user_version` 统一记录；优先沿用现有工程迁移风格，禁止每次运行重建表。
- `index_summary(run_id, summary_path)`：从完成并落盘的标准报告解析和入库；`INSERT...ON CONFLICT` 幂等。**必须连同 run_id 对应的候选 ID 保持一致**：发现已有同 run_id 绑定不同 candidate_id 时抛异常，不可静默更新指标导致错绑。
- `ResearchStore` 与 `ExperimentRegistry` 使用同一个 DB 文件，但不要复制创建 `experiments` 表的逻辑。
- 成功运行不能出现“SQLite 标记 completed，但研究索引缺失且无修复能力”。建议：Runner 在 summary 原子写入后执行 `index_summary`，再设置 `experiments` completed；若索引要求 parent 必须 completed，则需要事务改造或采用**可恢复的提交顺序**：先 complete，再 index，索引失败明确报错并通过 `backfill` 修复，且不得静默成功。选择一条与现有实现兼容的路径并写集成测试。

## 4. 按文件施工清单

### P0-A：入口统一与配置身份

**文件：`alpha_lab/candidate_cli.py`, `alpha_lab/config.py`, `alpha_lab/runner.py`**

1. 检查当前 CLI 生成配置的 `factors=[]`。默认启用两个内置 baseline：`FactorSpec(momentum, periods=4)` 与 `FactorSpec(momentum, periods=12)`，使用真实 JSON schema；提供 `--no-baseline`、`--baseline-config`，不能强制覆盖用户显式基线。
2. CLI 不另开一套评价流程，仅调用 `run_experiment()`；只调用一次（不再要求协议 Run A + 增量 Run B）。
3. Runner 按三种分支运行：only builtin = 1 模型；only candidate = 1 模型；baseline+candidate = 3 模型（A/B/C）。在增量分支**不再先运行第四个全量模型**。复用 `evaluate_incremental(return_model_results=True)` 和原结果保存器。
4. `config_hash`/研究键包含完整候选身份、基线列表及参数、horizon、训练/测试区间、Bootstrap 设置、模型配置和数据版本/指纹。**不能仅凭源码 SHA 去重不同测试条件**。
5. 适配 `ExperimentConfig` 原有序列化字段，CLI 与 Runner 测试覆盖所有三分支、参数覆盖与向后兼容。

### P0-B：不可变源码快照

**文件：`alpha_lab/factors/candidate.py`（可扩展辅助函数）, `alpha_lab/runner.py`**

1. 在候选验证成功后，按 `run_id` 创建 `runs/<run_id>/candidate_snapshot/`，复制 `factor.py` 和 `candidate.json`；在拷贝之前与之后计算 SHA-256，确保所执行源码与入库源码一致。
2. **执行必须从快照加载，而不是继续从 Agent 工作目录导入**。如果 `load_candidate()` 目前依赖目录名与 `candidate.json.name` 一致，快照目录结构设计为 `candidate_snapshot/<candidate.name>/`，不要为了快照降低校验强度。
3. 把快照文件路径/哈希写 `manifest.json` 与 `candidates`；运行结束后原始工作目录允许变化，不影响复现。
4. 不要把整个仓库或行情数据复制进快照。行情由原 Loader 读取，记录数据范围、文件签名/数据版本、数据行数、标签定义版本；避免 26k/更多行重复落盘。
5. **重要**：快照只能防止“源码被覆盖导致历史不可复现”；不代表进程权限沙箱。用户明确不要求 Docker / 安全隔离。

### P0-C：研究落库与历史补录

**文件：`alpha_lab/research_store.py`, `alpha_lab/research_cli.py`, `alpha_lab/experiment_registry.py`（必要时仅加只读查询）, `alpha_lab/runner.py`**

1. 若 `research_store.py` 已存在，保留可用的 `candidates` / `evaluations` 结构，通过幂等迁移补齐字段；**不要另建 `research_history.py` 扫 JSON 作为主索引**。
2. 将数据库记录调用放在 Runner 统一提交路径，确保 `summary.json` 已原子写入、模型输出已保存；更新索引失败不得伪称完整成功。
3. 历史 `backfill`: 只读取 `runs/*/summary.json` 和已存在的 experiments 完成记录。支持 `--dry-run` 输出待补录/缺关联/重复/错误摘要；真实补录幂等。
4. 对 `612d...` 等没有增量评价的旧 Run 标记 `incremental_status=not_evaluated`；对 `f912...` 报告存在对应字段时落库真实指标；不要把无 baseline 的 Run 与有 baseline 的 Run 合并成一条虚构实验。
5. 独立 `incremental_reports/*.json` **不能凭 candidate.name 自动贴到某个 run**；仅在含可靠 run_id、候选哈希、配置一致时允许关联，否则归入待人工核对清单。
6. `research_cli list/show` 默认紧凑摘要，不输出全部月度结果，支持 name、状态、limit 过滤及 `--json` 机器可读；需要详情时给出 report_path。

### P1-A：避免重复实验与研究污染

**文件：`alpha_lab/runner.py`, `research_store.py`/`experiment_registry.py`, `candidate_cli.py`**

1. 生成严格的 `research_key`，至少包含 candidate fingerprint、基线 fingerprint、数据指纹、标签/预测 horizon、训练切分、模型及 Bootstrap 参数、评价代码版本。
2. 已有相同键且状态为 completed + 报告/快照文件存在并通过身份核验时，默认返回历史 run，不重新训练；`--force` 允许显式重跑。
3. 已存在 failed 或文件损坏不能直接复用；重跑生成新 Run，失败历史保留。
4. 并发相同键运行要有确定行为：可用 SQLite 唯一占用/事务或受控提示，避免两个 Agent 同时重复启动；**不要让临时 running 记录永久阻塞**，要有恢复策略。
5. 同一个因子 `pressure_window∈{4,6,12,24,48}` 虽未按业绩挑选，但仍进行过基于相关性的选择；数据库记录尝试/设计筛选的来源，避免宣称真正完全预注册。

### P1-B：Evidence Report 与 Research Decision 职责分离

**文件：`alpha_lab/evaluation/evidence.py`, `research_store.py`, `research_cli.py` 及报告格式**

1. Alpha Lab 只保存 Evidence Report：因子质量、IC/RankIC、模型表现、相似度、增量表现、Bootstrap、数据与样本外使用信息。
2. Evidence Report 不包含候选名称分支、固定经济解释、研究分类、标签或下一步动作。
3. 报告显示与哪个 baseline 比较；负 IC/RankIC 保留符号，是否反向使用属于 Research Agent 的后续判断。
4. 记录 `sample_eligible` 共同掩码及训练/预测时间戳一致性检验；确保 `label_available_time` purge 不回退。
5. Research Agent 综合 Evidence Report 与历史记录，自主追加 Research Decision：支持证据、反对证据、研究结论、下一轮假设和研究动作。
6. Research Decision 与实验评价分开存储，强制引用 Run ID 和候选源码哈希，不得覆盖 `summary.json`、Evidence Report 或旧决策。
7. 历史 `research_feedback` 仅兼容读取，不自动升级为 Agent 决策。

## 5. 数据一致性与复现协议

- **单位运行身份**：`run_id` 是执行 ID；`candidate_id` 是源码+参数版本 ID；`research_key/config_hash` 是实验条件 ID。三者不能混用。
- **运行真值**：Runner 归档的 `manifest.json`、`summary.json` 与预测 CSV；SQLite 是可查询索引，允许通过 Backfill 恢复，不能存伪造摘要。
- 原始 `candidate_dir` 可以是任意绝对/相对路径，但报表应记录标准化路径；快照是实际执行源码。
- 旧 SQLite 数据库先备份，迁移不修改历史 completed/failed 状态，不删任何旧行。
- 数据使用的是 Binance USDM BTC/USDT:USDT 1h、2022–2024 开发区间；多数据集支持可以预留标识，不要求本轮扩展交易标的。

## 6. 最小测试清单（必须实现）

| 测试 | 断言 |
|---|---|
| `test_default_candidate_baselines` | `candidate_cli run` 默认确实包含 mom4+mom12，一次 Run 产生 incremental Evidence Report |
| `test_explicit_no_baseline` | 纯候选可运行，增量字段为 `null` + `not_evaluated`，而非假指标 |
| `test_three_model_no_duplicate_train` | 含 baseline 时只训练三组模型，无第四次重复全量训练 |
| `test_candidate_snapshot_immutable` | 修改原始 factor.py 后旧 snapshot 源码/哈希不变，复现仍引用快照 |
| `test_snapshot_hash_matches_loaded_code` | 执行源码与记录源码 hash 一致，变更/不一致报错 |
| `test_research_store_idempotency` | 同一 Run 重复索引只保留一条评价；错绑不同 candidate_id 必须失败 |
| `test_database_upgrade` | 旧版 SQLite 结构不丢历史；新列补齐、FK 生效 |
| `test_backfill_old_single_candidate` | Run A 入库但增量为空且状态明确 |
| `test_backfill_incremental_candidate` | Run B 的 ΔAUC、Spearman、反馈按真实 summary 入库 |
| `test_backfill_missing_parent` | 没有 experiments 对应父记录的 summary 被报告/跳过，不插入伪记录 |
| `test_research_key_reuse_force` | 相同研究键默认复用、强制运行新 Run；不同数据/基线不可错复用 |
| `test_run_lifecycle_recovery` | summary 成功但索引失败时无静默“完整成功”，补录能恢复 |
| `test_common_sample_alignment` | 三模型测试时间、target、训练时间/样本 ID 对齐 |
| `test_research_cli_contract` | `list/show` 输出可解析 JSON，能查询候选历史与详情 |

运行命令：

```bash
python -m compileall -q alpha_lab
python -m pytest -c /dev/null -p no:cacheprovider alpha_lab/tests/ -q
python -m alpha_lab.research_cli backfill --dry-run
python -m alpha_lab.research_cli backfill
python -m alpha_lab.research_cli list --limit 20
python -m alpha_lab.research_cli list --candidate intrabar_pressure_reversal
```

## 7. 真实验收：严格按照先后顺序

### Gate 1：兼容性

原有测试全部通过；`run_experiment(config_path)` 不带 candidate 的旧调用不变。`test_runner_lifecycle` 测试替身适配真实 `config.factors` 和新增参数，不允许为了通过测试而删除业务校验。

### Gate 2：历史实验落库

- `ResearchStore` 正常连接现有 SQLite，原 experiments 行数不减少。
- Run `612d083a617c401f91b8c6a28611a35a` 可查询，`incremental_status=not_evaluated`。
- Run `f91255f125a14d70a19c706612bdb13e` 能在原始报告可核实的前提下查询完整增量指标及 `INCONCLUSIVE`。
- 历史 `volume_divergence` 可按旧有报告情况索引；不存在增量结果时不得补造。
- `backfill` 连跑两遍，candidates/evaluations 计数不再增长。

### Gate 3：单命令完整研究

对 `intrabar_pressure_reversal` 调用一次 `candidate_cli run`（建议 `--force` 测一次以绕过去重），必须生成：因子评价、baseline 相似度、三模型评价、块长度 24/72/168 Bootstrap、Evidence Report、SQLite evaluation、实际执行源码快照。Run ID 必须只有一个。

### Gate 4：源码与去重

- 在备份/测试临时目录修改原始候选 `factor.py`，历史快照和历史研究指标不会改变。
- 重复提交完全相同研究键默认命中已完成实验，不重新训练；改变 horizon、基线或源码能生成不同研究键。
- 运行失败保留错误记录并能在查询工具中看到。

### Gate 5：研究结论诚实性

检查结果仍为 `INCONCLUSIVE`（若研究配置与模型未变）；不得为了产生 `PROMISING` 而改评价方法。说明历史结果仅是开发/滚动验证，不代表可交易收益、统计确定性或独立 OOS。

## 8. 实施顺序及边界

**P0（先做）：**
1. 核对工作树与现有数据库 schema、备份数据库。
2. 修复 CLI 默认 baseline + Runner 一次评价一次记录。
3. 实现从快照执行的不可变源码归档；修复输入哈希身份。
4. 完成 SQLite 幂等研究索引、迁移、backfill 和查询。
5. 通过 Gate 1–3。

**P1（随后做）：**
6. 研究键去重、并发占用、失败恢复；覆盖 Gate 4。
7. 完成 Evidence Report、Research Decision 分离与元数据；覆盖 Gate 5。
8. 清除重复研究入口（可保留 `experiments/check_incremental.py` 作为手动分析/回归脚本，但正式研究不得依赖它）；补充 README/CLI `--help`。

**不在本轮实现**：新 ML 模型、正交 IC、自动基线检索/向量化、全自动无限 Agent 循环、跨市场扩展、实盘策略、Docker 或权限沙箱。后续如需要可另建独立任务。

## 9. Agent 交付格式

施工结束后必须回答：

1. 修改/新增的具体文件和功能（按 P0/P1）。
2. 数据库旧/新表结构、迁移方式、是否保留了原历史行。
3. 真实 Gate 1–5 的测试命令、通过数量、错误日志（若失败）。
4. 两个 intrabar Run 的可检索结果与状态；哪个有增量哪个没有，不能混淆。
5. 新完成 Run 的 ID、源码 SHA-256、源码快照路径、research_key、SQLite 关联行、summary 路径。
6. 默认 `candidate_cli run` 是否只触发一次训练流程（必要的 A/B/C 三模型除外）。
7. 遗留风险和下一步建议（尤其 2025 数据是否历史污染不明、Bootstrap 与多次研究的统计限制）。

**完成标准：从 Agent 自选目录提交一个候选因子，到完整评价、不可变保存、SQLite 可查询、默认去重、历史可回溯，全程单命令，且不改变已有研究计算的数学语义。**
