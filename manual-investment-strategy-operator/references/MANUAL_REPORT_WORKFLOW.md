# Manual Report Workflow

## 报告类型

| 类型 | 何时使用 | 目标 |
|---|---|---|
| Daily Manual Report | 用户主动要求今日报告 | 持仓、短期操作、长期DCA |
| Weekly Strategy Report | 每周手动复盘 | 组合比例、现金、质押、候选和行动 |
| Monthly Review | 每月 | 预测复盘、错误归因、策略迭代 |
| Event Review | monitor 或用户触发 | 单事件风险和行动草案 |
| Single Best Candidate | 用户只问现在最值得交易的一个标的 | 跨美股/crypto 比较后，只输出一个清晰交易卡 |

## 请求路由优先级

- 用户只问一个当前机会时，优先读取并执行 `SINGLE_BEST_CANDIDATE_DEFAULT_POLICY.md`，不要套用下面 26 个面板的完整可读结构。
- 内部仍执行与该候选有关的实时行情、消息、宏观、资产专项、资金和下行风险检查；最终正文只展示能够改变决策的证据。
- 用户明确要求“完整组合报告、今日总览、周报、月报、全部候选或持仓复盘”时，才使用完整固定结构。
- 单一候选模式必须在第一屏回答：标的、当前动作、入场价和截止时间、止损、两档目标、预计持有期、三种走势及概率来源。

## 推荐调度入口

默认优先使用一键手动调度脚本：

```bash
python3 manual-investment-strategy-operator/scripts/manual_dispatch_run.py \
  --require-fresh-market-intelligence
```

该入口会编排：

1. `generate_manual_report.py` 生成 fresh 市场/情绪/目标导向报告。
2. 报告完整性审计。
3. 目标覆盖审计。
4. `goal_system_completion_audit.py` 系统完成度审计。
5. `manual_dispatch_summary_*.json/md` 调度摘要。

它仍然是被动执行工具：不会后台轮询、不会下单、不会转账、不会修改 `portfolio_ledger.json`。如果只想验证脚本链路而不写 recommendation history，可使用：

```bash
python3 manual-investment-strategy-operator/scripts/manual_dispatch_run.py --smoke
```

## 运行时长与收口

每次手动报告必须遵守 `PASSIVE_DISPATCH_RUNTIME_POLICY.md`：

- 用户触发一次，系统运行一次完整闭环：取数、情绪、全盘分析、报告、记录、审计、复盘队列。
- `主动获取全盘数据` 只表示本次调度内尽量全面拿数据，不表示后台自动盯盘。
- 如果 subagent、社交情报或 API 没有按时返回，报告必须标记 `degraded`，然后继续生成可读结论；不能为了等齐所有研究无限运行。
- 如果 readiness/queue 输出 `stop_and_report_status`，本轮必须停止并汇报：已经生成什么、还缺什么、下一次应由什么触发。
- 收口不等于目标完成。它只表示本轮机制已推进到可复盘状态，真实 5年/10年 10x 目标仍要靠后续结果证明。

## 固定结构

完整组合报告必须先输出一页结论和持仓总览对比，再输出旧结论挑战、Research Committee Panel、持仓状态和 DCA 交易对推荐。默认结构：

1. 一页结论：今日状态、主判断、最大允许动作和 1-3 条最重要操作。
2. 持仓总览对比：Current / 1D / 7D / 30D。
3. Fresh Market Intelligence Panel：本次手动调度主动刷新了哪些市场、情绪、宏观、链上、新闻和 active handoff 数据；哪些来源失败；失败如何降级动作。
4. Full-Market Deep Analysis Panel：把全盘数据映射到月度战术收益目标、5-10 年长期目标、现金通道和最大允许动作。
5. Cost Basis Reconciliation Panel：完整均价、部分已知 lot、未知历史 lot、已确认成本金额和禁止完整 PnL 声明的阻断项。
6. Portfolio Evidence Gap Panel：lcETH 成本/赎回、ADA/SOL 残缺 lot、US cash rail、crypto cash floor 的缺口、导入字段和阻断影响。
7. Goal Gap Panel：5年/10年 10x 缺口、资产贡献标签和目标对齐度。
8. Goal Execution Dashboard：5年/10年 10x、DCA、美股战术、验证缺口、渐进学习状态、paper 复盘日历和 backlog 总控台。
9. Asset Goal Contribution Panel：每个主要资产的目标贡献、质押复利和达到 10x 仍需价格倍数。
10. Prior Thesis Challenge Panel：上一轮结论、支持证据、反证、失效条件和旧 thesis 是否可沿用。
11. Research Committee Panel：subagent 研究覆盖、分歧、bull/base/bear case、缺失数据、arbiter 决策和最大允许动作。
11. 长期趋势矩阵：本次相关资产的长期 thesis、持仓目标贡献和趋势状态。
12. Macro Regime / Fund Flow Panel：利率、美元、美债、通胀、风险偏好和资金流决定 DCA 节奏。
13. Crypto Key Person Intelligence Panel：关键人物/官方账号社交情报、验证状态、资产影响和最大允许动作。
14. Asset Micro Thesis Matrix：本次相关资产的价格位置、链上/生态、供应、流动性、资金流和风险。
15. Staking Compounding Model：5年/10年复利倍数和达到 10x 仍需价格倍数。
16. Technical Execution Window：1D/5D/20D/60D、支撑阻力、量能、价差和最多两档入场/出场。
17. Missing Data / Downgrade Panel：缺失数据对建议的影响。
18. 本期 DCA 交易对推荐：目标函数、数据质量、动态权重和分批计划。
19. Unified Candidate Deep Dive：所有 DCA、长期、短线、持有/减仓候选卡片。
20. 美股 High-Return Focus Gate / Dynamic Alpha Discovery。
21. US Tactical Performance Panel：当前战术资金池相对月度/季度 50%+ 目标的进度、缺口和现金拖累。
22. Tactical Rotation Relay Panel：当前战术仓与现金接力状态。
23. 最终操作清单：短期走势、长期 DCA、现金通道、风险与下次复盘。
24. Recommendation History Panel：本次建议ID、未复盘建议、到期结果、错误归因和 proposed changes。
25. Progressive Learning Confidence Panel：当前学习阶段、60%-79% 学习样本、80%+ 强候选样本、resolved 缺口和最大学习动作。
26. 术语小注：解释本报告中出现的硬术语，并说明它们对本组合行动的含义。

## 可读性与术语注释

报告必须执行 `Readability / Terms Gate`：

- 第一次出现专业术语时，用一句中文短备注解释，例如 `walk-forward（用过去一段训练、后面一段验证，防止只适合历史）`。
- 同一个词后续可以直接使用，但最终报告末尾要有 `术语小注`。
- 行动建议必须用直白中文写“买什么、等什么价、卖什么、什么时候复盘”，不能只写模型词。
- 每个复杂面板后要补一句 `这对你意味着什么`，把数据翻译成持仓、DCA、等待或不动。
- `forecast_probability_pct` 是目标时间内达到目标价的真实预测概率；`execution_readiness_score` 是执行准备度分数，不是上涨概率。
- `paper_only` 要解释为只做模拟不动真钱；`no_deploy` 要解释为不投入真实资金；`conditional_action` 要解释为等触发后再人工确认。

常用硬术语的解释以 `references/REPORT_READABILITY_AND_TERMS_POLICY.md` 为准。

每段都要拆分：

- Crypto
- 美股
- 现金通道

目前持仓情况必须优先说明：

- 总资产估值口径。
- crypto / 美股 / 现金通道比例。
- 核心仓、卫星仓、战术仓分别是什么。
- ETH/lcETH、ADA、SOL 等质押状态和流动性。
- 超配/低配资产。
- 数据质量和阻断项。

持仓总览对比必须优先说明：

- 当前总资产估值。
- 与昨日、7日、30日对比的总资产变化。
- crypto / 美股 / 现金 / 质押资产的变化。
- 主要正负贡献标的。
- 数据口径：API proxy、历史价格重建、持仓数量是否假设不变。

Cost Basis Reconciliation Panel 必须说明：

- 若使用自动化输入，运行 `scripts/cost_basis_reconciliation_audit.py` 或让 `scripts/build_daily_report_context.py` 自动生成 `cost_basis_reconciliation_panel`。
- 完整可用均价的持仓、只有部分已知 lot 的持仓、重大成本缺失的持仓分别是什么。
- 已确认 lot 成本金额、已知数量占比、未知历史 lot 和下一步需要导入的 broker/exchange 记录。
- 若状态为 `partial_cost_aware_only`，报告只能使用完整成本持仓和已确认 lot 做局部收益/仓位判断，不得宣称 lcETH/ADA/SOL 的完整成本收益率。
- 缺失成本不得阻止价格/市值/权重判断，但会把完整 PnL、税务口径和以成本为基础的换仓建议降级为 `conditional only`。

Portfolio Evidence Gap Panel 必须说明：

- 若使用自动化输入，运行 `scripts/portfolio_evidence_gap_packager.py` 或让 `scripts/build_daily_report_context.py` 自动生成 `portfolio_evidence_gap_panel`。
- 当前证据缺口的 `gap_id`、优先级、资产/现金通道、缺失字段、接受的导入来源和最大允许动作。
- lcETH 必须列出成本 lot、receipt conversion ratio、redeemable underlying、费用和 unstake delay 是否缺失。
- US equity rail 必须区分 user-stated cash 与 broker-verified settled cash/buying power；未验证时只能做 conditional/watch sizing。
- Crypto rail 必须区分已到账稳定币、已成交/未成交限价单和未来计划 DCA；未确认到账不得当作可用现金。
- 该面板只生成导入模板和重跑审计命令，不自动修改账本。

Fresh Market Intelligence Panel 必须说明：

- 每次用户手动调度报告时，先按 `references/MANUAL_DISPATCH_MARKET_SENTIMENT_POLICY.md` 主动刷新数据，不得只沿用上一份报告结论。
- 本次覆盖了哪些资产、现金通道、crypto 市场、美股市场、宏观、链上/DeFi、关键人物/官方消息、新闻和 active handoff。
- 数据源必须列出 `attempted / succeeded / failed`，并带时间戳；失败源要写明是否影响 DCA、战术仓或长期 thesis。
- 情绪数据必须拆开：市场情绪、官方/关键人物消息、社交热度、价格/成交量反应。单源社交或传闻只能进入 `watch`。
- 如果无法联网、API 限流或关键数据缺失，报告要标记 `market_intelligence_degraded`；此时不得输出新的 `execute_now`，只能输出持有、等待、conditional、paper 或 no_deploy。
- 每个复杂结论后写一句 `这对你意味着什么`，把取数结果翻译为“现在买/等触发/不动/降低仓位/只复盘”。

Full-Market Deep Analysis Panel 必须说明：

- 数据刷新后形成的核心判断，而不是只列价格或新闻。
- 月度收益目标：美股/战术资金池当前应该继续持有、换仓、等待回踩、等待突破，还是不部署。
- 长期收益目标：crypto/DCA 当前应该加速、正常分批、等待回调，还是暂停新增。
- 现金通道：crypto rail 与 US equity rail 分别能用多少、是否已确认、是否应该保留 1-2 个交易日等待更优机会。
- 目标一致性：每个主建议必须说明它如何服务月度收益或 5-10 年 10x；不能解释目标贡献的标的只能 `watch`。
- 最大允许动作：在 `execute_now / conditional_action / watch / no_deploy / hold` 中明确一个上限，并解释被哪些数据或证据限制。
- `这对你意味着什么`：用一句话把全盘结论翻译成“现在该做什么/等什么/不做什么”。

本期 DCA 交易对推荐必须说明：

- `primary_pair`：第一推荐交易对。
- `secondary_pair`：第二推荐交易对。
- `satellite_pair`：长期高凸性或机会候选，如适用。
- `avoid_pairs`：本期不推荐或暂停的交易对。
- 买入金额、动态权重、分批计划、立即执行金额、触发价、失效条件。
- 推荐变化是否提高 5年/10年 10x 目标达成概率。
- Goal Gap Panel 的结论如何影响权重：哪些资产是 `accelerator / neutral / drag / tail_convexity`。
- Macro Regime Panel 给出的 DCA 节奏：`accelerate / normal / split_more / wait_for_pullback`。
- Crypto Key Person Intelligence Panel 中的社交情报是否只影响 watch、conditional、risk alert 或 DCA 节奏，不得单独扩大仓位。
- Asset Micro Thesis Matrix 和 Staking Compounding Model 如何改变金额、分批和触发价。
- Missing Data / Downgrade Panel 中每个缺口对应 `no impact / smaller size / conditional only / block`。
- 数据来源、时间戳、质量状态和任何降级原因。
- 每个 DCA 候选必须输出候选卡片，包括分批入场区间、入场截止时间、目标情景、复盘窗口、thesis 置信度、失效条件和下次复盘日期。
- 报告只比较本次真正影响推荐、观察、减仓或风险判断的标的；不得为了固定格式硬性输出无关代币长表。

Asset Goal Contribution Panel 必须说明：

- 若使用自动化输入，运行 `scripts/asset_goal_contribution.py` 或让 `scripts/build_daily_report_context.py` 自动生成 `asset_goal_contribution_panel`。
- 每个主要资产必须输出：当前权重、crypto rail 权重、`accelerator / neutral / drag / tail_convexity` 标签、质押 APY、5年/10年复利倍数、5年/10年达到 10x 仍需价格倍数。
- ETH/lcETH 超配时必须标记为 `drag` 或 `reduce_new_dca`；SOXL 必须标记为长期 DCA 排除；NIGHT 必须标记为尾部高凸性而非稳定复利仓；BTC 默认是机会型流动性锚，不是新增主线。
- DCA 建议必须引用该面板的 `dca_weight_implication` 和 `concentration_flags`，不能只凭价格低或上一轮结论决定。

Macro Regime Panel 必须说明：

- 宏观数据、来源、时间戳、数据质量、DCA 节奏和不作为单币强买入依据的限制。
- 若使用自动化输入，先运行 `scripts/macro_regime_snapshot.py` 生成 JSON，再通过 `scripts/build_daily_report_context.py --macro-regime-json` 接入报告上下文。
- 自动化宏观输入至少尝试覆盖：SPY/QQQ/SOXX/SMH、VIX、DXY、BTC/ETH、美国财政部 10Y/2Y 曲线、BLS CPI、可选 Alpha Vantage Fed funds / 10Y。
- 若 PCE、FedWatch 或数字资产 ETF/fund flows 缺失，必须在 Missing Data / Downgrade Panel 标记；这些缺口会影响 DCA 节奏和战术仓 sizing，但不能单独触发买入或卖出。

Prior Thesis Challenge Panel 必须说明：

- 上一轮主要结论和推荐，例如 SOL/ADA DCA、SOXL 接力、NIGHT 持有或不新增 BTC/ETH。
- 本轮新数据中支持旧结论的证据。
- 本轮新数据中反驳旧结论的证据。
- 旧结论的原始失效条件是否触发。
- `prior_thesis_status`: `confirmed / weakened / invalidated / insufficient_data`。
- `old_thesis_reuse_allowed`: true/false。若为 false，旧结论不能出现在主行动清单，只能进入复盘说明。

Research Committee Panel 必须说明：

- 本次成功返回的 subagent 角色和缺失角色。
- 每个 subagent 的 `recommended_max_action`、`confidence_pct`、`data_quality`、关键证据、缺失数据和 `what_would_change_my_mind`。
- `researcher_votes`、`bull_case`、`base_case`、`bear_case`、`disconfirming_evidence`。
- arbiter 决策必须显式应用：长期目标优先、组合状态优先、数据质量否决、任一关键 block 禁止新增、社交/新闻不能单独触发实盘建议。
- 若 subagent 工具不可用或少于 6 个相关角色成功返回，报告必须标记 `research_committee_degraded`，不得输出新的 `execute_now`。
- 当前运行环境提供 subagent 工具时，必须按 `references/SUBAGENT_ORCHESTRATION_RUNBOOK.md` 先并行调用真实 subagent，并把结果通过 `--external-agent-outputs-json` 交给 `scripts/research_panel_runner.py` 校验；本地 fallback 只能作为降级保底。
- 若 `scripts/research_committee_quality_auditor.py` 质量审计失败，必须运行 `scripts/research_evidence_backlog_builder.py`，并在报告或附录中列出 evidence backlog 摘要：哪些角色证据不足、下一轮要补哪些来源、补完以后仍有哪些 action readiness 阻断。
- 若下一步需要真实 subagent 补采，必须运行 `scripts/research_subagent_task_packager.py` 生成每个角色的 `.task.json` 与 `.prompt.md`，再用 `scripts/research_subagent_output_collector.py` 收集 per-role 输出、列出缺席角色，并把收集后的外部 JSON 交回质量审计和 `research_panel_runner.py`。
- 报告必须运行 `execute_now positive path` readiness：当前证据不足时保持 `execute_now_allowed=false`；未来只有 Research Committee verified、Strategy Promotion passed、候选双 80、无阻断缺失数据、live order disabled 且人工确认草案齐备时，才允许把 `execute_now_allowed` 置为 true。
- 报告必须运行 `Progressive Learning Confidence Gate`：`60%-79%` 候选不是失败，而是学习样本；它们可以进入 watch、paper、conditional 或 small_probe_review，但必须写入 recommendation history，并在后续复盘中校准。只有 `>=80%` 且执行准备度、数据质量、策略晋级和人工确认均通过，才可能进入 execute_now candidate。

Recommendation History Panel 必须说明：

- 本次报告新增的每个 `recommendation_id`、对应 `action_id`、资产、通道、动作、时间窗口和最大允许动作。
- 历史未复盘建议中哪些已经到期，必须标记为 `hit / failed / not_triggered / expired / invalidated`。
- 每条失败或偏差的错误归因，例如 `timing_error`、`regime_error`、`data_error`、`onchain_error`、`expected_value_error` 或 `research_committee_error`。
- 本次是否产生 `proposed_changes`；若有，必须保持 `approval_status=pending`，等待人工确认。
- 若 `recommendations/recommendation_history.json` 无法读取或写入，报告必须标记 `recommendation_history_degraded`，不得输出新的 `execute_now`。

Progressive Learning Confidence Panel 必须说明：

- 当前学习阶段：`cold_start / early_calibration / usable_calibration / validated_ramp`。
- `60%-79%` 学习型建议的总数和已复盘数。
- `80%+` 强候选的总数和已复盘数。
- 距离下一阶段还需要多少 resolved 样本。
- 当前最大学习动作；低于 80% 的建议可学习、可复盘，但不得包装成高把握执行。

Unified Candidate Deep Dive 必须说明：

- 所有候选先进入 `candidate_card`，再进入行动清单。
- 覆盖范围包括 `us_tactical`、`crypto_tactical`、`dca`、`long_term`、`hold_trim`。
- 每张候选卡片必须写清：交易通道、入场区间、入场截止时间、目标价或目标情景、目标时间窗口、真实目标达成概率、执行准备度、概率依据、止损/失效、止盈/复盘、最晚退出/复盘日、仓位计划和数据质量。
- 美股和 crypto 短线候选若未同时达到真实概率 >=80% 与执行准备度 >=80，不得输出 `execute_now`；`60%-79%` 可以作为学习型 `watch / paper_only / conditional_action / small_probe_review`，低于 60% 默认 `watch / no_deploy`。
- 长期/DCA 候选不强制 80% 短线概率，但必须给出 `thesis_confidence_pct`、长期 thesis score、1年/3年/5年情景、DCA 批次、数据质量和 thesis 破坏条件。

Pre-Entry Downside and Capital Risk Panel 必须说明：

- 该面板只覆盖美股战术候选和已有战术仓的新增/继续持有审查，必须在收益目标和 Top 候选之前出现。
- 输出目标先于止损概率、止损先于目标概率、5/10/20日最大不利波动、隔夜或事件跳空压力、风险回报和按压力损失计算的仓位。
- 固定检查未来十个交易日的财报、FOMC、CPI/PCE和公司事件；五个交易日内有二元事件但没有 bull/base/bear 压力测试时，不得给满仓或 `execute_now`。
- 资本密集型或亏损公司必须读取最新 10-Q/10-K/8-K/S-3/424B5/ATM、可转债/优先股、债务、股数增长、股权激励、资本开支和资金缺口。两项严重资本风险同时出现时必须阻断新增。
- 若利好公告后仍放量下跌或明显跑输基准，必须标记 `positive_news_failure`；同一 setup 两次出现时，不得摊低成本。
- 杠杆 ETF 必须明确“每日目标倍数不等于多日累计倍数”，并同时分析底层指数、成分股广度、利率、波动损耗和跳空穿越止损的风险。

美股 High-Return Focus Gate 必须说明：

- 当前全部美股持仓、市值、盈亏、长期保护状态和可动用性。
- 可动用本金：动态识别 `deployable_tactical_position`；COIN 仅在机会显著更强时条件可用；CRCL 默认保护。
- Dynamic Discovery Summary：说明本次候选来自 market movers、earnings surprises、news catalysts、unusual volume、relative strength、sector rotation 或 active monitor handoff 中的哪些来源。
- Current Tactical Position Comparison：说明动态候选是否明显优于继续持有当前战术仓。
- Target Achievement Gate：每个候选必须列出 `target_price`、`target_time_window`、`forecast_probability_pct`、`execution_readiness_score` 和 gate 结果；只有目标达成概率 >=80% 且执行准备度 >=80 分，才允许输出 `execute_now`。
- Top 1-3 高回报候选；若没有足够把握，输出 `no_action: keep_current_tactical_position / wait_for_verified_catalyst`。
- 轮动建议必须精确到“卖什么、卖多少、买什么、买多少”。
- 每条建议必须包含止损、止盈、最晚退出日和失效条件。
- 消息必须区分已确认事实、市场传闻、社交热度和价格反应；未验证消息只能输出 `watch`。
- 不得把 `execution_readiness_score` 当作真实概率；不得把涨幅、热度、单源新闻或 screener 排名当作 80% 目标达成概率。
- CRCL 长期信号必须独立输出：持有、低估关注、thesis 风险或额外投入提醒。

US Tactical Performance Panel 必须说明：

- `sleeve_id`、基准时间、基准金额、当前战术资金池金额、当前收益率。
- 月度 `50%+` 目标值、当前缺口、进度状态和若要追上目标所需的剩余收益。
- 季度 `50%+` 目标值、当前缺口、进度状态和复盘窗口。
- 当前战术资金中有多少是持仓、有多少是现金；现金等待是否已经成为目标拖累。
- CRCL 默认排除，COIN 默认不纳入常规战术池，除非本次报告明确把它动态识别为可部署补充流动性。
- 若战术现金来自用户口述或截图而非券商导出，标记 `degraded_user_stated_cash`，真实交易建议只能是 `conditional_action` 或更低。
- 若缺少 active sleeve baseline，必须提示先初始化或人工确认基准，不得用 50% 目标制造新的强买入建议。

Tactical Rotation Relay Panel 必须说明：

- `deployable_tactical_position`：当前动态识别的战术仓，可能是 SOXL、APLD、RGTI、QBTS、COIN 或其他非保护仓。
- `relay_state`：`holding_tactical_position` / `sell_triggered` / `cash_pending_reentry` / `candidate_entry_ready` / `rotated_position_open` / `failed_reentry_reassess`。
- 目标卖出区间、卖出数量、释放现金估算、候选接力队列、每个候选触发价、现金等待截止日、未触发 fallback。
- 现金等待默认当天优先完成，最多 1-2 个交易日；超过后必须重新评估，而不是默认回 SOXL。
- 不得 hardcode SOXL 为默认资金来源；只有当 SOXL 被本次动态识别为当前战术仓时，才能写“当前战术仓=SOXL”。

长期 DCA 段必须额外说明：

- 每月默认 `$1,000` DCA 如何动态分配，以及本期是否需要少买、正常买或加速买。
- 当 crypto 处在长期价值低位且长期 thesis 未破坏时，必须比较“现在买入并开始质押”与“继续等回调”的收益取舍；若适合加大投入，写明可前置 0.25/0.5/1.0 个月 DCA 的金额和理由。
- 必须输出 `Long-Term Low-Value Entry Panel`：说明当前是否长期低位、低位证据、早买入/质押的好处、等待所需最低折扣、现金闲置是否拖慢 5-10 年 10x 目标，以及最终结论是 `accelerated_dca / near_price_entry / limit_order_wait / hold_stablecoin_until_trigger`。
- 当前组合达到 5年/10年 10x 所需年化收益和目标缺口。
- 每个相关持仓是否是目标路径的 `accelerator / neutral / drag / tail_convexity`。
- 每个可质押相关资产的 5年/10年复利倍数，以及含复利后仍需的价格上涨倍数。
- BTC 是否因为低估、风险锚或流动性需求而恢复配置；ETH/lcETH 若超配则只简短说明不新增。
- 推荐中真正相关的增长主线、质押卫星、高凸性卫星和机会仓各自的理由。
- 至少三种 5 年和 10 年情景测算：Survival、Base、Bull/Moonshot。

## 输入顺序

1. 当前持仓账本和截图数量。
2. 当前/1D/7D/30D 价格和估值重建。
3. 现金通道、稳定币和外部战术资金池。
4. 质押估值、APY、奖励、解锁和可流动性。
5. 目标缺口输入：当前组合规模、DCA 金额、5年/10年 10x 所需收益、资产贡献标签。
6. 宏观输入：Fed/CME 利率预期、10Y/2Y、美债曲线、DXY、CPI/PCE、风险偏好、ETF/基金流。
7. Crypto key-person social intel：关键人物/官方账号、验证状态、事件类型、资产映射、最大允许动作和风险标签。
8. API 综合价格和数据质量。
9. DCA 数据质量：价格、走势、成交量、盘口/价差、funding、OI、交易所流动性、项目公告、链上/生态、资金流、市场情绪和重大新闻。
10. 微观 thesis 输入：TVL/链上活跃、费用/收入、开发者/生态、供应/解锁、官方路线图和风险事件。
11. 当前持仓目标偏离和风险阻断项。
12. DCA 候选交易对和资金流/市场状态。
13. monitor handoff 候选。
14. 历史 recommendation/outcome 和 `recommendations/recommendation_history.json`。

## 输出字段

每条行动必须包含：

| 字段 | 说明 |
|---|---|
| action_id | 唯一ID |
| recommendation_id | 推荐历史ID，必须可追溯到 `recommendation_history.json` |
| action | buy/add/hold/trim/sell/watch/paper/no_deploy |
| pair_or_symbol | 交易对或标的，例如 SOLUSDT / ADAUSDT / CRCL |
| amount_or_quantity | 金额或数量 |
| rail | crypto_rail / us_equity_rail / external |
| trigger | 价格、时间或事件触发 |
| stop_or_invalid | 止损或失效条件 |
| take_profit | 止盈或退出条件 |
| forecast_probability_pct | 明确事件概率 |
| execution_readiness_score | points |
| candidate_type | us_tactical / crypto_tactical / dca / long_term / hold_trim |
| entry_zone | 入场区间或 no_new_entry |
| entry_deadline | 入场截止时间或条件失效时间 |
| target_price_or_scenario | 战术目标价或长期目标情景 |
| target_time_window | 目标达成窗口 |
| probability_basis | 概率依据或不足原因 |
| latest_exit_or_review_date | 最晚退出或复盘日 |
| position_size_plan | 金额、数量、资金池比例或 DCA 分批计划 |
| data_quality_status | verified / disputed / stale / missing |
| data_sources_and_timestamps | 主要来源、时间戳、fallback 和降级原因 |
| thesis_confidence_pct | 长期/DCA thesis 置信度 |
| long_term_thesis_score | 长期评分和关键分项 |
| goal_gap_contribution | accelerator / neutral / drag / tail_convexity |
| macro_regime_dca_pace | accelerate / normal / split_more / wait_for_pullback |
| micro_thesis_score | 本次相关资产的微观评分 |
| staking_compounding_summary | 5年/10年复利倍数和达到 10x 所需价格倍数 |
| long_horizon_timing_decision | accelerated_dca / near_price_entry / limit_order_wait / hold_stablecoin_until_trigger |
| long_term_low_value_entry_panel | 长期低位证据、早买入/质押收益、等待所需折扣、现金闲置拖累和最终 DCA 节奏 |
| front_load_dca_plan | 是否前置未来 DCA、前置金额、近期待投入上限和保留的机会仓 |
| staking_wait_cost_vs_pullback | 等待少拿的质押/上涨暴露，和等待必须换来的最低折扣 |
| missing_data_downgrade | no impact / smaller size / conditional only / block |
| risk_decision | allow/downgrade/block/manual_review_required |
| human_confirmation_required | true |
| deployable_tactical_position | 当前动态识别的美股战术资金来源 |
| relay_state | 战术仓接力状态 |
| why_better_than_current_tactical_position | 候选相对当前战术仓的优势 |
| social_key_person_intel | 关键人物/官方账号消息、验证状态、资产影响和最大允许动作 |

## 对 monitor 候选的处理

monitor 候选进入报告后，必须重新分类：

| monitor 输出 | manual 可能输出 |
|---|---|
| watch_candidate | watch/no_deploy |
| paper_candidate | paper_only/watch |
| small_probe_review | conditional_action/manual_review_required |
| event_alert | risk_action/watch/no_deploy |

若用户问“是否能主动获取利润”，回答应区分：

- 可以主动搜集信号、主动触发 paper、主动筛选候选。
- 当前版本不自动真实交易。
- 未通过门槛时不部署真实资金，也是系统主动保护本金的一部分。
