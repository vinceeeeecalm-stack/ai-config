---
name: manual-investment-strategy-operator
description: 手动投资赚钱闭环的唯一日常入口。用于运行短期 Crypto 1–7 日唯一 Top1 与明确入场/等待计划、长期 5 年/10 年 10 倍目标路径、资金加权净 ROI 与盈亏复盘，也用于统一持仓/现金、长期 DCA、事件交易、存量持仓复盘和晨晚主控台；生成带证据与独立执行门控的人工确认草案，不自动下单、转账、质押或换仓。
---

# Manual Investment Strategy Operator V3

这是投资系统的最终仲裁层。先回答“研究上值得做什么”，再独立回答“当前账户能否执行”，不要让现金或证据门控抹掉研究结论。

## 赚钱导向双区总入口

用户说“按赚钱导向双区闭环运行”、只运行短期/长期或复盘赚钱目标时，必须读取 `references/PROFIT_ORIENTED_DUAL_SLEEVE_LOOP.md` 和 `config/profit_objective_v1.json`。本 Skill 是用户唯一日常入口，内部按需调用 `active-alpha-paper-monitor` 获取扫描、历史验证和 paper 证据；只有修改 Skill、策略、数据链、配置或产品时才调用 `closed-loop-delivery-governor` 取得锁并验收。

- 短期只使用 Crypto `tactical_1_7d`，输出一个研究 Top1，并以 `ENTER_NOW` 或 `WAIT_FOR_ENTRY` 给出同一证据快照上的完整交易卡。
- 如果没有证据合格标的，允许保留研究 Top1，但正式 TradePlan 必须为 `NO_FRESH_DECISION` 或不生成；不得编造价格、催化、样本、目标或止损，也不得复用过期计划。
- 长期使用用户确认的持仓、现金、成本和质押快照，比较 5 年 10 倍主路径与 10 年 10 倍兜底；每轮只给一个主要新增资金方向和最多一个次要方向。
- 成交、月度资金加权 ROI、短转长独立重估、双周单假设和四轴状态均由 `scripts/profit_oriented_dual_sleeve.py` 的确定性合同完成。真实运行账本不得写入 Git。
- 每次结论同时显示：系统交付、账户执行准备、短期 Paper/真钱利润、长期目标路径、业务结果、完整阻断、首要阻断和唯一下一步。`RUNTIME_VERIFIED` 不得与外部输入缺失压成一个笼统 `BLOCKED`；未有真实结果时不得写 `BUSINESS_READY` 或声称目标达成。
- `$500` Paper 本金只属于新的 `tactical_1_7d` 独立账本；旧期限未知样本不得迁入。Paper 结果只能作为研究证据，永远不得提升真钱利润或业务就绪状态。
- 当前 TradePlan 要求 60 秒内价格、至少两个公共价格源且价差不超过 1%、单源 12 秒和全轮 120 秒时限；失败时必须重新扫描。

### V3 权威分析内核与正式动作

`scripts/universal_investment_core.py` 是跨 Crypto/美股、短期/长期的唯一确定性分析入口。链路固定为：`目标/期限 → 同时点 EvidenceSnapshot → 估值区间/折价 → 催化/兑现时间 → 下行/失效 → RegressionEvidenceV1 → 跨候选硬门与排名 → 当前实时信号 → LiveInvestmentDecisionV1 → 生命周期/真钱归因`。

- 同一推荐的 `snapshot_id / strategy_version / config_digest / source_digest` 必须完全一致；绑定冲突直接拒绝。
- 每轮保留唯一 `research_top1`。综合分数不得绕过价值、催化、费用后正 EV、回归完整性、流动性、数据质量、风险或实时硬门。
- 正式用户 `current_action` 只允许 `ENTER_NOW / WAIT_FOR_ENTRY / NO_TRADE`。`paper_only / watch` 等旧内部动作不得出现在正式决策卡。
- `WAIT_FOR_ENTRY` 必须带同一当前快照重建的价格区间、触发、有效期和期限适配计划；`NO_TRADE` 只保留研究 Top1 与原因，`decision_card` 必须为空。
- 现金为零不改变通过市场硬门后的 `ENTER_NOW / WAIT_FOR_ENTRY`，但账户轴必须为 `NO_DEPLOY_CASH`、建议金额为 0。
- `RegressionEvidenceV1` 只允许 `historical_walkforward / forward_paper`，永远是内部回归证据；不能成为用户动作、真钱成交、真钱 ROI 或 `BUSINESS_READY` 证据。
- V3 权威状态为 `InvestmentOutcomeStatusV2`：系统交付、账户执行、真钱利润、长期路径独立；Paper 只作为内部附属轴。`BusinessOutcomeStatusV2` 只保留一个兼容周期。

## 硬性边界

- 只做公开或授权只读研究、报告、paper/watch 和人工确认草案。
- 不自动下单、撤单、转账、提现、质押、换仓、融资、卖空、期货、永续或期权交易。
- 美股与 crypto 使用独立资金通道；不得假设跨通道购买力。
- 当前权威覆盖为 `NIGHT=0`、`ENA=0`、`USDT=0`、`us_equity_cash_usd=0`，直到出现更新且带时间戳的用户确认或账户证据。
- CRCL 是保护仓；不得默认卖出。APLD、COIN 和现有 crypto 持仓也不得自动视为资金来源。
- 真实动作必须由用户人工确认；研究通过不等于执行通过。

## 请求路由

每个 recommendation 只能属于一个 `request_mode`：

| request_mode | 适用请求 | 必读引用 |
|---|---|---|
| `longterm_dca` | 5–10 年 DCA、质押复利、长期组合下一美元 | `references/V3_DECISION_CONTRACT.md`、`references/V3_DATA_AUTHORITY_AND_LEARNING.md`、`references/GOAL_ORIENTED_DCA_POLICY.md`、`references/LONG_TERM_PRICE_SCENARIO_POLICY.md` |
| `intraday_scalp` | 几分钟至数小时、当天平仓的高流动性机会 | `references/V3_DECISION_CONTRACT.md`、`references/CANDIDATE_DEEP_DIVE_POLICY.md`、`references/HISTORICAL_CYCLE_AND_EVENT_CONDITIONING_POLICY.md`、`references/PRE_ENTRY_DOWNSIDE_AND_CAPITAL_RISK_GATE.md` |
| `tactical_1_7d` | 1–7 日快速交易、高 ROI、异动/回调 | `references/V3_DECISION_CONTRACT.md`、`references/CANDIDATE_DEEP_DIVE_POLICY.md`、`references/HISTORICAL_CYCLE_AND_EVENT_CONDITIONING_POLICY.md`、`references/PRE_ENTRY_DOWNSIDE_AND_CAPITAL_RISK_GATE.md` |
| `event_trade_1_3w` | 财报、监管、FOMC 前后 1–3 周事件交易 | `references/V3_DECISION_CONTRACT.md`、`references/US_EQUITY_TACTICAL_ALPHA_POLICY.md`、`references/HISTORICAL_CYCLE_AND_EVENT_CONDITIONING_POLICY.md`、`references/PRE_ENTRY_DOWNSIDE_AND_CAPITAL_RISK_GATE.md` |
| `existing_position_review` | 继续持有、减仓、退出、事故复盘 | `references/V3_DECISION_CONTRACT.md`、`references/V3_DATA_AUTHORITY_AND_LEARNING.md`、`references/POSITION_FIRST_RECOMMENDATION_FLOW.md`、`references/TACTICAL_DRAWDOWN_SENTINEL_POLICY.md` |
| `daily_dual_window` | 08:30 晨报、23:30 晚报及学习闭环 | `references/V3_DECISION_CONTRACT.md`、`references/V3_DATA_AUTHORITY_AND_LEARNING.md`、`references/DAILY_DUAL_WINDOW_EXECUTION_AND_LEARNING_POLICY.md`、`references/RECOMMENDATION_LIFECYCLE_AND_DUAL_SAMPLE_POLICY.md`、`references/POSITION_FIRST_RECOMMENDATION_FLOW.md` |
| `risk_adjusted_path` | Sharpe/Sortino、风险调整路径、趋势与超跌修复排序 | `references/V3_DECISION_CONTRACT.md`、`references/RISK_ADJUSTED_PATH_QUALITY_POLICY.md` |

不要每轮读取全部 references。只读取本模式列出的文件；需要专项细节时再读取一个直接相关引用。

## 标准流程

1. 对 `daily_dual_window`，先按 `references/RECOMMENDATION_LIFECYCLE_AND_DUAL_SAMPLE_POLICY.md` 更新全部未关闭判断、到期项与复盘草稿，再发现新机会。不得只复盘当天推荐。
2. 用 `scripts/v3_horizon_router.py` 规范化请求模式；一个报告可含多个模式，但必须拆成不同 recommendation ID。
3. 用 `scripts/v3_portfolio_state.py` 合并当前覆盖与 legacy ledger。输出每个字段的 `value / as_of / source / confidence / evidence_id` 和冲突记录。
4. 创建一次 `EvidenceSnapshotV2`。所有研究角色共享同一个 `snapshot_id`、价格和截止时间；数字判断必须引用 evidence ID。
5. 按请求模式分析：
   - 长期 DCA 先运行 `scripts/longterm_next_dollar_ranker.py`，在持仓、美股、普通 ETF、crypto 与现金之间比较基本面/网络、护城河、价值捕获、资产负债/供应、估值、生存回撤、相关性、组合下一美元和 5/10 年情景；不得沿用固定资产角色结论。
   - `intraday_scalp` 使用 60 秒内报价、闭合 1m/5m、VWAP、开盘区间、相对成交量、价差、深度和市场锚，默认有效 5 分钟并当日强制平仓；杠杆 ETF 必须确认底层。
   - 短线/事件交易先对 Discovery Top3 使用同一 point-in-time 历史口径做样本外胜率区间、保守 EV、Profit Factor、回撤、回报空间和流动性比较；再对 rank-1 做完整深度研究。最多两个备选只有通过最低历史质量门时才进入主报告。
   - 存量持仓先读取原始期限、成本/盈亏口径、thesis 和失效条件，再判断持有、退出或轮动。
   - Crypto 扫描遇到 mainnet、新链、钱包、DEX 或 launchpad 支持事件时，
     先调用 Active sibling 的
     `../active-alpha-paper-monitor/scripts/new_chain_opportunity_radar.py`。不得把
     Binance/CoinGecko 静态列表当作完整市场；DEX-only 候选必须保留
     事件→生态→资产→量价→安全/容量的推导链。
6. 先由统一内核形成唯一 `research_top1` 和正式 `current_action`，再单独运行资金与结算门，形成 `account_execution`。旧 `research_decision / current_direct_decision / execution_decision` 仅作一个兼容周期的内部投影。
   风险调整路径只允许改变 research/paper 排序；晋级前不得改变真实执行门。
7. 用 `scripts/v3_decision_contracts.py` 校验 recommendation。未通过时不得写入正式历史，只能输出修复清单。
8. 校验通过后才可写 recommendation，并同时生成带 `next_check_at` 和 `review_due_at` 的 `RecommendationLifecycleV1`。冻结判断进入观察样本；只有 Paper 或用户确认成交进入交易样本。
9. 复盘只追加 OutcomeReviewV2，不改原价格、概率、时间窗或证据快照。
10. 报告先输出一页决策卡，再附证据和审计。允许输出“今日无合格标的”；此时使用 `rejected_candidates` 保留最近候选、冻结价格和拒绝原因，不得为凑 schema 伪造概率、目标、止损或事件日期。

## 决策语义

- 权威 `current_action` 只允许：`ENTER_NOW / WAIT_FOR_ENTRY / NO_TRADE`。
- `current_direct_decision` 仅是旧兼容字段，不能覆盖权威 `current_action`。
- `research_decision` 表示不考虑当前现金时的研究判断。
- 旧 `execution_decision` 可保留内部兼容值，但正式用户输出不得显示 `paper_only / watch`；账户轴只允许 `CASH_READY / NO_DEPLOY_CASH / SETTLEMENT_BLOCKED`。
- 未来触发只能写入 `observation_plan`，不能代替当前直接决策。
- 现金为零时，研究结论仍可为 `enter_now` 或 `small_entry_now`，但执行必须为 `no_deploy_cash`、金额为 0。
- 最终记录保留兼容字段 `best_candidate / research_decision / current_direct_decision / account_state / execution_decision / executable_amount`，并对 `intraday_scalp / tactical_1_7d / event_trade_1_3w` 增加 `ranked_recommendations`：一个 `primary_candidate` 和最多两个 `qualified_alternatives`。
- 主推荐是系统的最高优先级判断；备选必须说明为何在样本外胜率区间、保守 EV、预期回报、回撤、流动性、催化或入场位置上落后。不得为了凑足三个标的而降低门槛。
- 相同 `snapshot_id / strategy_version / config_hash` 必须生成相同排名。现任主推荐只有在 thesis 失效或挑战者具有实质且可复现的风险调整优势时才可被替换。
- 发现阶段只消费概率无关的 `DiscoveryCandidateV1` Top3，不等待委员会。Top3 的历史批量比较不等于三份完整委员会研究；只有 rank-1 进入完整深挖。日内小仓需两个角色，1–7 日完整仓需四个相关角色，二元事件、新高风险资产或重大长期配置需六个以上角色。
- 战术动作不使用统一 80% 门槛：`n<10` 仅 judgment-only；`n=10–29` 仅 wide interval，并要求正保守 EV、R/R≥2、完整实时信号和账户风险≤0.25%；`n≥30` 的 `enter_now` 还要求无前视的未触碰 holdout、正的保守下界 EV 和账户风险≤0.5%。

## 期限硬隔离

- FOMC、24 小时走势和短期技术面只能调整长期 DCA 的贡献节奏，不能决定长期资产质量。
- 长期 DCA 不要求价格止损、战术目标或事件前强制退出；使用 thesis invalidation、贡献计划、季度/年度复核。
- 战术与事件交易必须提供决策价、入场窗口、目标、止损、时间止损、事件处理、最迟退出和三情景概率。
- 一个概率字段只能回答一个明确事件；情景概率与“目标先于止损概率”不得混用。
- 样本小于 10 只能写 `judgment_only`；10–29 输出宽区间；至少 30 个无前视样本后才可写 `calibrated`。

## 持仓和数据权威

- 较新的用户明确成交确认优先于账户导出；较新的账户导出/截图优先于当前覆盖；当前覆盖优先于 legacy ledger。
- 同一时间等级下，直接账户证据优先于推断。
- Legacy ledger 只补成本、lot 和历史字段，不得覆盖更近的数量或现金。
- 冲突不得静默平均；报告必须显示采用值、被覆盖值、来源和原因。
- 重复用户账户确认按 rail 与规范化账户事实去重；保留原始审计行，但等价新写入返回 `NO_UPDATE`，解析结果显示 `duplicate_count`。
- 旧估算长期持仓只能标记 `DATA_DEGRADED / RESEARCH_ONLY`，不得推断用户已确认或输出正式 CAGR/在轨结论。
- 价格、funding/OI、期权、链上、SEC/IR、财报和宏观字段都必须带 `as_of / source / freshness`。
- 缺失数据只降级受影响的结论；不得把局部缺口伪装成全市场没有机会。

## 推荐历史与学习

- V2 到期时间只读取结构化 `review_due_at / latest_exit_or_review_at / entry_deadline`。
- 每个 active lifecycle 必须有 `next_check_at`；新扫描不得跳过旧生命周期、到期项或待确认复盘。
- 观察样本与交易样本使用独立分母；未触发、不行动、Legacy observation、Paper 和真钱不得混算。
- 复盘分开记录观察触发、真实执行、目标/止损先后、窗口状态、MFE、MAE、滑点和事件跳空。
- 一次性价格和新闻留在当日报告。明确 schema、安全或数据错误可立即修复；分析启发式需三次独立复现或回测证据后才可晋升。
- 每次规则晋升记录：`漏项 → 根因 → 通用规则 → 受影响组件 → 验证结果`。

## 修改与发布门

修改本 skill、脚本、配置、推荐 schema 或共享 ledger 后：

1. 运行 `python3 -m unittest discover -s manual-investment-strategy-operator/tests -v`。
2. 运行三套 skill 的 `quick_validate.py`。
3. 运行 `python3 manual-investment-strategy-operator/scripts/sync_installed_skill.py --with-smoke`。
4. 只有 manifest 哈希一致、正式记录 validator 通过、负向 smoke 能失败、正式 smoke 全部关键门通过时，才能宣称完成。

Smoke 只验证链路，不是投资建议，也不授权真实交易。
