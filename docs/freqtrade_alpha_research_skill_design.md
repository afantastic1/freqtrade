# Freqtrade Alpha Research Skill — 设计文档

> 版本：v1.0（设计稿）  
> 日期：2026-10-16  
> 目标：OpenCode 可复用的自主 Alpha 因子发现、评估、记忆和 Freqtrade 策略验证 Skill  
> 默认市场：Binance USDT 本位永续；起点 BTC/USDT；框架可扩展到多个币种  
> 状态：待仓库审计后实施；本文是目标架构，不声称已经实现

## 1. 目标与边界

### 1.1 目标

建立 `.opencode/skills/freqtrade-alpha-research/SKILL.md`，让 OpenCode 在现有 Freqtrade 项目内独立执行以下闭环：

1. 查询过往研究及失败机制；
2. 从经济机制或已有因子库提出可反驳的研究假设；
3. 实现并校验无未来数据泄漏的因子；
4. 用独立的研究评估器检验预测能力；
5. 对新颖性、样本外证据和可交易性分层判定；
6. 仅将少数候选接入现有 Freqtrade 运行与风险模型；
7. 持久化全部成功与失败结果，依据结果规划下一轮。

成功定义是 **可重复的研究流程和可信证据**，而不是每一轮都必须找到盈利因子。

### 1.2 明确不做

- 不开发新的 Agent 框架、Web UI、行情采集平台或独立交易引擎。
- 不让 LLM 直接执行任意 SQL 修改历史实验结论。
- 不以单一 Sharpe 或历史收益作为因子筛选标准。
- 不默认开启自动实盘或自动解锁最终验证集。
- 不将股票横截面 rank / neutralize 直接照搬到单一 BTC。
- 不无差别导入外部因子库全部依赖；优先抽取可用表达式和必要算子。

### 1.3 沿用既有研究约束

前序 BTC 研究中的 A（无条件突破）和 B（资金费偏冷）为冻结的 NO-GO 候选。已发现：历史研究存在滑点缺口、不同回撤与收益口径、有效样本偏少；将这些作为基线回归测试，不得在新系统内静默改写结论。

风控初始建议沿用已审计配置（以仓库实际 `RiskCfg` 为唯一事实来源）：单笔目标风险 1%，事前风险硬上限 2%，并发总风险 4%，日亏 4% 暂停，最大回撤 10% 停机待人工复核。这些属于研究/执行约束，而非最大实际亏损保证。禁止为了凑最小下单额放宽限制。

2022–2024 已被既有研究反复使用，属于探索性历史区；2025 被参考污染；2026 是锁定区，未经明确批准不得用于自主搜索或评价。代码与数据权限同时强制执行，而不只依赖提示词。

## 2. 总体架构

```text
User → OpenCode → freqtrade-alpha-research/SKILL.md
                          │
             参考文档 + 受控 Python CLI
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   Research DB         Factor Lab        Freqtrade Adapter
   SQLite              Pandas/NumPy       Existing strategies
   假设/因子/实验       因子/标签/统计      风控/成交/回测
        │                 │                 │
        └────────────┬────┴─────────────────┘
                     │
                 Parquet Store
                     │
        Critique → 下一轮候选或停止
```

**职责隔离**：LLM 提出假设、解释证据和选择探索方向；Python 工具控制数据访问、计算、统计、状态迁移、预算和持久化；Freqtrade 仅负责最终策略运行与执行语义。不要将测量数据交给 LLM 手算。

## 3. 目录结构

```text
.opencode/skills/freqtrade-alpha-research/
  SKILL.md                         # 入口；尽量简短
  references/
    research-protocol.md            # 因果、数据拆分、统计证据标准
    factor-catalog.md               # 因子源、适用场景、表达式语义
    freqtrade-adapter.md            # 版本检查、策略/风险接口和成本语义
    experiment-policy.md            # 预算、去重、停止、人工门控
  templates/
    hypothesis.yaml                 # 研究假设契约
    experiment.yaml                 # 实验配置契约
  scripts/
    alpha.py                        # 唯一 CLI 入口，可转发项目内部模块

user_data/research/
  alpha.sqlite                     # 元数据/研究记忆，生产默认不提交 Git
  parquet/                         # 因子、标签、分组收益及权益曲线
  reports/                         # 每轮报告
  runs/                            # 配置、stdout、校验与结果摘要

# 如仓库已有对应目录、DB 或研究包，优先复用，不要照搬此结构新增一套。
```

实现落点应由仓库审计决定；Skill 仅是发现入口与协作规范。重计算逻辑应该位于现有 Python 包或 Skill 中的轻量脚本，避免把大量业务代码堆进 SKILL.md。

## 4. SKILL.md 契约

示意 frontmatter：

```yaml
---
name: freqtrade-alpha-research
description: Discover, test, catalog and critique causal quantitative alpha factors using local market data; optionally validate qualified candidates with Freqtrade. Use for factor discovery, IC research, robustness testing, and research continuation; never trade live.
compatibility: opencode
metadata:
  opencode/slash: "true"
---
```

正文只包含：触发场景、默认安全模式、必读参考文档、CLI 查询方式、严格阶段顺序、证据分级、停止与升级条件。更细的统计标准放 `references/`，通过按需读取降低上下文负担。

**建议阶段**：`inspect → retrieve → hypothesize → implement → check → evaluate → critique → (optional) backtest → persist → repeat`。

AI 在进入任何阶段前读取该阶段规则；不允许跳过 `check` 直接回测。触发器可以是 `/freqtrade-alpha-research`，也可根据 OpenCode 实际版本的 Skill 自动发现机制加载。运行前用本地 OpenCode 版本验证路径、权限及 slash 支持情况。

## 5. 数据层与时间语义

### 5.1 数据适配

现有 Freqtrade OHLCV / funding / informative 数据接口优先；研究层将输入统一为规范化的只读 DataFrame 或 Parquet。逐源注册：市场、交易所、交易对、周期、时区、可观察时间、下载时间、修订版本、schema、SHA256。

字段定义：`event_ts`（交易所事件时间）、`available_ts`（决策者实际可获得时间）、`bar_open_ts` / `bar_close_ts`、`source_version`。所有时间统一 UTC，因子只能读取 `available_ts <= decision_ts` 的信息。

- 对完整 K 线而言，使用 `bar_close_ts` 后才能观察完整 OHLCV，实际下单不得假设仍能以该 bar 的收盘价成交。
- 资金费率需区分公告/预测费率与已结算费率；已结算值不能回填到其结算前的决策时点。
- 多周期 4h → 1h / 15m 合并需按已完成高周期 K 线的可用时刻进行 as-of join，并对早期行严格置空。
- OI 数据短且处于锁定区时标记不可用于当前研究，不允许透传到实验。
- 对 spot/perp 配对要检查事件时间一致性、数据滞后、缺失值和成交量单位。

### 5.2 Dataset Guard（强制）

数据集按权限分类：`EXPLORATORY`、`REFERENCE_CONTAMINATED`、`LOCKED`、`INDEPENDENT_TEST`。当前 BTC 2026 默认 `LOCKED`。所有读数据经受控 loader，以**路径白名单、分区校验、日期窗口校验、权限检查**为准；非法读取直接报错并写审计事件。

测试集访问由人工显式批准并产生只读快照，不能让自动循环自行批准。外部原始文件如果不经过 loader 可被任意访问，则需在进程/工作区或文件权限层进一步隔离；单靠 Python 检查不是强沙箱。

### 5.3 标签与成交时点

标签不能直接默认使用 `close[t+h] / close[t] - 1` 作为可实现收益。如果信号在 t 收盘后可得，定义 `entry_ts` 为最早可成交时刻（如 t+1 开盘或保守撮合），预测收益基于可执行 entry 口径，报告同时可保留理论 close-to-close 标签，但要明确区分。

同时保存 `horizon`、方向、手续费假设、交易执行假设与是否重叠标签。不同 h 的结果不能混排。针对 6/12/24/48h 的多重尝试做统一检验与记录。

## 6. 因子知识库（Factor Registry）

来源分三类：

1. **Baseline**：Qlib Alpha158、Alpha101 等明确可迁移的公开表达式；记录许可证、原始版本、是否跨资产算子。
2. **Adapted**：依据 BTC spot/perp、资金费、波动和趋势特征改造的机制型因子；明确原公式与改动原因。
3. **Novel**：由 OpenCode 从经济机制出发提出的新因子。

每条因子至少记录：`factor_id`、`hypothesis_id`、`name`、`formula_ast`、`formula_hash`、`semantic_family`、`inputs`、`lookback`、`frequency`、`availability_rule`、`direction`、`source`、`license`、`implementation_hash`、`created_at`。参数化实例需记录 `parameter_json` 与独立 `instance_hash`。

限制因子表达式为**安全、可审计的算子白名单**，第一版可用受控 Python 函数库：returns、rolling mean/std、ewm、zscore、rolling rank（时间序列）、ATR、volatility、cross-asset spread、safe divide、lag、interaction。不要用 eval 执行任意外部表达式。所有公式由可复现 AST 或显式 Python 实现并附单元测试。

### 去重策略

- 第一层：AST 规范化 + 参数规范化 + 哈希；同义别名统一。
- 第二层：经济机制文本检索（SQLite FTS5）+ 相似家族标记；用于提示重复风险而非机械拒绝。
- 第三层：同一**固定公开探索窗口**下的数值相关性及符号反转检查；高相关意味着重复信息可能大，不代表严格等价。
- 第四层：基于已有基线的条件增量检验，避免重复包装资金费/基差等已知因子。

防止字段和算子被长期忽视：在检索中可报告数据覆盖率、研究次数和空白方向，鼓励有经济意义的新方向；不得为了字段或算子配额捏造无依据公式。

## 7. SQLite 模型（最小五表）

### 7.1 `hypotheses`

`id, thesis, mechanism, expected_sign, horizon, data_requirements_json, falsification, semantic_family, parent_id, status, created_at`

### 7.2 `factors`

`id, hypothesis_id, name, formula_ast, formula_hash, instance_hash, source, license, inputs_json, availability_rule, code_hash, status, created_at`

### 7.3 `experiments`

`id, factor_id, run_id, dataset_id, dataset_hash, split_role, config_hash, code_commit, target_spec_json, test_family_id, status, started_at, finished_at, artifacts_json`

### 7.4 `evaluations`

`id, experiment_id, horizon, metric_schema_version, metrics_json, intervals_json, q_value, gate_decision, failure_reason, created_at`

### 7.5 `research_runs`

`id, previous_run_id, purpose, proposal_budget, eval_budget, used_budget, decision_json, status, created_at, finished_at`

增加适当外键与唯一约束：`(factor_id, dataset_hash, config_hash, code_commit, target_spec)` 形成缓存键；`experiment_id` 一次配置唯一。策略回测可以作为类型为 `backtest` 的 experiment，仍需关联相应 factor/因子组合元数据，必要时增加最小关联表而不是把多个因子字符串塞进一个字段。

数据库使用 SQLite WAL、事务、参数化查询、schema migration；历史结果只追加，纠错通过新的 revision、supersedes_id 或勘误事件关联，禁止覆盖导致无法追溯。Parquet 保存时间序列并通过路径、schema hash、文件 hash 关联。CLI 输出 JSON，不允许 LLM 任意写 SQLite。

### 状态与证据分开

研究状态：`PROPOSED / IMPLEMENTED / EVALUATED / REJECTED / INSUFFICIENT / PROMISING / VALIDATED / ARCHIVED`；证据等级：`EXPLORATORY / INTERNAL_OOS / INDEPENDENT_OOS / DEPLOYMENT_READY`。`VALIDATED` 不能仅因历史 IC 显著而产生；`DEPLOYMENT_READY` 还必须通过真实成本、风险、执行与人工批准。

## 8. 自主研究状态机

```text
START
  ↓ inspect: 仓库/数据/风险/技能依赖检查
RETRIEVE: 查已有假设、失败机制、同族因子
  ↓
HYPOTHESIZE: 经济机制 + 反证条件 + 预测窗口预登记
  ↓
IMPLEMENT: 提交候选公式和确定性测试
  ↓
CHECK: 数据合法性 / lookahead / NaN / 参数合法性 / 去重
  ├─ fail → REJECTED/INSUFFICIENT → CRITIQUE
  ↓
EVALUATE: IC、分组收益、条件增量和稳定性
  ↓
CRITIQUE: 复盘失败原因；标记探索而非独立证据
  ├─ 新颖但无证据 → 保留研究记录 → 下一轮
  ├─ 有价值但样本不足 → INSUFFICIENT → 不部署
  ├─ 明确未过门槛 → REJECTED → 下一机制
  └─ 初筛通过 → PROMISING →（许可后）策略构建
                                      ↓
                                 FREQTRADE BACKTEST
                                      ↓
                              COST / RISK / OOS GATES
                                      ↓
                                REPORT + PERSIST
```

让 OpenCode 独立决定每轮新假设方向，但允许受控随机性与预算约束。建议默认 10 轮，每轮最多 3–5 个假设、每个假设最多 2 个预登记表达式；这些是资源预算上限，不是成功率目标。超出预算需人工批准。

### 下一轮选择策略

采用三种可解释决策：`REFINE`（验证新颖假设的薄弱点）、`EXPLORE`（提出全新经济机制）、`CONDITIONAL`（研究市场状态条件）。保留与 parent hypothesis 的关系；原验证失败不能因为换了窗口/阈值而删除。

## 9. 因子独立评价协议

### 9.1 指标层

- `IC`（单资产时间序列相关性），`Rank IC`、稳健 IC、rolling IC、ICIR。
- 分位组平均/中位未来收益、Q5-Q1 差异、方向单调性、不同市场状态分层结果。
- 按年度或连续 walk-forward 分段一致性、有效样本块数量、CI、HAC 或保序 block bootstrap。
- 多重检验校正（例如 BH/FDR），明确整个同批测试家族；报告累积搜索次数。
- 与既有基线的相关性、部分回归与 out-of-sample 增量（条件预测价值）。
- 数据完整性、样本覆盖、稳定性、参数敏感度与因子 turnover（指标波动频度）。

单资产不能直接将股票横截面 IC 定义迁移使用。因子 IC 指示预测关联，**不是**盈利保证，也不是无需策略回测的证明。正交性弱不等于可交易性为零，必须分别判定 **独立 Alpha** 与 **绝对可交易收益**。

### 9.2 分层验证与污染管理

当前历史数据主要支持探索性候选筛选。即便使用 expanding-window 或 walk-forward，如果人/Agent 已反复查看相同时间段并调整假设，该段就不能再次被宣称为全新独立测试。数据库必须追踪 `data_exposure`、`selection_history`、`test_family_id`。

未来的独立测试必须采用：冻结代码/表达式/阈值/成本协议 → 人工批准解锁一次 → 只读跑一遍 → 结果进入不可修改证据记录。失败不可再次调整后将同一段继续称为最终 OOS。2026 锁定集保持默认不可读。

### 9.3 判定层

不要仅使用魔法数字 IC>0.03 或 t>2 来筛因子。门槛配置显式版本化；基本分级：

- `REJECTED`：数据/因果不合法、公式重复无新增价值、经适当检验未达到预先登记的候选标准。
- `INSUFFICIENT`：有效样本不足、缺少独立数据、区间过宽或执行细节未知。
- `PROMISING`：探索性证据支持、方向有经济解释且未显示明显失效；**不代表可部署**。
- `VALIDATED`：获得按协议冻结的独立 OOS 证据，仍需执行层校验。

在报告中同时保留点估计、区间、稳定性、经济大小、交易成本敏感度和反证条件。

## 10. Freqtrade 适配器边界

### 10.1 进入条件

只有 `PROMISING` 或更高且具备明确可执行入场/退出规则的候选才能构建 Freqtrade 策略。将因子转为信号是新实验：阈值、持有期、过滤器、止损与仓位均需单独记录版本和选择历史。

### 10.2 基于现有仓库适配

审计当前安装 Freqtrade 版本、策略基类、`populate_indicators`、`populate_entry_trend`、`populate_exit_trend`、`custom_stake_amount`、`confirm_trade_entry`、`custom_stoploss` 等行为；不要假设上游最新版与本地 2026.9 一致。

复用当前的 `RiskCfg` 与修复过的 fail-closed 风控，新的适配策略不得覆盖已有修复。回测前确认 `trading_mode=futures`、保证金、杠杆、做多/做空、交易所约束、历史最小数量/名义、精度、手续费、资金费等。最小下单约束需按价格动态计算，并注明历史/当前规则是实际数据还是敏感性情景。

### 10.3 成本与停机

严格分开：

1. 定仓用预估成本缓冲（缩小可用名义，不是 PnL 扣费）；
2. 预测标签用研究成本（必须标注模型）；
3. 回测成交的实际手续费、资金费与补充滑点。

禁止将“定仓考虑 30bps”当作“回测已经扣除 30bps”。对手续费、滑点、资金费和强平/跳空/止损失效分别进行基准与压力情景。

净收益、CAGR、最大相对回撤、Freqtrade 原生回撤、逐笔 risk、停机后 sticky 净收益必须使用独立且一致的字段；不能混填或用非停机回测收益替代可执行结果。若采用 cooldown 必须明确是假设而非生产规则。

### 10.4 风险检查与测试

每个回测策略执行：静态因果检测 + 流式/截断重算 + Freqtrade `lookahead-analysis` + 必要的 `recursive-analysis`（适用于当前版本）；后二者是辅助检测而不是充分证明。加入按 bar 决策日志和逐笔权益复算，防止未来数据和权益双计入。

## 11. CLI 接口设计

统一入口 `python .opencode/skills/freqtrade-alpha-research/scripts/alpha.py <command>`；可按现有项目 Python 模块做薄封装。

```bash
alpha.py inspect
alpha.py catalog import --source qlib-alpha158 --dry-run
alpha.py catalog search --query "funding crowding" --limit 20
alpha.py hypothesis propose --file proposal.yaml
alpha.py factor validate --factor-id F001
alpha.py factor evaluate --factor-id F001 --config experiment.yaml
alpha.py research next --budget-runs 1
alpha.py research resume --run-id R001
alpha.py strategy backtest --factor-id F001 --config strategy_experiment.yaml
alpha.py report --run-id R001
alpha.py dataset status
```

实际使用中的命令行以本项目实现为准，不要求独立 pip 安装或全局可执行。所有命令支持 `--json` 且失败非零退出；写操作默认限制在 `user_data/research`。`--dry-run` 只预览变更，不向交易所下单；`strategy backtest` 只能启用回测模式。

建议工具输入契约（最小）：

```yaml
hypothesis_id: H001
mechanism: "异常资金费可能反映拥挤多头，未来存在反转风险"
falsification: "在预登记窗口净收益/条件增量不大于零"
family: derivatives-crowding
inputs: [funding_rate, perp_close]
frequency: 1h
horizons: [6h, 24h]
access: EXPLORATORY
max_expressions: 2
```

工具返回结构化结果：`experiment_id, status, data_hash, n_raw, n_eff, metrics, CI, multiple_testing, failure_reason, evidence_level, artifacts`。

## 12. 外部因子库接入

只要表达式而非整套重依赖：

- Qlib Alpha158 / Alpha360：提取可映射的时序表达式，识别是否依赖股票 universe；保留原始出处与许可证。
- Alpha101：作为公开机制和公式参考；对仅有单 BTC 无法定义的横截面算子标记 `UNSUPPORTED_SINGLE_ASSET`，**不**静默替换成时间序列 rank 并冒称原始因子。
- pandas-ta-classic / TA-Lib / freqtrade/technical：作为可选计算后端，优先复用已经安装的工具，确保与项目环境兼容。

导入流程：`发现源码/许可证 → 映射输入与算子 → 可计算性检查 → deterministic fixture 对照 → metadata 注册 → 探索性基线实验`。如果外部源码许可证不允许直接复制则只保存概念来源，重新独立实现并完成合规检查。

## 13. 测试计划与验收标准

### P0 — 现场审计/冻结接口
- 确认 Freqtrade 版本、数据目录、研究脚本、现有两个 Skills、已审计风控实现及 P0.5–P4 资产。
- 输出复用/删除/新增清单，禁止在不了解 repo 时重复造轮子。
- 测试：2026 数据在无批准状态下被读写请求必须被拒；历史策略文件与风险阈值不被改动。

### P1 — Skill + 元数据库
- Skill 能被 OpenCode 识别和按需加载；`inspect/search/register/report` CLI 可用。
- SQLite schema 可迁移、事务/唯一索引生效、历史失败保留、重跑使用缓存。
- 测试：相同定义/参数/数据/版本不重复插入；不同 horizon 不混排；并发写无记录损坏。

### P2 — 因子计算 + 基线接入
- 导入少量确定性 Alpha158 与可迁移 Alpha101 示例（不是一次性全量）；完整存源、公式、适用性和许可证。
- 测试：用合成数据注入未来信息时被检测；4h 视图提前可见被阻断；资金费 availability 失配被阻断；相同因子重算逐位或容差一致。

### P3 — 研究自动循环
- 可连续完成 10 轮，自动查询历史、提出假设、评估、保存失败并规划下一轮；遇预算和无新假设正常停止。
- 测试：故意让候选全部失败，系统仍产出完整报告；研究重启后续跑不重复试验；搜索耗尽不靠改阈值自救。

### P4 — Freqtrade 接入
- 至少一个合格示范候选能转换为 Freqtrade 回测策略；风控 fail-closed；验证手续费、资金费、滑点差异；对账 sticky 停机与回撤口径。
- 无实盘/无下单；审计测试不退化；`lookahead-analysis` 通过或正确输出不能使用的原因。

### P5 — 人工门控与证据冻结
- 对 2026 等 LOCKED 数据默认拒绝；尝试解锁必须人工批准并生成记录。
- 所有候选报告有污染状态及证据级别；在没有独立测试前绝不显示 `DEPLOYMENT_READY`。

## 14. 风险与故障处理

| 风险 | 控制 |
|---|---|
| 搜索过拟合 | 预算上限、预注册、FDR、锁定测试、搜索历史 |
| 未来函数/错时 | available_ts、as-of 对齐、流式重算、lookahead |
| 看似新因子实为变体 | AST + 语义族 + 数值相关 + 条件增量 |
| 统计显著但不可交易 | 成本/换手/容量/流动性/停机验证 |
| 小样本停机率为零 | 报告 n_eff 和 CI，不能推断安全 |
| LLM 改写历史结果 | DB append-only revision、工具权限、审计轨迹 |
| 文件堆积/数据库膨胀 | Parquet 分区、内容哈希、实验缓存、垃圾收集保留策略 |
| 经典库许可证与适配 | license inventory、显式不支持算子、版本固定 |
| 研究循环失控 | 单轮/总实验、CPU 时间、token、存储配额及停止条件 |
| 正交检验结果误用 | 区分独立 Alpha 与自身经济价值两套判定 |

## 15. 首期交付与禁止的过度设计

首期只要求一套 Skill、一个统一 CLI、一个 SQLite、Parquet 存储，以及对**现有** Freqtrade 的薄适配。先验证一个参考因子从注册、数据对齐、因子评价、记录、回测到报告的完整链路，然后再做十轮自主搜索。

暂不开发：Web UI、独立多 Agent 系统、向量 DB、在线交易服务、GPU 因子计算框架、复杂分布式队列、新行情采集平台。

## 16. 向 OpenCode 下发的实施总指令

> 审计当前 Freqtrade 仓库、两个现有 Skills 和研究成果，依据本设计实施 `.opencode/skills/freqtrade-alpha-research`。优先复用既有研究工具、风控模块和数据文件；不能静默改写 P0.5–P4 结论。按 P0→P5 分阶段施工，每阶段运行单元及集成测试并提交进度报告；先交付端到端的一因子闭环，再运行 10 轮预算受控的自主研究。2026 锁定数据不可访问，任何实盘下单不可启用。不要用新的 Agent 框架、向量库或 Web UI 代替轻量 Skill + Python CLI + SQLite/Parquet + Freqtrade 的目标架构。

## 17. 参考资料与实现核验

- OpenCode Skills 官方文档：https://docs.opencode.ai/docs/skills/
- OpenCode Skills v2：https://dev.opencode.ai/v2/docs/skills/
- Freqtrade Backtesting：https://docs.freqtrade.io/en/latest/backtesting/
- Freqtrade Lookahead Analysis：https://docs.freqtrade.io/en/2026.5/lookahead-analysis/

> 具体功能参数与代码语义需以项目实际安装的 Freqtrade 版本为准。该文档的目标是指导实现，不代表已经完成源码核查或实机/交易所验证。
