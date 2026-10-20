# Alpha Lab 增量源码与 PIT 准入审计

审计基线：`3671fd70e0227c0997be767679ac3a33192b6221`  
分支：`codex/fundamental-data-adapter`  
范围：指定 Alpha Lab / Data Engineering 模块，14 个本地 Catalog snapshot manifest；不读取 2025+ 行情数据。  
结论：**v1.1 仍为 RC_BLOCKED；Agent 正式研究准入 BLOCKED。**

## 1. 已有功能（无需重做）

| 能力 | 调用链与实现位置 | 覆盖 |
|---|---|---|
| 五资产 UTC MarketPanel、按币种滚动、数据指纹 | `alpha_lab/data/market_panel.py:26-100` → Cross-sectional Runner | `alpha_lab/tests/test_market_panel.py`、Runner/数据集测试 |
| 同期横截面因子计算与统一时间分割 | `alpha_lab/evaluation/cross_sectional.py` → `alpha_lab/cross_sectional_runner.py:156-177` → `alpha_lab/data/cross_sectional_dataset.py:32-53` | `test_cross_sectional_*` |
| 逐币种 next-open 研究代理标签及 label availability | `alpha_lab/labels/cross_sectional.py:18-45` | `alpha_lab/tests/test_cross_sectional_labels.py` |
| 横截面评估以 UTC section 为推断观测；记录资产数、覆盖、MBB/HAC、月度/年度、组合与换手 | `alpha_lab/evaluation/cross_sectional_evaluator.py:31-145` | `alpha_lab/tests/test_cross_sectional_evaluator.py` |
| 独立 35 场景 Monte Carlo 校准；比较 MBB/HAC/IID；旧方法诊断级 | `alpha_lab/calibration/cs_inference_calibration.py:36-66,175-261,264-349` | `alpha_lab/calibration/test_cs_inference_calibration.py`；结果见 `cs_inference_calibration.md` |
| strict PIT adapter、行级值/版本/时间绑定、content pin、PIT token 与修改检测 | `data_engineering/fundamentals.py:192-332`、`data_engineering/pit.py:84-143,146-274` | `tests/test_pit_evidence.py`、`tests/test_fundamentals.py` |
| strict backward as-of join、最大年龄、资产和指标 Registry 校验、未命中保留为空 | `data_engineering/fundamentals.py:335-407` | `tests/test_fundamental_asof.py` |
| 可将 strict PIT features 对齐成 Panel Series、覆盖率预检、不填零 | `data_engineering/alpha_lab_features.py:16-66`、`data_engineering/coverage.py` | `tests/test_fundamental_feature_bridge.py` |
| 固定动作、精确 request schema、日期预检、受限 panel ID、脱敏输出、候选 Python 不加载 | `alpha_lab/research_agent_cli.py:22-117` → 固定变换 Runner | `alpha_lab/tests/test_research_agent_cli.py`、`test_research_agent_permissions.py` |
| 实验身份、产物复用校验、独立 CS evidence append-only 存储 | `alpha_lab/cross_sectional_runner.py:91-209`、`alpha_lab/cross_sectional_store.py:17-117` | `test_cross_sectional_runner.py`、`test_experiment_registry.py` |

## 2. 确认缺陷与修复

### SUPPORTED 证据资格

`ResearchStore.create_decision()` 原先依赖模块级 `CROSS_SECTIONAL_SUPPORTED_ENABLED`。若该布尔值被打开，具有合法 run/candidate identity 的旧 diagnostic evidence 可写入 `SUPPORTED`，没有绑定校准方法和证据身份。

结论合同版本升为 `cs-verdict-gated-v2`，并增加默认空的 `(method_version, calibration_id)` 准入集合。正式 `SUPPORTED` 还必须提交当前 run 的 `evidence_id`，该 ID 与 append-only `cs_evaluations.evidence_hash` 相等，且存储 payload 重新计算摘要一致；证据还须 `inference_status=CALIBRATED`、feature coverage 为 `READY`、至少一个有效评价结果。Gateway `show` 返回 `cross_sectional_evidence_id`，便于受控决策引用。Runner 当前固定输出 `DIAGNOSTIC_ONLY`；旧 `cs-time-block-centered-v1` 没有 calibration_id，且没有任何准入项，因此不能晋升。生产布尔门禁仍关闭。

回归：旧方法在测试中模拟布尔开关为 true 仍被拒绝；模拟明确准入的测试 fixture 时，错误 evidence_id 拒绝、精确 ID 通过。fixture 白名单只在测试 monkeypatch，生产集合为空。

### PIT 同时间戳边界

`data_engineering/fundamentals.py` 原先使用 `allow_exact_matches=True`，但 sidecar 不声明发布时钟精度，也不证明事件在与决策同一时间戳上已被处理可用。strict as-of join 现改为只接受 `available_time < decision_time`，版本标识为 `pit-asof-strict-precedence-v1`，并写入 feature Series provenance。与决策时间相等的 VERIFIED 或 CONSERVATIVE 记录不会进入该时点；后续严格晚于决策时间的时点仍可匹配。

回归：verified 时间戳相等、conservative upper-bound 相等均不匹配；决策时点晚 1ms 后匹配。未改历史实验或旧统计结果。

## 3. 逐指标 PIT 清单

审计窗口为 `2022-01-01` 至 `2025-01-01`（结束日不含）。本机存在 14 个不可变 manifest、30 条指标序列；没有 `pit_attestations/` 或 `pit_evidence/` 文件。各 dataset manifest 的 `publication_unknown_rows` 覆盖该 snapshot 全部行，`research_safe=false`、`point_in_time=unverified`。表内 hash 是现有 snapshot SHA-256，不代表 PIT 证明。

| 数据集 | 来源 / 经济资产 | 指标（Registry scope） | Snapshot SHA-256 | 状态 / Strict | 缺失证据与可恢复性 |
|---|---|---|---|---|---|
| `coinmetrics_btc_dev_v1` | Coin Metrics / BTC | `AdrActCnt` (chain) | `adcc4234b494a469b37a808b513c38ffb0d6e290f50141a3f80f35441e4d7c87` | UNVERIFIED / no | 无历史发布时间、source vintage 与行级 revision/value 证明；是否可从上游归档恢复未证实 |
| `coinmetrics_btc_dev_v1` | Coin Metrics / BTC | `CapMVRVCur` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_btc_dev_v1` | Coin Metrics / BTC | `CapMrktCurUSD` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_btc_dev_v1` | Coin Metrics / BTC | `CapMrktEstUSD` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_btc_dev_v1` | Coin Metrics / BTC | `HashRate` (chain) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_btc_dev_v1` | Coin Metrics / BTC | `SplyCur` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_btc_dev_v1` | Coin Metrics / BTC | `TxCnt` (chain) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_eth_dev_v1` | Coin Metrics / ETH | `AdrActCnt` (chain) | `a2ef694146f8066a1d565bfef858a2560845555865987ee0b991c4cc0c10455c` | UNVERIFIED / no | 无历史发布时间、source vintage 与行级 revision/value 证明；是否可从上游归档恢复未证实 |
| `coinmetrics_eth_dev_v1` | Coin Metrics / ETH | `CapMVRVCur` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_eth_dev_v1` | Coin Metrics / ETH | `CapMrktCurUSD` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_eth_dev_v1` | Coin Metrics / ETH | `CapMrktEstUSD` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_eth_dev_v1` | Coin Metrics / ETH | `SplyCur` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_eth_dev_v1` | Coin Metrics / ETH | `TxCnt` (chain) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_sol_dev_v1` | Coin Metrics / SOL | `CapMrktEstUSD` (token) | `dea590fa5d8a48300614da491d163f88ca7dae3e26103742c3ac4b8137ec4d89` | UNVERIFIED / no | 无历史发布时间、source vintage 与行级 revision/value 证明；是否可从上游归档恢复未证实 |
| `coinmetrics_bnb_dev_v1` | Coin Metrics / BNB | `CapMrktEstUSD` (token) | `b5b05e9aeda0fd55af78642bb7e9ae5487976292665aa212e7f047094bbf3d1d` | UNVERIFIED / no | 无历史发布时间、source vintage 与行级 revision/value 证明；是否可从上游归档恢复未证实 |
| `coinmetrics_xrp_dev_v1` | Coin Metrics / XRP | `AdrActCnt` (chain) | `d79e0d0300bf72234561a454611f951896390fdae127308bf7bed8321fa8b60c` | UNVERIFIED / no | 无历史发布时间、source vintage 与行级 revision/value 证明；是否可从上游归档恢复未证实 |
| `coinmetrics_xrp_dev_v1` | Coin Metrics / XRP | `CapMVRVCur` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_xrp_dev_v1` | Coin Metrics / XRP | `CapMrktCurUSD` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_xrp_dev_v1` | Coin Metrics / XRP | `CapMrktEstUSD` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_xrp_dev_v1` | Coin Metrics / XRP | `SplyCur` (token) | 同上 | UNVERIFIED / no | 同上 |
| `coinmetrics_xrp_dev_v1` | Coin Metrics / XRP | `TxCnt` (chain) | 同上 | UNVERIFIED / no | 同上 |
| `bitcoin_chain_tvl_dev` | DefiLlama / Bitcoin ecosystem | `chain_tvl_usd` (ecosystem) | `9ddad1414482dc6ffe75eb71432234526142e0bde539cec5b84bbb7ff3e64c00` | UNVERIFIED / no | 无历史发布时刻、API vintage、行级 revision/value 与可证明 availability upper bound；历史版本可恢复性未证实 |
| `ethereum_chain_tvl_dev` | DefiLlama / Ethereum ecosystem | `chain_tvl_usd` (ecosystem) | `ef2a868a4229fad377f1024b60a970a22de80c34cf90a118b28fd3574bc0f304` | UNVERIFIED / no | 同上 |
| `solana_chain_tvl_dev` | DefiLlama / Solana ecosystem | `chain_tvl_usd` (ecosystem) | `0a7baed8082569a89b193352011956a9e2e46483e024d776a0929251f4e2ff77` | UNVERIFIED / no | 同上 |
| `bsc_chain_tvl_dev` | DefiLlama / BSC ecosystem | `chain_tvl_usd` (ecosystem) | `ba6b1db225f6df0bea1193677015e871f81c1acaa7eb0a7670d4a8392fcc2edb` | UNVERIFIED / no | 同上 |
| `xrpl_chain_tvl_dev` | DefiLlama / XRPL ecosystem | `chain_tvl_usd` (ecosystem) | `c5cef6f4718033dae35a1faee13afa2f94b0d961fc451837c38b8d4d892e3581` | UNVERIFIED / no | 同上 |
| `defillama_bsc_dailyfees_dev_v1` | DefiLlama / BSC ecosystem | `ecosystem_daily_fees_usd` (ecosystem) | `c65955c90b3fcf10d680df46a7ce4c3f44365a50e9b8f6cbef912ccc7dbd8953` | UNVERIFIED / no | 无历史发布时刻、API vintage、行级 revision/value 与可证明 availability upper bound；历史版本可恢复性未证实 |
| `defillama_bsc_dailyrevenue_dev_v1` | DefiLlama / BSC ecosystem | `ecosystem_daily_revenue_usd` (ecosystem) | `cbff7c10e5a695f7aa5ab8309c8bfec7c299b16878a312c79597a19b28b6ccae` | UNVERIFIED / no | 同上 |
| `defillama_solana_dailyfees_dev_v1` | DefiLlama / Solana ecosystem | `ecosystem_daily_fees_usd` (ecosystem) | `bb98770e5299225f102d387c933bbde2245f724c83b361ebabae2cd734cb5ca3` | UNVERIFIED / no | 同上 |
| `defillama_solana_dailyrevenue_dev_v1` | DefiLlama / Solana ecosystem | `ecosystem_daily_revenue_usd` (ecosystem) | `c9df04f05d4bbd5f83f1dde790e89f92c9883d7f70825877e40701b80ddfddde` | UNVERIFIED / no | 同上 |

### 证据缺口与恢复判定

- 所有 30 条序列：sidecar 行级 release time/upper bound、历史修订 ID、能证明 snapshot 中具体值的原始历史版本均缺失；`event_time`、当前 snapshot `published_at`、固定延迟均不能替代。
- Coin Metrics：要补证需提供逐观测 historical release/vintage 或可审计归档，明确 source asset/metric/event/value/revision/available time；当前代码和本机 catalog 未证明这些历史版本可恢复。
- DefiLlama：要补证需提供逐观测历史 API vintage/发布记录或经过审计的可用时间上界，并绑定实际 TVL/fees/revenue 值与 revision；当前代码和本机 catalog 未证明这些历史版本可恢复。
- “可能可向供应方询问/从自有历史归档恢复”只是后续取证路径，不等于可恢复性已成立。取得证据后仍须人工审计来源并通过 PIT v2 校验。

## 4. 统计校准根因与决定

生产 evaluator 把每个 UTC section 的 CS-RankIC 作为时间序列观测；没有把同一时刻各币种误当独立时间样本。MBB 的 p 值从 section-mean moving-block 重采样并对均值零假设中心化；HAC 计算 Bartlett long-run variance，但只输出 SE/t，不是已准入的正式 p 值合同。校准模块的零效应生成中 factor 与 returns 独立，并覆盖因子/标签时间相关、共同冲击、异方差和重叠 horizon。

先前固定校准矩阵 35 场景 × 5,000 rep，MBB bootstrap 399 rep。MBB block 4/12/32、HAC lag 4/12/32、IID t 均未通过预设 Type-I 与 coverage 验收；特别是高持久性和联合压力场景造成膨胀。当前证据支持“现有方法不适合正式 SUPPORTED”，不支持通过改阈值或挑 block length 修复。保留 `cs-time-block-centered-v1` diagnostic-only，不改公式和历史解释。新推断候选需在独立 calibration design / holdout simulation 上先通过，再更换 `method_version` 并登记 calibration identity。

## 5. 版本化基本面 Runner / Gateway 接口

已增加 `alpha_lab/cross_sectional_features.py`，Runner 现在经 `build_features()` 消费声明式特征。旧市场配置保持兼容；新格式要求 `feature_schema_version=alpha-cs-feature-schema-v1`。市场特征只能是注册变换；基本面格式为：

```json
{
  "feature_schema_version": "alpha-cs-feature-schema-v1",
  "features": [{
    "kind": "fundamental",
    "metric": "CapMrktEstUSD",
    "max_age": "48h",
    "snapshot_pins": [{"dataset_id": "<actual-dataset-id>", "sha256": "<actual-64-hex-catalog-digest>"}]
  }]
}
```

上面的尖括号值是说明性占位符，不是可运行 pin。`run_cross_sectional()` 默认只使用可信默认 Catalog；测试注入 adapter 是 Maintainer 内部接口，Agent 不能通过配置选择文件系统路径。严格读取窗口会按最大 `max_age` 向前扩展 warm-up，但下界不会越过 `2022-01-01`。不支持 exploratory access。PIT attestation、snapshot pin、Registry versions、特征值指纹、实现/评价代码 hash、label version、complete-case missingness policy 与完整 config 都纳入 run identity；复用仍验证产物 hash。`preflight_feature_coverage()` 写入 summary，报告特征覆盖率与每时点有效资产数，不填充缺失值；SUPPORT eligibility 要求 coverage `READY`。当前 inference status 固定为 `DIAGNOSTIC_ONLY`。

Runner 的正式固定 action `cross_sectional_run` 可消费该配置，不增加任意 Python 执行入口。当前 14 个真实基本面 datasets 均无 PIT sidecar/evidence，严格读取全部失败；新增入口只在未来 snapshot 通过 strict PIT v2 后才可形成有效输入。合成 attested Catalog 测试仅验证代码链路，不代表现实来源已取得证明。

## 6. 决策与权限

- `SUPPORTED` 资格现要求精确匹配的 `(method_version, calibration_id)` 准入，以及存储层重新验证的 `evidence_id`。生产准入集合仍为空，门禁仍关闭。
- `REJECTED` 仍表达研究决策，不被解释为已校准的正式统计拒绝；当前跨截面结果均标 diagnostic，Evidence 与 Decision 仍分离。
- Research Agent 只能经固定 Gateway 操作；候选 Python 不被加载。Gateway CLI 权限测试覆盖 request schema、held-out 日期拒绝、脱敏与不执行候选文件。OpenCode 配置不等于 OS sandbox；该已知边界不作为本轮新发现。

## 7. 冻结准入

**RC_BLOCKED / Agent BLOCKED。** 统计校准仍失败，14 个基本面数据集仍无严格 PIT 证据；五资产 smoke 只是 diagnostic。版本化接口和证据资格门禁已实现，但没有真实 PIT 数据可跑基本面 Runner。未更新 v1.1 freeze manifest，也未修改 v1.0 manifest、SQLite、快照或 2025+ 行情数据。
