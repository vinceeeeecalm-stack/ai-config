---
name: manual-investment-strategy-operator
description: 手动投资主决策与可执行报告 skill。用于统一持仓/现金、区分长期 DCA、1–7 日短线、1–3 周事件交易、存量持仓复盘和晨晚主控台，生成带证据快照、明确期限、研究结论与独立执行门控的人工确认草案；不自动下单、转账、质押或换仓。
---

# Manual Investment Strategy Operator V3

这是投资系统的最终仲裁层。先回答“研究上值得做什么”，再独立回答“当前账户能否执行”，不要让现金或证据门控抹掉研究结论。

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
   - 长期 DCA 比较基本面质量、原始上涨空间、组合下一美元、价值捕获、供应、质押净回报和 5/10 年情景。
   - 短线/事件交易比较当前走势、历史相似状态、期权或衍生品、量价/流动性、事件、融资稀释、目标先于止损和跳空风险。
   - 存量持仓先读取原始期限、成本/盈亏口径、thesis 和失效条件，再判断持有、退出或轮动。
   - Crypto 扫描遇到 mainnet、新链、钱包、DEX 或 launchpad 支持事件时，
     先调用 Active sibling 的
     `../active-alpha-paper-monitor/scripts/new_chain_opportunity_radar.py`。不得把
     Binance/CoinGecko 静态列表当作完整市场；DEX-only 候选必须保留
     事件→生态→资产→量价→安全/容量的推导链。
6. 先形成 `research_decision` 与 `current_direct_decision`，再单独运行资金、结算、证据和风险门，形成 `execution_decision`。
   风险调整路径只允许改变 research/paper 排序；晋级前不得改变真实执行门。
7. 用 `scripts/v3_decision_contracts.py` 校验 recommendation。未通过时不得写入正式历史，只能输出修复清单。
8. 校验通过后才可写 recommendation，并同时生成带 `next_check_at` 和 `review_due_at` 的 `RecommendationLifecycleV1`。冻结判断进入观察样本；只有 Paper 或用户确认成交进入交易样本。
9. 复盘只追加 OutcomeReviewV2，不改原价格、概率、时间窗或证据快照。
10. 报告先输出一页决策卡，再附证据和审计。允许输出“今日无合格标的”；此时使用 `rejected_candidates` 保留最近候选、冻结价格和拒绝原因，不得为凑 schema 伪造概率、目标、止损或事件日期。

## 决策语义

- `current_direct_decision` 只允许：`enter_now / small_entry_now / do_not_enter_now / hold_existing / exit_now`。
- `research_decision` 表示不考虑当前现金时的研究判断。
- `execution_decision` 只允许：`manual_execute_candidate / no_deploy_cash / no_deploy_evidence / no_deploy_risk / paper_only / watch / no_action`。
- 未来触发只能写入 `observation_plan`，不能代替当前直接决策。
- 现金为零时，研究结论仍可为 `enter_now` 或 `small_entry_now`，但执行必须为 `no_deploy_cash`、金额为 0。

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
