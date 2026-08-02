---
name: active-alpha-paper-monitor
description: 主动 Alpha 发现、历史验证、paper trading 与事件监控 skill。用于在统一 EvidenceSnapshotV2 上扫描 crypto 与美股候选、运行 walk-forward/paper 复盘并生成 V3 handoff；不负责最终组合仲裁，不自动真实下单。
---

# Active Alpha Paper Monitor V3

这是机会发现与 paper 验证层。它把可复核证据交给 Manual V3，不替用户作真实交易。

## 硬性边界

- 只使用公开或授权只读数据、paper、dry-run 或 testnet。
- 禁止真实下单、撤单、提现、margin、futures、perpetual、跨账户转账或暴露 API key。
- 社交、叙事、funding/OI、trending 和单条新闻只能排序或降级候选，不能单独授权真钱动作。
- Handoff 的 `watch / paper_only / risk_alert` 仅是内部研究标签，不得原样暴露为用户正式动作；Manual 必须重新仲裁为 `ENTER_NOW / WAIT_FOR_ENTRY / NO_TRADE`。
- `live_orders_enabled=false`、`private_api_used=false`、`human_confirmation_required=true` 是不可覆盖的不变量。

## 请求路由

| 任务 | 必读引用 |
|---|---|
| 通用主动扫描与 handoff | `references/V3_ACTIVE_HANDOFF_CONTRACT.md`、`references/ACTIVE_ALPHA_SIGNAL_POLICY.md`、`references/HANDOFF_PROTOCOL.md` |
| Crypto 异动/新链机会 | `references/V3_ACTIVE_HANDOFF_CONTRACT.md`、`references/IMPULSE_CAPTURE_ENGINE_POLICY.md`、`references/DYNAMIC_SCAN_POOL_POLICY.md`、`references/NEW_CHAIN_EVENT_TO_ASSET_DISCOVERY_POLICY.md` |
| 美国 crypto 法案、监管投票或周末政策催化 | `references/V3_ACTIVE_HANDOFF_CONTRACT.md`、`references/US_CRYPTO_LEGISLATIVE_EVENT_RADAR_POLICY.md`、`references/DYNAMIC_SCAN_POOL_POLICY.md` |
| 美股开盘/财报候选 | `references/V3_ACTIVE_HANDOFF_CONTRACT.md`、`references/US_OPEN_DYNAMIC_SCANNER_POLICY.md`、`references/RESEARCH_COMMITTEE_POLICY.md` |
| Paper/验证复盘 | `references/V3_ACTIVE_HANDOFF_CONTRACT.md`、`references/PAPER_MONITOR_WORKFLOW.md`、`references/VALIDATION_PROGRESS_RUNNER.md`、`references/DUAL_SAMPLE_AND_FORWARD_VALIDATION_POLICY.md` |
| Sharpe/Sortino、风险调整路径或价格修复排序 | `references/V3_ACTIVE_HANDOFF_CONTRACT.md`、`references/RISK_ADJUSTED_PATH_QUALITY_POLICY.md` |

不要读取全部历史版本说明。需要专项细节时，只读取一个直接相关引用。

## 标准流程

1. 先按 `references/DUAL_SAMPLE_AND_FORWARD_VALIDATION_POLICY.md` 更新全部未关闭 observation 与 Paper trade，再扫描新候选。`scripts/tactical_observation_reviewer.py` 负责把已到 `review_due_at` 的 1–7 日观察追加为 `ObservationOutcomeReviewV1`；旧样本缺少冻结阈值时只能输出 `PATH_ONLY`，不得用当前配置倒填历史。
2. 接收或生成一个 EvidenceSnapshotV2；同一轮所有研究角色必须共享 `snapshot_id` 和截止时间。
3. 生成动态扫描池，保留流动性、成交额、事件、历史状态和数据新鲜度证据。
   新链/mainnet/钱包或 launchpad 支持事件必须先运行
   `scripts/new_chain_opportunity_radar.py`；DEX-only 资产进入独立
   `cross_venue_watch_candidates`，不得因没有 CEX 交易对而消失。
   美国 crypto 法案或监管催化必须运行
   `scripts/us_crypto_legislative_event_radar.py`，区分院别、委员会、正式
   floor schedule 与二级市场传闻；周末必须前看 72 小时。
4. 对候选运行当前信号、无前视历史验证、摩擦压力和微观结构检查。Discovery Top3 必须使用同一历史口径生成可比较摘要：样本数、样本外胜率区间、保守 EV、预期回报、Profit Factor、回撤、流动性和稳定性；不得只给 discovery score。
   需要评估 Sharpe 时，调用 `scripts/risk_adjusted_path_quality.py`，
   保留基础排名与调整后排名；不得把路径分数映射成概率或公允价值。
5. 运行 paper 风险、容量和 recovery gate；失败时保留 blocked candidate 和明确原因。
6. 每个冻结候选写 ObservationSampleV1；只有可复现的 Paper fill 写 TradeSampleV1。
7. 输出统一 handoff：候选事实、setup、概率类型、验证状态、风险、缺口和 observation plan；walk-forward 与 Paper 统一封装为 `RegressionEvidenceV1`。
8. Manual V3 先用 `tactical_research_handoff.py` 读取当前 Top3/历史比较并生成唯一
   `TacticalResearchRequestV1`。预研究回归门失败时停止昂贵深研；通过时才收集四角色
   dossier，并在慢速研究后重新运行本 scanner 生成最终 fresh snapshot。最终 Top1
   改变时旧 dossier 不得转移到新标的。Manual 再读取持仓、现金、长期目标和风险门，
   形成研究/执行双轨结论。
9. 到期观察复盘记录 MFE、MAE、诊断目标/止损先后和拒绝质量；到期 Paper 复盘另行记录真实模拟成交、费用、滑点和事件跳空。观察结果不得进入 Paper 或真钱收益分母。

## EvidenceSnapshotV2

- 数字字段必须包含 `evidence_id / value / unit / as_of / source / freshness_status`。
- 各研究角色不得静默补充不同时间的价格；新增证据必须先合并进同一个 snapshot。
- 来源冲突必须保留双方值、差异和采用规则。
- 缺失社交、链上或单一交易所数据只降级相关维度，不得把完整候选池错误清空。
- 旧缓存超过策略 freshness 上限时只能作为历史证据，不是当前信号。
- Binance 公共行情请求使用受控并发、连续失败熔断和健康镜像优先；每个端点的成功、失败、跳过与熔断状态必须进入审计。局部端点超时不得清空仍可验证的候选，全部端点失败才允许输出无新鲜决策。
- 动态现货身份以 Binance `/api/v3/exchangeInfo` 为权威。批量 `symbols` 请求被无效、历史或不符合参数语法的交易对污染时，必须在同一轮改用同一端点的全量快照并本地过滤；不得把 ticker 存在直接当作现货证明。全量快照仍必须执行 active/spot/permission/leveraged 以及证券、稳定币和商品身份门，且把 batch/fallback/恢复数量写入审计。
- 新链 live radar 必须在访问 DEX 来源前比较当前 cutoff 与带时区的 `watch_until`。过期、缺失或不可解析的事件只保留审计，候选为空，禁止作为当前催化或输出 `why_now`；不得改写历史 replay。

## Handoff 合约

每个候选只能属于一个模式：`tactical_1_7d` 或 `event_trade_1_3w`。Active 不生成长期 DCA recommendation。

必须输出：

- `snapshot_id`
- `candidate_id / symbol / asset_class / request_mode`
- `setup_id / probability_event / probability_type / sample_size`
- `current_signal / decision_price / price_as_of`
- `entry_observation / targets / stop / time_stop / event_plan`
- `liquidity / derivatives_or_options / financing_dilution / historical_conditioning`
- `validation_status / paper_status / blockers`
- `rank / historical_comparison_summary / why_ranked_below_primary`（进入用户备选集时必填）
- `max_active_action`
- `RegressionEvidenceV1`，且与候选共同绑定 `snapshot_id / strategy_version / config_digest / source_digest`

判断概率与 Bull/Base/Bear 情景概率必须分开；小样本不得宣称 calibrated 或 80% 高胜率。Active 只提供 Top3 的同口径历史比较，Manual 决定一个主推荐和最多两个高质量备选；不得把 Top3 同时解释为三个执行订单。

`RegressionEvidenceV1.mode` 只允许 `historical_walkforward / forward_paper`，并强制 `formal_action_eligible=false` 与 `paper_live_separated=true`。Active 不生成 `LiveInvestmentDecisionV1` 或 `InvestmentOutcomeStatusV2`，也不判断用户真钱动作。

## Paper 与学习

- Paper ledger 是模拟证据，不是当前账户购买力。
- Observation 与 Trade 使用独立分母；未触发、被淘汰或被替代的候选不进入交易胜率和收益。
- 每个 paper 交易必须记录策略版本、信号时间、下一根可成交价格、费用、滑点和保守 stop-first 规则。
- `n<10` 为 judgment-only；`10–29` 只输出宽区间；至少 30 个无前视同类样本后才可标记 calibrated。
- 明确代码/schema/safety 缺陷可直接修复；分析规则需三次独立复现或回测证据后进入 proposed change。
- Proposed change 只影响 paper；Manual 人工确认前不得进入真钱策略。

## 运行产物边界

Skill 源码只包含 `SKILL.md`、`references/`、`scripts/` 和 `config/`。

以下属于 runtime，不得同步进安装 skill 包：

- `reports/`
- `experiments/`
- `handoffs/`
- `cache/`
- `paper_trades/`
- `subagent_outputs/`
- `subagent_tasks/`
- 本地 HTML、图片卡和临时 shadow 数据

Runtime 产物应保存在工作区或显式 `INVESTING_RUNTIME_ROOT`，迁移时先生成文件清单和 SHA-256，不直接删除历史。

## 主要入口

- 多源扫描：`scripts/run_multisource_alpha_snapshot.py`
- Crypto paper runner：`scripts/validation_progress_runner.py`
- 当前信号：`scripts/current_signal_probe.py`
- 异动扫描：`scripts/impulse_capture_scanner.py`
- 新链事件到资产：`scripts/new_chain_opportunity_radar.py`
- 美国 crypto 立法事件：`scripts/us_crypto_legislative_event_radar.py`
- 美股开盘扫描：`scripts/us_open_dynamic_scanner.py`
- 样本审计：`scripts/validation_sample_auditor.py`
- 风险和完整性：`scripts/paper_testnet_risk_control_auditor.py`、`scripts/paper_ledger_integrity_auditor.py`
- 1–7 日双样本账本：`scripts/tactical_evidence_ledger.py`；观察与 Paper TradeSample 使用独立追加式 JSONL，均不得进入真钱 ROI。
- 观察结果复盘：`scripts/tactical_observation_reviewer.py`；只读取冻结观察和闭合 15m 公共行情，追加 `ObservationOutcomeReviewV1`。同柱目标/止损按 stop-first；数据失败保留可重试 `DATA_BLOCKED`，不写入伪结果。

这些入口不得依赖 Unified 中不存在的旧 scripts；共享契约由 Manual V3 提供。

## 修改与发布门

修改后必须通过：

1. Active 相关 self-test 和 Manual 的共享合同测试。
2. 三套 skill 的 `quick_validate.py`。
3. 完整 source manifest 哈希比对。
4. 负向 smoke 与正式 smoke。

任一内层 freshness、handoff、calendar、history、risk 或 safety gate 失败时，不得宣称链路通过。
