# Alpha Lab 自主因子研究系统 — PR 施工任务书

版本：v1.0 | 基线：afantastic1/freqtrade · my_dev | 日期：2026-10-21

## 总目标
将现有 Data Engineering / DatasetBuilderV2 / Python Factor Service v2 / Agent Gateway v2 集成为可信的自主探索平台，再扩展因子注册、Agent 循环、多资产、多因子训练、Freqtrade 策略验证。

## 本轮原则
- 统计显著性正式检验暂缓，`formal_inference_status=BLOCKED` 保留；探索性报告不宣称显著或可交易。
- 安全隔离、PIT、真实数据、冻结 Manifest、授权 Campaign、历史 Evidence 不得绕过或篡改。
- 不用 Holdout 反复研究或调参；不同数据区间与资产集合不混作相同证据。
- 优先复用现有仓库实现，提交前审计重复模块；核心只读，扩展放非冻结层。
- 每个 PR 独立可验收，绑定 HEAD/测试命令/真实证据；未通过直接 BLOCKED。

## 当前已知状态（不是最新 HEAD 全量重测）
- 本文创建时的代码基线为 `my_dev` / `fef168b30f2fa6cbfc81e96ff43696dec00bb83b`；工作树仅有本文未跟踪。该记录不等于任何 PR 的验收基线；每个 PR 必须在自己的 HEAD 上重新生成证据。
- Catalog v3、Catalog source adapters 和 DatasetBuilderV2 均已有实现及历史验收报告；报告中的 BTC/多资产覆盖、分区哈希和构建指纹只证明各自记录的代码与数据快照，不自动证明当前 HEAD 或 Agent 生产部署可用。参考 `docs/alpha_lab/data_catalog_v3_acceptance.md`、`docs/alpha_lab/data_catalog_v3_source_adapters_acceptance.md`、`docs/alpha_lab/dynamic_dataset_builder_acceptance.md`。
- Agent Gateway、固定变换、Campaign/Request 契约和 Worker 执行边界已有实现；历史真实数据与隔离验收曾分别为 `BLOCKED_DATA_UNAVAILABLE` 和 `BLOCKED_ISOLATION_UNVERIFIED`。这不是 Gateway 单元测试失败，而是正式执行的独立准入证据缺失。参考 `docs/alpha_lab/agent-research-gateway-acceptance.md`、`docs/alpha_lab/agent-research-gateway-acceptance.json`。
- 最近可见的 Alpha Lab 收敛审计仍将整体准入标记为 `RC_BLOCKED`：研究进程无法证明物理排除 2025+ 数据、Agent 的有效 OS/runtime 隔离未验证、当前 HEAD 真实端到端重放未运行、历史 SQLite 缺少 v1.1 CS 表。参见 `docs/alpha_lab/v1.1-convergence-audit.md`；该报告 HEAD 为 `5d39cde83e36d25d77f9c8a2fb05976ec126d5b3`，早于本文记录的 HEAD，不能当作当前 HEAD 验收。
- 正式推断仍为 `BLOCKED`；现阶段只允许按明确范围展示描述性/诊断性结果并由人工复核。任何探索报告不得声称统计显著、正式支持/拒绝或可交易。

### 证据新鲜度与 PR 绑定规则

| 证据类别 | 可复用内容 | 必须在本 PR 重做/重新绑定 |
|---|---|---|
| 代码与冻结治理 | 已有 manifest、治理校验器和 CI 配置可复用 | 当前 HEAD、dirty state、manifest 校验输出、实际 CI run 与工作流版本 |
| 数据覆盖 | 历史 Catalog v3 source/partition 摘要可作候选数据源线索 | 当前 Catalog snapshot、源文件/分区 SHA-256、请求区间覆盖及缺失摘要；不可从旧报告推断当前 `AVAILABLE` |
| 本地构建与合成测试 | 历史构建指纹、回归范围可复用作复现线索 | 当前代码下的命令/结果；合成、真实数据、本地容器、正式 Agent 部署分栏报告 |
| 安全与 Agent | 八项隔离控制定义、Gateway 协议和旧负例可复用 | 当前 Worker 镜像摘要、五分钟内有效的可信 readiness、实际宿主/身份/挂载/网络负例及隔离证明 |
| Evidence/历史 | 旧 Evidence 保留其原始版本身份 | 不重写历史；新报告绑定 Request、Catalog、Worker、Evaluator、Evidence 身份及哈希 |

旧报告如果无法绑定当前 HEAD、数据身份或部署身份，只能标记为历史参考，不能作为当前 PR 的 PASS。遇到缺证、失配或命令未运行时，报告明确写 `BLOCKED` / `NOT RUN` 及具体缺口；不得把旧 PASS、合成结果或容器探针替代真实 Agent 部署验收。

## 依赖里程碑
M1 / PR-08～10：正式 Agent 真实研究闭环。
M2 / PR-11～18：20 次探索性候选尝试，完整历史与 Top 3（允许 0）。
M3 / PR-19～21：多资产与 Walk-Forward 预测研究。
M4 / PR-22～24：Freqtrade 回测、风险门禁与 Dry-run。

## 全局 Definition of Done
代码审查＋测试＋验收报告＋可复现命令＋数据/请求/源码/Evidence 身份；未经证实不得标 PASS。

## PR-08｜基线审计与冻结治理 [P0] — BLOCKED
**前置依赖：** 无

**施工任务：**
- [x] 记录 my_dev HEAD / dirty state / Manifest 校验 / CI；审核现有 Registry、历史 Store、增量评价，避免重复建设
- [x] 建立 Data Engineering → DatasetBuilder → Service → Gateway → Evidence 的调用链
- [x] 区分合成、真实数据、本地容器、Agent 正式环境四类证据

**重点路径：** `alpha_lab/`, `alpha_lab_service/`, `alpha_lab_contracts/`, `data_engineering/`, `.github/workflows/`

**交付物：** [当前 HEAD 绑定的审计矩阵、回归命令与结果、缺口及复用决策](alpha_lab/pr08-10-acceptance-report.md)

**验收门槛：** 单独提交审计结果；冻结 Manifest 完整；不声称未运行测试通过

## PR-09｜真实数据覆盖及可信部署 [P0] — DATA PASS / DEPLOYMENT BLOCKED
**前置依赖：** PR-08

**施工任务：**
- [x] 基于已存在 BTC 永续真实数据建立或核验 Catalog v3 映射，先查覆盖再决定补数据
- [x] 审计并复用 `alpha_lab_contracts` 的 Campaign Policy/ResearchRequest 契约；确认授权 policy 的 Discovery/Validation/Holdout 半开区间、资产、周期、数据类型及参数边界。新范围必须新建 policy 版本，不能改旧哈希或放宽已授权区间
- [ ] 部署只读数据挂载、独立系统身份、无网络/无 Docker socket、镜像摘要绑定及资源上限验证器
- [ ] 记录隔离证明与有效期，不允许伪造验收

**重点路径：** `data_engineering/sources/`, `data_engineering/catalog_v3.py`, `alpha_lab_service/runtime.py`, `alpha_lab_service/isolation_v2.py`

**交付物：** [BTC Coverage/Partition 摘要](alpha_lab/pr09-btc-coverage-evidence.json)；隔离和部署缺口见[验收报告](alpha_lab/pr08-10-acceptance-report.md)

**验收门槛：** Catalog 的可用性为 AVAILABLE；八项安全门禁全通过；所有报告基于当前部署

八项安全门禁以 `alpha_lab_service/isolation_v2.py::REQUIRED_ISOLATION_CHECKS_V2` 为唯一名称来源：`worker_image_immutable`、`worker_os_identity_separated`、`network_disabled`、`docker_socket_unavailable`、`read_only_root_filesystem`、`data_mount_read_only`、`stores_write_restricted`、`resource_limits_enforced`。readiness 必须与部署的 Worker digest 一致，验证时间不得超过五分钟；用配置文本、自测容器或合成请求代替目标部署证明均不通过。数据隔离还须覆盖研究进程的真实文件读取权限，单纯先读混合年份文件再过滤不满足物理边界要求。

## PR-10｜Agent MCP 真实因子端到端 [P0] — BLOCKED_NOT_RUN (PR-09)
**前置依赖：** PR-09

**施工任务：**
- [ ] 通过正式七工具 Agent Profile 完成 Coverage → Request → Submit → Run → Status → Report → History
- [ ] 运行 BTCUSDT 永续 Momentum 基线；核对输出与未来收益标签分离
- [ ] 模拟中断重启、重复提交和无权限请求；核对 Evidence 身份与不可变性

**重点路径：** `alpha_lab_service/server.py`, `alpha_lab_service/agent_gateway_v2.py`, `alpha_lab_service/jobs_v2.py`, `alpha_lab_service/store_v2.py`

**交付物：** 真实运行日志、Request/Catalog/Worker/Evidence 哈希及报告

**验收门槛：** 真实数据 E2E PASS，失败时保持 BLOCKED，不使用 mock 冒充

七工具及流程顺序按 `alpha_lab_service/agent_gateway_v2.py` 的实际接口核对：Coverage、Request、Submit、Run、Status、Report、History。若任一步依赖 PR-09 未通过的准入门禁，则保持 BLOCKED，不得以绕过门禁的直接 Worker 调用拼成 E2E PASS。真实 E2E 的结论仍须将 `formal_inference_status=BLOCKED`。

## PR-11｜探索性因子评价扩展 [P1]
**前置依赖：** PR-10

**施工任务：**
- [ ] 复用描述性 evaluator，补足 ICIR、跨月方向一致性、因子有效覆盖、成本敏感性
- [ ] 时间序列与横截面指标语义分离；检查分位阈值是否用到了未来样本
- [ ] 设计规范化 Metric Schema 及版本；保留原始值和分区口径

**重点路径：** `alpha_lab_service/evaluation_v2.py`, `alpha_lab/evaluation/`

**交付物：** 统一描述性报告及单元/真实数据回归

**验收门槛：** `formal_inference_status` 始终为 `BLOCKED`；新增指标只属描述性/诊断性；不把全样本事后分位当历史可交易信号

先盘点 `alpha_lab_service/evaluation_v2.py`、`alpha_lab/evaluation/` 和现有 Evidence 字段，列出已有指标及语义，再补缺失项；Metric Schema 必须区分时间序列与横截面聚合单位、空值/无效覆盖规则、分区范围、成本假设和 evaluator 版本。不得为了生成 ICIR 或月度方向摘要引入未经校准的显著性判断。

## PR-12｜统一 Factor Registry [P1]
**前置依赖：** PR-10, PR-08

**施工任务：**
- [ ] 审计 experiment_registry.py、factors/registry.py 和现有 Evidence Store 后优先扩展
- [ ] Factor ID 与 Evidence ID 分离；版本、父子因子、源码/参数哈希、假设分类及人工意见可查询
- [ ] 同条件幂等写入、冲突拒绝、历史 append-only

**重点路径：** `alpha_lab/experiment_registry.py`, `alpha_lab/factors/registry.py`, `alpha_lab_service/store_v2.py`

**交付物：** 注册表迁移/适配说明、API、索引测试

**验收门槛：** 用 20 条隔离测试夹具验证跨窗口索引可按因子/数据集/状态查找、同条件幂等和冲突拒绝；验证旧数据未被改写。真实 Campaign Evidence 数量在 PR-18 验收，不能用夹具冒充真实研究记录。

## PR-13｜去重、相似度和增量价值 [P1]
**前置依赖：** PR-11, PR-12

**施工任务：**
- [ ] 实现源码+参数+研究条件精确去重
- [ ] 同面板下的输出相关性、增量价值诊断，识别经济逻辑近似但表达不同的因子
- [ ] 精确身份包含规范化源码哈希、参数、Campaign/Policy、数据集与 Manifest 指纹、区间、周期、Universe、模式、标签周期及 evaluator 版本
- [ ] 相似度/增量比较只接受相同面板和标签口径；明确不同日期/周期/Universe 不可直接比较

**重点路径：** `alpha_lab/evaluation/incremental.py`, `新增非冻结扩展层`

**交付物：** Duplicate/Similarity Report 和缓存命中记录

**验收门槛：** 不会因语义相近而强制覆盖不同因子；无未来数据泄漏

## PR-14｜排行榜及人工候选审查 [P1]
**前置依赖：** PR-11, PR-12, PR-13

**施工任务：**
- [ ] 按比较域分组展示 IC、稳定性、覆盖、成本、去重、增量结果
- [ ] 记录人工批准/拒绝与理由；禁止自动升级为统计显著
- [ ] 建立 Top-K 的可解释排序与回溯

**重点路径：** `非冻结 service 扩展与 CLI`

**交付物：** 排行榜、Top-K 摘要及人工审查接口

**验收门槛：** 跨域不得直接混排；每个排名可回溯 Evidence

比较域至少绑定 mode、数据集及数据指纹、时间区间、timeframe、Universe、label horizon、evaluator/metric schema 版本和成本假设。任一维度不匹配时分组展示或拒绝比较；排序规则、缺失指标处理和并列规则写入版本化配置，禁止在结果出来后调整排序规则再回排同一批候选。

## PR-15｜最小自主研究编排器 [P2]
**前置依赖：** PR-10, PR-12

**施工任务：**
- [ ] 建立 PLAN→PROPOSE→SUBMIT→RUN→EVALUATE→DECIDE→NEXT 状态机
- [ ] 保存循环状态、失败原因与任务 ID，实现幂等恢复
- [ ] Agent 不得修改冻结核心；Worker 不得创建新任务

**重点路径：** `新增 agent orchestration 层；既有 Gateway`

**交付物：** 可恢复研究循环及失败场景测试

**验收门槛：** 从既有证据出发能自主提出并执行多个候选

## PR-16｜预算、策略与停止规则 [P2]
**前置依赖：** PR-15, PR-13

**施工任务：**
- [ ] 探索/改良/多样化三种策略
- [ ] 限制任务数、计算预算、失败重试、相似因子比例与分组配额
- [ ] 禁止用 Holdout 反馈改进候选

**重点路径：** `orchestrator 配置和评估`

**交付物：** 预算记录、停止原因、拒绝路径

**验收门槛：** 预算耗尽/越权/数据不足均安全停止

## PR-17｜Agent Research Skill 与工具说明 [P2]
**前置依赖：** PR-15, PR-16

**施工任务：**
- [ ] 精简可复用 Skill，包含 Factor API、工具七项、报告契约与决策规则
- [ ] 将数据/字段目录改为按需发现，避免超长上下文
- [ ] 将经济假设与失败分析写入注册表

**重点路径：** `skills/ 或 repo 文档`, `Agent Gateway`

**交付物：** Skill、示例任务、最短提示词

**验收门槛：** 无需人手改源代码即可从请求走到评价报告

## PR-18｜20 因子研究验收 [P2]
**前置依赖：** PR-11, PR-12, PR-13, PR-14, PR-15, PR-16, PR-17

**施工任务：**
- [ ] BTC 永续 5m/15m/1h 授权 Discovery 上至少尝试 20 个候选
- [ ] 包含 Momentum/反转/波动/量价/信号处理等异质假设
- [ ] 去重后形成不超过三条值得人工审查的候选；记录全部失败与预算

**重点路径：** `Agent + 全研究链路`

**交付物：** 完整 20 次尝试清单、Top 3（可为 0）报告、复现命令

**验收门槛：** 100% 尝试可追溯，Holdout 未用于反馈，未擅自宣称显著

每次尝试记录 Candidate/Factor ID、Evidence ID（若已完成）、Request/Campaign Policy 哈希、数据 Manifest/区间/Universe/timeframe、源码/参数哈希、失败阶段与原因、预算消耗和重试关系。20 次是至少 20 个可区分的预登记候选尝试；重复提交的幂等重放不重复计数。报告分别列出提交、执行、评价成功数和失败数；Top 3 允许少于三条或为空。

## PR-19｜多资产多源研究验收 [P3]
**前置依赖：** PR-18

**施工任务：**
- [ ] BTC/ETH/SOL/BNB/XRP 的 TS/CS 可用覆盖检查
- [ ] Funding/OI/现货/永续的真实 PIT 可用性和延迟校验
- [ ] 动态 Universe 与固定 Universe 区分幸存者偏差

**重点路径：** `data_engineering/`, `alpha_lab/data/`

**交付物：** 多资产多源真实数据验收

**验收门槛：** 至少两周期一横截面可复现；PIT 证据可核验

## PR-20｜多因子特征矩阵和训练 [P4]
**前置依赖：** PR-18, PR-19

**施工任务：**
- [ ] Registry → 特征矩阵的因果时间对齐；先以现有 Ridge / Logistic 与 Walk-Forward 路径为基线，记录复用或新增边界
- [ ] 训练折内拟合标准化、缺失、因子选择；Ridge/ElasticNet 基线
- [ ] 只有在依赖与样本规模审查通过后才增加 LightGBM 对照；否则保留线性基线并将树模型记为未运行，不为满足计划而引入未经验证的重依赖

**重点路径：** 先审计并复用 `alpha_lab/models/cross_sectional.py`, `alpha_lab/cross_sectional_runner.py`, `alpha_lab/models/evaluator.py`, `alpha_lab/validation/walk_forward.py`；只有现有接口无法表达 Registry 特征矩阵时才新增独立 training 扩展层

**交付物：** Walk-Forward 折内产物、模型及数据指纹

**验收门槛：** 未触碰 Holdout；训练运行可复现

每折需保存训练/测试区间、输入因子 Evidence ID、数据和特征矩阵指纹、训练折拟合的转换器状态、模型参数及代码版本。Discovery/Validation 可用于开发流程的规定阶段；Holdout 在候选、超参数和流程冻结前不得读取，读取后只能作为一次性最终报告，不得反馈到模型选择。

## PR-21｜多因子模型评价与审批 [P4]
**前置依赖：** PR-20

**施工任务：**
- [ ] 比较单因子、简单等权与 ML 的 OOS 信号效果
- [ ] 成本敏感性、稳健性和人工批准流程
- [ ] 生成独立信号契约而非交易指令

**重点路径：** `training/evaluation`, `审批接口`

**交付物：** 模型评估与批准记录

**验收门槛：** 只有经人工确认的模型/规则可交给交易层

## PR-22｜Freqtrade 信号适配 [P5]
**前置依赖：** PR-21

**施工任务：**
- [ ] 定义时间戳、标的、信号、目标持仓契约
- [ ] 实现不包含未来标签的 freqtrade strategy adapter
- [ ] 对齐交易所精度、可交易时间与手续费

**重点路径：** `strategy_adapter/`

**交付物：** 可复现回测示例

**验收门槛：** 不存在未来标签穿透

## PR-23｜风险与成本执行门禁 [P5]
**前置依赖：** PR-22

**施工任务：**
- [ ] 风险预算≤账户权益 2%/单笔；下单量不足时拒单
- [ ] 止损、杠杆、最小名义金额、滑点及资金费率一致性
- [ ] 确认 stake 异常不会回退到引擎默认的超额下单

**重点路径：** `strategy_adapter/risk_engine.py`, `Freqtrade integration tests`

**交付物：** 极端场景风险回归

**验收门槛：** 无法合法下单时 fail closed；无静默加仓

## PR-24｜回测与 Dry-run 发布门禁 [P5]
**前置依赖：** PR-23

**施工任务：**
- [ ] 冻结策略版本并生成成本完整回测
- [ ] 至少一段滚动 OOS、压力与敏感性检查
- [ ] Dry-run 验收与实盘独立人工授权

**重点路径：** `Freqtrade 策略与部署配置`

**交付物：** 回测、Dry-run 和风险审查报告

**验收门槛：** 默认不启动真实资金交易

## 并行建议
PR-11 与 PR-12 可并行；PR-13 依赖二者；PR-15 可在 PR-12 后与评价改进并行。其余严格按门禁推进。多人不得同时修改冻结 Manifest 或同一数据 Store。

## 不在此次施工范围
正式推断校准/FDR 的解除、强化学习交易、自动启动实盘、复杂多 Agent 辩论。后续仅在研究闭环稳定且证据充分时另立 PR。
