# Manual Dispatch Market & Sentiment Refresh Policy

## Purpose

本 policy 定义：每次用户手动调度 `manual-investment-strategy-operator` 生成报告、DCA 计划、美股战术建议或持仓复盘时，系统必须先主动刷新市场数据、宏观数据、情绪数据和关键消息，再判断策略。

这里的“主动”只表示**本次手动运行内主动取数和分析**，不表示后台自动盯盘、自动下单或自动转账。后台扫描、paper trading 和长期自动化路线图属于 `active-alpha-paper-monitor`，且仍不能直接决定真实交易。

刷新数据不是最终目的。每次调度还必须执行 `Full-Market Deep Analysis Objective Gate`：把全盘数据、情绪和持仓状态转成目标导向策略判断，分别服务美股/战术资金的月度收益目标，以及 crypto/DCA 的 5-10 年长期收益目标。

## Dispatch Boundary

| 场景 | manual skill 行为 |
|---|---|
| 用户要求“生成今天报告/重新分析/看现在机会” | 执行一次完整取数、研究、仲裁和可读报告 |
| 用户没有发起请求 | 不自动运行，不后台轮询，不主动推送交易 |
| active skill 有 handoff | 作为候选输入，manual 必须重新刷新数据和复核组合 |
| 网络/API/数据源失败 | 输出降级报告，禁止新的 `execute_now` |
| 用户明确要求“必须最新数据/不要降级” | 使用 `--require-fresh-market-intelligence`，若无法完成本轮核心行情 fresh refresh，则不输出任何当前买入/卖出/DCA/回接建议，只输出失败原因 |
| 用户要求自动化扫描 | 引导到 `active-alpha-paper-monitor`，本 skill 只消费结果 |

## Required Gate

每次报告在 `Position Gate` 后、`Prior Thesis Challenge` 前必须执行：

```text
Market Data Source Preflight
Manual Dispatch Strategy Contract
Manual Dispatch Market & Sentiment Refresh Gate
Realtime Price Certification + Dynamic Analysis Trigger
Macro/Micro Sufficiency Gate
Full-Market Deep Analysis Objective Gate
```

`Manual Dispatch Strategy Contract` 是本次调度的总约束：每次用户触发报告时，系统必须完成“取数、情绪、全盘分析、目标映射、操作结论”的闭环；如果闭环不完整，就必须降级，不能用旧结论补位。

`Market Data Source Preflight` 由 `scripts/market_data_source_preflight.py` 生成，先检查公开数据源和可选 API key 环境变量是否可用。它也支持用户显式传入 ignored `.env.local`，但不会输出密钥值。它不抓账户数据、不授权交易、不输出密钥，只输出每个源的 `ok / failed / missing_key / degraded` 状态。

随后 `Manual Dispatch Market & Sentiment Refresh Gate` 需要生成 `fresh_market_intelligence_snapshot`。如果无法生成，则报告必须标记：

```text
market_intelligence_degraded=true
```

并把新的真实交易动作上限降为 `watch / paper_only / conditional_action / no_deploy / hold / trim_review`。

`Realtime Price Certification + Dynamic Analysis Trigger` 由 `references/REALTIME_PRICE_DYNAMIC_ANALYSIS_POLICY.md` 定义。凡是涉及当前入场、出场、DCA、回接、限价或战术轮动，必须先认证当前价格、盘口、来源时间戳和数据口径，再判断是否需要触发美股动态扫描、crypto 多源快照、宏观快照、社交/关键人物、链上/DeFi 或 research committee。只拿到昨收、截图价或用户口述盘前价时，不得输出主仓追高或强买入，只能输出条件计划、等待触发或状态报告。

## Macro/Micro Sufficiency Gate

每次手动报告不能因为抓到了几个价格就直接生成完整操作结论。`Macro/Micro Sufficiency Gate` 必须判断本轮数据是否足以支撑“今日手动操作报告”。如果数据不够完整，报告必须收缩为“状态报告 + 等待触发条件”，而不是硬给买卖建议。

该 gate 必须在 `Full-Market Deep Analysis Objective Gate` 之前执行，并输出：

| 字段 | 说明 |
|---|---|
| `macro_coverage_status` | 利率、美元、美债、风险偏好、ETF/资金流是否有最新可得信息 |
| `us_micro_coverage_status` | 美股候选的盘前/盘中价格、板块、成交量、催化剂和相对强弱是否足够 |
| `crypto_micro_coverage_status` | crypto 价格、24h/7d/30d、盘口、成交量、链上/项目/解锁/质押是否足够 |
| `portfolio_cash_coverage_status` | 两路现金、可用资金、开放订单和质押锁定是否足够 |
| `sufficiency_decision` | `full_report_allowed / action_report_allowed / status_only / no_current_action_due_to_missing_latest_data` |
| `max_allowed_action` | `execute_now / conditional_action / watch / no_deploy / hold` |
| `missing_items_that_matter` | 本轮真正影响结论的缺失项 |

决策规则：

- `full_report_allowed`：宏观、微观、行情、流动性、持仓现金和目标映射都足够，允许输出完整今日报告；是否能 `execute_now` 仍需后续风险门。
- `action_report_allowed`：核心行情和关键微观足够，宏观或补充源有缺口；只允许 `conditional_action / watch`，不得写成高把握执行。
- `status_only`：只能说明市场状态、风险和等待触发；不得输出新的买入/卖出金额。
- `no_current_action_due_to_missing_latest_data`：当前价格、盘口、成交量或现金通道不完整，不能给任何当前操作价位。

最低要求：

- 美股战术报告至少需要：本轮现价或用户口述盘前价的明确标记、上一交易日 OHLCV、QQQ/SMH 或相关板块走势、候选自身成交量/催化剂、当前美股现金/战术仓状态。
- Crypto DCA 报告至少需要：本轮 Binance 或等价现货价格/24h stats、盘口或价差、Fear & Greed 或等价情绪、持仓与 USDT 可用金额、以及本轮相关资产的项目/链上/质押缺口说明。
- 若 7d/30d、链上、解锁或宏观数据抓取失败，但 24h 价格/盘口/现金可验证，最多只能输出 `action_report_allowed`，且必须降低金额或使用限价等待；不能输出 `full_near_term_deploy`。
- 若用户提供盘前价但系统无法独立验证，必须标记为 `user_stated_premarket_price`；可以作为情景输入，但不能作为唯一依据触发主仓追高。

该 gate 的目的不是让报告永远卡住，而是避免“数据短板很大时仍然显得很确定”。如果缺口只影响把握程度，输出条件计划；如果缺口影响价格/现金/流动性，输出等待触发或状态报告。

`Full-Market Deep Analysis Objective Gate` 必须在 fresh snapshot 之后执行，至少回答：

- 本次全盘数据是否支持美股战术资金继续追求月度收益目标。
- 本次全盘数据是否支持 crypto/DCA 增强 5-10 年 10x 目标概率。
- 现金应该进入哪个通道、等待什么触发价或事件、最多等待多久。
- 哪些资产因为数据质量、流动性、宏观或 thesis 风险而不应该新增。
- 本次最大允许动作是 `execute_now / conditional_action / watch / no_deploy / hold` 中的哪一种。

如果数据已经刷新但无法完成上述目标映射，报告必须把动作降级，不能把“行情摘要”包装成投资策略。

## Required Data Domains

### Portfolio And Cash

- 当前持仓数量、成本口径、质押状态、已成交/未成交订单。
- Crypto rail 与 US equity rail 分开确认现金和可用资金。
- 用户口述现金可以记录，但必须标记 `user_stated_not_broker_or_exchange_verified`。

### Crypto Market

- 当前持仓：ETH/lcETH、SOL、ADA、NIGHT、BTC/USDT。
- 动态候选：由市场数据、成交量、资金流、新闻、链上和 active handoff 决定，不固定币种长表。
- 至少尝试获取：实时价格、24h/7d/30d、成交量、价差、盘口深度、资金费率、OI、交易所流动性、链上/DeFi、项目公告、关键人物情报、市场情绪。
- Binance/CoinMarketCap/CoinGecko/DeFiLlama/项目官方源是优先通道；无法获取时必须写明缺口和降级。

### US Equity Market

- 当前美股持仓、可卖状态、现金/settled cash/buying power、当前战术仓。
- 大盘与风格：SPY、QQQ、IWM、SOXX/SMH、VIX、DXY、10Y/2Y、半导体和 crypto equity 风险偏好。
- 动态候选：market movers、unusual volume、relative strength、news catalysts、earnings surprises、sector rotation、active handoff。
- 候选必须与当前战术仓比较，说明为什么更好或为什么不动。

### Macro And Sentiment

- 宏观：利率预期、美元、美债、CPI/PCE、风险偏好、ETF/基金流。
- 情绪：Fear & Greed、资金流、社交/关键人物、官方公告、新闻热度、价格/成交量反应。
- 社交和新闻不能单独触发实盘，只能改变观察优先级、DCA 节奏或风险提醒。

## Freshness Rules

| 数据 | 目标时效 | 超时处理 |
|---|---:|---|
| 现货价格、盘口、价差 | 15 分钟内 | 禁止即时强动作 |
| 24h stats、funding、OI | 60 分钟内 | 降级为 conditional/watch |
| 7d/30d/90d 趋势 | 24 小时内 | 标记 degraded |
| 新闻/公告/社交 | 战术 72 小时内；长期使用需标注日期 | 单源或过期只 watch |
| 宏观与资金流 | 最新可得发布 | 影响 DCA 节奏，不单独强买入 |

如果当前环境无法联网或 API 被限流，报告必须直接说明“本次没有完成 fresh refresh”，不能用旧报告结论冒充实时判断。

## Strict Latest Data Rule

用户已经把默认要求升级为“每次都必须用最新数据，不能降级”。因此，任何涉及当前资金部署的建议必须满足以下硬条件：

- 本轮运行内重新抓取核心行情，不得沿用上一轮报告、旧快照或截图价格。
- Crypto 当前操作建议至少需要本轮的价格、24h 统计、7d/30d 趋势、成交额、价差/盘口和主要交易对可用性。
- 美股当前操作建议至少需要本轮的现价、盘中/近 5-20 日趋势、成交量、相关大盘/板块状态和当前可用资金状态。
- 如果核心行情 fresh refresh 失败，输出 `no_current_action_due_to_missing_latest_data`，而不是 `conditional_action`。
- Reddit、社交、部分 fund-flow 等补充源失败时，可以作为“补充缺口”披露；只要核心行情和决策必需源是最新且一致的，不必阻断 DCA，但不得使用旧补充数据来支持买卖。
- 缓存只允许作为“同一轮调度刚刚生成的运行产物”使用；跨轮缓存不得支持当前买入/卖出/DCA/回接区间。

`scripts/manual_dispatch_run.py` 的正式手动调度默认使用严格刷新：非 `--smoke` 模式会自动把 `--require-fresh-market-intelligence` 传给报告生成器。

当用户明确允许“先要可读降级报告”，可以显式使用：

```text
--allow-degraded-market-intelligence
```

该模式下仍必须展示缺口和降级影响，且不得输出新的 `execute_now`。

底层报告生成器的严格参数为：

```text
--require-fresh-market-intelligence
```

该模式下如果使用缓存、跳过 active scanner、核心市场源失败、缺失 crypto/美股任一侧 fresh source，或任一核心输出内部标记 `missing_or_degraded / degraded / disputed / stale`，脚本应直接失败，而不是生成降级报告。普通手动报告仍可生成降级版，但必须标记 `market_intelligence_degraded=true`。

注意：只看到“脚本执行成功”不等于 fresh refresh 成功。报告生成器还必须读取各步骤产出的内部数据质量，例如 portfolio public source errors、macro missing component count、crypto 多源交叉验证缺失、美股 scanner degraded/no actionable trade。只要这些核心内部质量项降级，Fresh Market 面板也必须降级。

内部数据缺口分两层：

- `internal_degraded_sources`：核心数据缺失，例如主要价格源、核心 crypto 交叉验证、宏观核心组件、美股扫描完全不可用。这会触发 `market_intelligence_degraded=true` 并阻断严格 fresh 报告。
- `supplemental_degraded_sources`：补充数据缺口，例如 Reddit 403、非核心小币缺少 CoinGecko 校验、部分 Yahoo screener 超时、Fed/PCE/fund-flow 补充项缺失。这些必须展示在报告里，但不一定让整个 fresh 报告失败；它们会降低相关资产或相关模块的动作等级。

## Output Panel

报告必须新增 `Fresh Market Intelligence Panel`，至少包含：

| 字段 | 说明 |
|---|---|
| `snapshot_id` | 本次取数快照 ID |
| `captured_at` | 取数时间 |
| `asset_scope` | 本次覆盖的持仓和候选 |
| `sources_attempted` | 尝试的数据源 |
| `sources_succeeded` | 成功的数据源 |
| `source_preflight_summary` | 数据源预检摘要：哪些源可用、哪些关键类别缺失 |
| `market_sentiment_summary` | 情绪摘要 |
| `macro_summary` | 宏观摘要 |
| `crypto_market_summary` | Crypto 量价/流动性摘要 |
| `us_equity_market_summary` | 美股走势和候选摘要 |
| `objective_strategy_mapping` | 全盘数据如何映射到月度战术收益目标与 5-10 年长期目标 |
| `missing_or_failed_sources` | 缺失或失败来源 |
| `internal_degraded_sources` | 脚本运行成功但内部数据质量降级的来源 |
| `supplemental_degraded_sources` | 补充数据缺口，不一定阻断全局 fresh，但必须说明影响 |
| `downgrade_effect` | 对行动等级的影响 |

## Decision Rules

- 旧结论必须经过新数据挑战，不能因为上一轮看好而继续推荐。
- 数据质量有否决权：关键价格、现金、流动性、质押/解锁或候选证据缺失时，不能输出新的 `execute_now`。
- 长期目标优先：DCA 需要说明是否提高 5年/10年 10x 概率。
- 短期目标优先：股票、ETF、杠杆 ETF 与 crypto 战术建议需要说明是否帮助已确认战术资金接近月度 ROI `100%` 进攻目标，同时不得因目标差距而降低质量和风险门槛。
- 目标映射必须优先于资产偏好：如果某个标的很热门但不能解释如何服务月度或长期目标，只能进入观察；如果没有足够高质量机会，允许输出“不动/等待触发”。
- 情绪数据只作为辅助：情绪强但价格/流动性/风险门不支持，最多 `watch`。
- 如果没有高质量机会，最优动作可以是“不动/等待触发/保留现金 1-2 个交易日”，但必须写明等待什么信号。

## Plain Term Notes

- `fresh refresh`：本次报告重新抓取数据，不直接沿用旧报告。
- `market sentiment`：市场情绪，指资金热度、恐慌/贪婪、社交和新闻反应。
- `OI`：未平仓合约量，用来观察衍生品拥挤程度；它不是现货买入理由。
- `funding`：资金费率，用来观察多空拥挤；过热时反而可能提高回调风险。
- `degraded`：数据不完整，只能小心使用，不能当成强信号。
