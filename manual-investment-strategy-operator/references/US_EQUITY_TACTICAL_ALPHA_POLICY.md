# US Equity Tactical Alpha Policy

## 目的

本 policy 用于美股短期高回报候选，不用于长期 DCA。目标是在本金受限、不自动下单的前提下，把当前可动用的美股战术仓动态轮动到 1-5 个交易日内预期回报率最高、催化剂最明确、流动性充足的标的。候选和资金来源都由实时数据、当前持仓和风险模型动态生成，不 hardcode SOXL 或任何固定标的。

## High-Return Focus Gate

每次美股报告必须先执行本 gate：

- 最多输出 `1-3` 个重点推荐标的，不铺开长名单。
- 若没有候选同时满足数据 verified、短期催化剂、动量/量能、弹性、流动性、止损空间和当前持仓适配，输出 `no_action`。
- `execute_now` 只允许给第一名候选，且必须明显优于继续持有当前识别出的 `deployable_tactical_position`。
- 若候选只是“可能更好”，输出 `watch`，不建议换仓。
- 若真实预测把握不足，不允许写成 80% 概率；必须写明“把握不足，不动”。

## Open Tactical Position Drawdown Sentinel

在发现新候选之前，每次美股报告必须先执行已持仓风险哨兵，详见 `TACTICAL_DRAWDOWN_SENTINEL_POLICY.md`。

这个 gate 解决的是“买入后没有及时提醒大额回撤”的问题。它必须优先于 High-Return Focus Gate、Dynamic Discovery 和 Tactical Rotation Relay。

报告必须先回答：

- 当前战术仓是哪一个，成本价是否 verified 或 user-stated。
- 当前价格相对成本亏损多少，相对近期高点回撤多少。
- 是否跌破入场 thesis 的支撑位或趋势线。
- 成交量是否放大，是否出现 gap down、连续阴线或相对 QQQ/SMH/IWM 明显跑输。
- 是否出现融资、增发、债务、资本开支、项目交付、客户集中、财报或分析师下修相关风险。
- 现在最大动作是继续持有、禁止加仓、减仓复盘、退出复盘，还是只做修复计划。

强制阈值：

- 回撤成本 `>=8%`：黄灯，禁止新增，必须复盘。
- 回撤成本 `>=12%`：橙灯，必须输出减仓/止损计划。
- 回撤成本 `>=18%` 或近期高点回撤 `>=25%`：红灯，禁止新增或继续宽松持有，必须输出退出/修复计划。
- 若成本缺失，使用用户口述成本或最近一次明确入场价，并标记 `cost_basis_degraded`；缺失成本不能成为不提醒风险的理由。

如果本 gate 为 `orange/red/black`，后续候选推荐必须解释“为什么先处理现有亏损仓，而不是把注意力转移到新标的”。

## Target Achievement Gate

动态发现候选必须通过本 gate，才允许进入 `execute_now` 或强主推荐：

- `forecast_probability_pct >= 80`：目标标的在指定 `target_time_window` 内达到 `target_price` 的真实预测概率。
- `execution_readiness_score >= 80`：数据质量、消息、量价、流动性、止损/止盈、资金来源和人工确认准备度。
- 两者必须同时通过。若只通过一个，必须降级为 `watch`、`paper_only`、`conditional_action`、`small_probe_review` 或 `no_action`。
- `60%-79%` 概率段是学习阶段，不是失败。它可以输出清晰的价格、时间、止损和复盘计划，但必须标注为学习型/条件型建议，不能作为高把握 `execute_now`。
- 未通过时必须列出失败项：概率不足、执行度不足、目标价不合理、时间窗口不足、数据冲突、消息未验证、历史样本不足或已大涨导致赔率下降。

概率估计约束：

- 不得把 `execution_readiness_score` 当成概率。
- 不得把短线涨幅、社交热度、单源新闻或 screener 排名当成 80% 真实概率。
- 若缺少历史基础概率、同类事件样本、paper/walk-forward 或多源确认，最高只能输出 `watch`。
- 如果候选已有 fresh 数据、明确触发价和止损，但历史样本不足，允许输出 `paper_only` 或 `conditional_action` 作为学习样本；该建议必须进入 recommendation history，后续复盘后才可提高概率权重。
- 若候选高波动但已大涨，目标价概率必须重新下调，不能因涨幅大而自动推荐。
- 目标时间默认服务月度目标，但单笔战术持有仍按 1-5 个交易日复盘，绝对最多 10 个交易日；月度翻倍目标应由多次高质量轮动累计实现，而不是单笔无纪律持仓。

执行准备度评分满分 100：

| 项目 | 分数 |
|---|---:|
| 数据 verified / 多源一致 | 20 |
| 消息催化剂已确认 | 20 |
| 量价和波动支持 | 20 |
| 流动性和价差可执行 | 15 |
| 止损/止盈和风险回报明确 | 15 |
| 资金来源与持仓适配 | 10 |

## Goal-Linked Timeboxed Trade Plan

美股战术报告不能只给价格表。每个短线候选必须先说明它服务哪个目标、适合哪个时间窗口，以及这笔钱在该窗口内如何验证成败。

每个美股战术候选必须输出三层时间节点：

- `one_week_test`: 未来 1-5 个交易日。用于判断是否可以小仓测试、回补、追随突破，必须给出入场触发、目标价、止损和复盘日。
- `two_week_trend`: 未来 6-10 个交易日。用于判断主趋势是否仍成立，必须说明若未触发入场、触发后未到目标、或跌破失效位时如何处理。
- `monthly_goal_path`: 本月战术资金池是否更接近 ROI `100%` 进攻目标。必须说明这笔交易预计贡献多少收益、若不交易会造成多少现金等待、以及为什么不为了目标而追高。

报告必须先写 `trade_clock_state`：

| 状态 | 含义 | 最大动作 |
|---|---|---|
| `fresh_pullback_entry` | 回踩到可买区，基本面/主线未坏 | `conditional_action` 或更高 |
| `extended_chase_risk` | 标的已快速上冲，买入会放弃卖高优势 | `watch / small_probe_review` |
| `trend_intact_wait_pullback` | 主线仍强，但价格不划算 | `watch / conditional_limit_order` |
| `breakout_confirmed_small_probe` | 突破确认但位置偏高，只允许小仓 | `small_probe_review` |
| `thesis_broken_no_entry` | 主线、基本面或风控已坏 | `no_deploy` |

对已卖出后等待接力的现金，报告必须明确：

- `cash_wait_deadline`: 现金最多等到哪一天，默认 1-2 个交易日；若等待时间超过这个窗口，必须重新评估而不是继续沿用旧价位。
- `reentry_is_worth_it_if`: 哪个价格/条件让重新入场比继续持现金更值得。
- `do_not_reenter_if`: 哪个价格/条件说明追高风险过大，宁可错过。
- `planned_exit`: 到达哪个价格、日期或失效条件必须卖出/复盘。

如果用户已经在高位卖出某个战术仓，报告必须区分：

- `trend_follow_small_probe`: 防止趋势继续走强的小仓跟随位。
- `advantage_reentry`: 真正保留“卖高优势”的主仓回补位。

主行动清单必须优先输出 `advantage_reentry`，只有在明确说明概率和仓位限制后，才允许输出 `trend_follow_small_probe`。

## Candidate Deep Dive Requirements

所有美股动态候选必须先输出候选卡片，再进入 Top 1-3 或行动清单。候选卡片必须包含：

- `entry_zone`: 当前可买区间、回踩区间或突破确认区间。
- `entry_deadline`: 该 setup 的最晚入场时间，通常为 1-3 个交易日；超过后必须重算。
- `target_price`: 第一目标价，不允许只写“看涨”。
- `target_time_window`: 默认 1-5 个交易日复盘，最多 10 个交易日。
- `forecast_probability_pct`: 目标窗口内达到目标价的真实概率。
- `probability_basis`: 历史同类样本、事件催化、量价结构、相对当前战术仓表现、paper/walk-forward 或不足原因。
- `execution_readiness_score`: 0-100 分，按 Target Achievement Gate 拆分。
- `stop_loss`: 明确价格或百分比。
- `take_profit_zone`: 分批止盈区间。
- `latest_exit_date`: 最晚退出日；杠杆 ETF 和高波动事件股不得无限持有。
- `why_better_than_current_tactical_position`: 为什么它比继续持有当前战术仓更值得轮动；如果不能证明，则保持当前战术仓。
- `one_week_test`: 未来 1-5 个交易日的入场、目标、止损和复盘规则。
- `two_week_trend`: 未来 6-10 个交易日的趋势验证、目标延续和失效处理。
- `monthly_goal_path`: 这笔交易如何服务本月战术收益目标，以及为什么不会为了目标而追高。
- `trade_clock_state`: 当前是回踩入场、趋势等待、突破小仓、追高风险还是 thesis 破坏。

深度分析必须同时给出：

- 已确认事实、市场传闻、社交热度、价格反应四类消息拆分。
- 1D / 5D / 20D / 60D 价格结构、ATR 或波动、成交量相对基线。
- 支撑位、阻力位、失效位。
- 预期上涨空间与止损空间；若上行空间不足止损空间的 2 倍，默认降级。

## Pre-Entry Downside and Capital Risk Gate

任何美股战术主推荐都必须在 `Target Achievement Gate` 前通过
`PRE_ENTRY_DOWNSIDE_AND_CAPITAL_RISK_GATE.md`：

- 同时估计 `target_before_stop_probability_pct` 与
  `stop_before_target_probability_pct`。如果先止损概率不低于先到目标概率，
  不得部署。
- 输出 5/10/20 日最大不利波动和隔夜/事件跳空压力。仓位按压力损失预算
  反推，不能按希望赚多少钱反推。
- 检查未来十个交易日财报和宏观事件。五个交易日内有事件但没有
  bull/base/bear 压力测试时，最多 `paper_only / watch`。
- 对亏损或资本密集型公司，读取 SEC 融资、稀释、债务、股数、股权激励、
  客户集中度、资本开支与资金缺口；两项严重资本风险同时出现时阻断新增。
- 大额租约或订单不能直接等同为当前股权价值；必须扣除开工/交付时间、
  客户信用与集中度、建设成本、融资成本、终止/续约条款和完工风险。
- 重大利好之后仍放量下跌或明显跑输基准，记为 `positive_news_failure`；
  同一 setup 两次出现时禁止摊低成本。
- 杠杆 ETF 必须同时审查底层指数、板块广度、利率、每日复位与跳空风险。
  默认 1-3 日复盘，超过 5 日必须每日重新确认，10 日为绝对上限。

## Dynamic US Equity Alpha Discovery

候选必须来自数据，而不是固定列表。每次运行根据以下来源动态生成候选：

- market movers：当日/近几日强涨跌、异常成交额、异常波动。
- earnings surprises：财报、指引、预告、分析师上修或下修。
- news catalysts：监管、政策、订单、产品、融资、并购、诉讼、行业事件。
- unusual volume：成交量/成交额显著高于近 20 日均值。
- relative strength：相对当前战术仓、QQQ、SOXX、相关行业 ETF 的强弱。
- sector rotation：资金从半导体、AI、crypto equity、能源、工业、软件等板块迁移。
- active_monitor_handoff：主动监控 skill 输出的 watch/paper/event 候选。

动态发现规则：

- 低价、高波动、高消息催化、高成交额是偏好，不是硬性过滤。
- 价格偏高或波动不足的标的会降权，但不得永久排除。
- `NVDL`、`TQQQ` 不禁用；当价格、波动和催化剂不足时自动降权，只有明显优于当前战术仓时进入 Top 1-3。
- 未验证消息只能作为 `watch`，不能作为 `execute_now`。
- 每个候选必须区分：已确认事实、市场传闻、社交热度、价格反应。
- 每个候选必须说明“为什么现在比继续持有当前战术仓更好”；若不能说明，则不进入主推荐。

## 资产范围

允许：

- 股票现货。
- 普通非杠杆 ETF。
- 多头杠杆 ETF，但只能短期战术持有。
- 单股票多头杠杆 ETF，但只能在对应底层股票有明确催化剂时小仓使用。

禁止：

- 期权、0DTE、保证金、卖空、反向 ETF、期货、结构化票据。
- 把杠杆 ETF 作为长期 DCA 或核心仓。

## 候选排序

当用户要求“最激进”“短期回报最高”“值得追”时，报告必须优先输出 `us_equity_tactical_alpha`，并按以下顺序评估：

1. 短期催化剂：财报、指引上修、行业资金流、政策/监管变化、AI/crypto beta 共振，权重 30%。
2. 波动和弹性：ATR、真实波动、beta、杠杆倍数、对核心事件敏感度，权重 25%。
3. 当前动量：1D/5D/20D 趋势、放量、相对强弱，权重 20%。
4. 流动性：成交额、价差、可执行性，权重 15%。
5. 价格与本金适配度：低单价和可分批性加分，但不是硬限制，权重 5%。
6. 风险回报：止损距离是否小于预期上行空间的 1/2，权重 5%。
7. 持仓适配：是否比继续持有当前战术仓更值得换仓。

## Dynamic Tactical Funding Source

- 每次报告必须先识别 `deployable_tactical_position`，不能默认 SOXL。
- 识别依据：当前美股持仓、可卖流动性、盈亏、波动、持有周期、长期保护状态、候选相对机会强度。
- 当前实例可能是 SOXL、APLD、RGTI、QBTS、COIN 或其他战术仓；报告必须写明“当前识别结果”，而不是把它当成永久资金池。
- COIN 只有在 crypto equity 信号显著强于当前战术仓路径时，才可作为补充流动性。
- CRCL 是长期 Circle / stablecoin infrastructure 配置保护仓，默认不卖、不用于常规短线轮动。
- 新增外部资金默认不进入美股战术池；只有 CRCL 长期低估或极强催化剂时输出 `exceptional_add_signal`，由用户决定是否额外投入。

## Tactical Position Rotation Relay

美股短线轮动必须使用通用接力状态机：

| 状态 | 含义 |
|---|---|
| `holding_tactical_position` | 当前战术仓仍在持有中 |
| `sell_triggered` | 当前战术仓达到止盈/止损/换仓触发 |
| `cash_pending_reentry` | 已卖出或计划卖出，现金等待新候选触发 |
| `candidate_entry_ready` | 新候选触发入场且通过风险门 |
| `rotated_position_open` | 已人工确认换入新战术仓 |
| `failed_reentry_reassess` | 现金等待超过窗口或候选失效，需要重评 |

接力规则：

- 卖出当前战术仓后，优先当天完成接力，最长等待 1-2 个交易日。
- 现金等待期间必须列出候选队列、入场触发价、最晚等待时间和 fallback。
- 若候选未触发或未过双 80，不能为了避免现金空窗而硬买。
- 超过等待窗口后，重新评估：回到当前战术仓、继续现金、或换入新的 Top 候选。

## US Tactical Performance Panel

美股短线目标不是“看到机会就交易”，而是让动态可部署战术资金池朝月度 ROI `100%` 进攻目标前进。每次美股报告必须先用 `scripts/us_tactical_performance_tracker.py` 读取 `performance/us_tactical_performance.json`，输出：

- 当前 `sleeve_id`、基准金额、当前战术资金池金额和当前收益率。
- 月度 ROI `100%` 进攻目标值、缺口、进度状态，以及如果落后，是否需要提高候选质量或接受短期现金等待。
- 季度报告检查点、缺口和复盘状态；不得把它当成更低的替代目标。
- 当前战术资金中持仓与现金的比例；现金等待可以是接力状态，但超过 1-2 个交易日会成为目标拖累。
- 数据质量：券商现金若来自用户口述或截图，标记 `degraded_user_stated_cash`，只能输出 `conditional_action` 或更低。

计入口径：

- 纳入：本次动态识别的 `deployable_tactical_position`、已经卖出后等待接力的美股现金。
- 默认排除：CRCL 等长期保护仓。
- 条件纳入：COIN 或其他非保护仓，只有当本次报告明确把它识别为可部署补充流动性时才计入。

若没有 active sleeve baseline，报告必须先建议初始化/人工确认基准，不得把月度翻倍目标缺口解释成马上追高的理由。

## 特殊持仓

- `CRCL`: 默认长期持有；只有 thesis 破坏才减仓，只有长期低估才提示额外投入。
- `COIN`: 可作为补充流动性或候选，但必须明显优于当前战术仓。
- `NVDL` / `TQQQ`: 不禁用，但默认不作为固定主候选；若价格、波动或催化剂不足，自动降权。
- `SOXL`: 只是可能被识别为当前战术仓的一个例子，不是永久 benchmark 或默认资金来源。

## 输出要求

每次美股报告固定输出：

- 当前美股持仓：全部美股仓位的当前市值、盈亏、长期保护状态和可动用性。
- Open Tactical Position Risk Sentinel：已持有战术仓的成本、现价、回撤、相对大盘、放量破位、新闻/融资/债务/项目风险、风险等级和必须动作。
- 可动用本金：动态识别 `deployable_tactical_position`；COIN 条件可用；CRCL 保护。
- Dynamic Discovery Summary：本次候选来自哪些数据源、消息源和 monitor handoff。
- Current Tactical Position Comparison：候选是否明显优于继续持有当前战术仓。
- Target Achievement Gate：每个候选是否同时通过真实目标达成概率 >=80% 与执行准备度 >=80。
- Top 1-3 候选：只列动态发现后的最强候选，不铺开长名单。
- Tactical Rotation Relay Panel：当前战术仓、目标卖出区间、释放现金、候选队列、候选触发价、现金等待期限、fallback。
- 轮动建议：保持当前战术仓 / 卖部分当前战术仓换入动态候选 / 短等现金 / 不动。
- 风险条件：止损、止盈、最晚退出日、失效条件。
- CRCL 长期信号：持有 / 低估关注 / thesis 风险 / 额外投入提醒。
- Goal-Linked Timeboxed Trade Plan：按 `one_week_test`、`two_week_trend`、`monthly_goal_path` 说明每个候选本周怎么做、两周内怎么验证、月底目标如何影响仓位。

每个战术候选必须输出：

- action: `execute_now` / `conditional_action` / `watch` / `no_deploy`
- suggested_amount_pct_of_tactical_pool
- source_position_to_sell
- deployable_tactical_position
- current_tactical_position_comparison
- why_better_than_current_tactical_position
- target_symbol_to_buy
- entry_zone
- target_price
- target_time_window
- target_achievement_probability_pct
- probability_basis
- readiness_breakdown
- stop_loss
- take_profit_zone
- max_holding_period
- invalidation
- rotation_reason
- expected_short_term_roi_rank
- one_week_test
- two_week_trend
- monthly_goal_path
- trade_clock_state
- cash_wait_deadline
- discovery_source
- data_quality_status
- catalyst_type
- exceptional_add_signal
- reason

如果没有达到真实胜率或执行准备度门槛，必须明确说明不能把它描述成 80% 真实预测概率。

## 仓位上限

- 单一杠杆 ETF 初始部署不超过美股战术资金的 30%-40%。
- 默认单次战术交易最大可接受回撤为 8%-12%。
- 当前战术仓超过美股仓 25% 时，任何新增或轮动候选必须证明明显优于继续持有当前战术仓，否则输出保持当前战术仓或 watch。
- 高度相关的杠杆 ETF 或单股杠杆产品不应同时重仓；但这不是永久禁用规则，必须按当前相关性动态判断。
- 所有杠杆 ETF 默认 1-5 个交易日复盘，绝对最多 10 个交易日。

## 推荐动作粒度

报告必须达到以下粒度：

- `当前战术仓=SOXL，卖出 6股 -> 买入 SYMBOL $1000-$1200`
- `当前战术仓=APLD，保留 APLD，不换仓；若跌破 X，减半`
- `不动：动态候选均未明显优于当前战术仓，等待 verified catalyst`
