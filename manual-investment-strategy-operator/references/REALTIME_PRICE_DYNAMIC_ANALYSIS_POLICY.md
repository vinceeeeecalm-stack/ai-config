# Realtime Price And Dynamic Analysis Policy

## Purpose

本 policy 把“实时价格、充分数据、动态分析触发、目标导向仲裁”变成手动报告的硬门槛。它适用于所有当前买入、卖出、DCA、回接、限价和美股战术轮动建议。

核心原则：

- 当前操作必须先有本轮抓取的实时或准实时价格，不得用截图价、旧报告价或上一轮缓存价替代。
- 如果价格源、盘口、成交量或现金通道不足，报告只能输出状态、等待触发或条件观察，不能给主动作。
- 当普通数据不足以判断时，系统必须触发更多动态分析流程，例如美股开盘扫描、crypto 多源快照、宏观快照、社交/关键人物情报、链上/DeFi 或 research committee，而不是凭单一价格给结论。
- 最终建议必须由目标导向仲裁：美股战术服务月度收益目标，crypto DCA 服务 5-10 年 10x 目标，美股长期仓服务长期保护和低估配置。

## Realtime Price Certification Gate

每条涉及当前价格的行动建议前，必须先生成 `current_price_certification_panel`。

必填字段：

| 字段 | 说明 |
|---|---|
| `symbol` | 标的代码或交易对 |
| `action_scope` | buy / sell / dca / re_entry / limit_order / watch |
| `market_session` | premarket / regular / afterhours / crypto_24_7 / closed |
| `price` | 当前价格 |
| `bid` / `ask` | 如可得，必须记录盘口 |
| `last_quote_at` | 价格来源时间戳 |
| `sources_attempted` | 本轮尝试的数据源 |
| `sources_confirmed` | 成功的数据源 |
| `source_disagreement_pct` | 多源价格差异 |
| `certification_status` | verified / realtime_single_source / user_stated_only / stale / missing / disputed |
| `max_allowed_action` | execute_now / conditional_action / watch / no_current_action |
| `plain_language_note` | 用直白中文说明这个价格是否足以支持当前操作 |

### US Equity Source Cascade

美股现价按以下顺序尝试：

1. 券商或用户授权的实时行情源，如未来接入。
2. TwelveData `quote`，需包含 `is_market_open`、`last_quote_at` 和最新价。
3. Finnhub `quote`。
4. Yahoo quote/chart，如可用。
5. Alpha Vantage `GLOBAL_QUOTE`，主要用于上一交易日或延迟行情确认。
6. Stooq，用于正式收盘 OHLCV 或延迟行情，不得单独支持盘中主动作。
7. 用户口述盘前/盘中价只能标记为 `user_stated_only`，可作为情景输入，不得单独支撑主仓追高。

美股当前买入/卖出必须至少满足：

- `certification_status` 为 `verified` 或 `realtime_single_source`。
- 若只有单一实时源，动作上限默认是 `conditional_action`，除非另有券商或第二源确认。
- 若只有 Stooq/Alpha Vantage 昨收，不得给当前入场价，只能给触发公式和下一次刷新要求。

### Crypto Source Cascade

Crypto 现价按以下顺序尝试：

1. Binance spot ticker + order book/depth。
2. CoinMarketCap 或 CoinGecko。
3. 其他交易所、项目源或 DeFiLlama 补充。
4. 对 NIGHT 这类较新资产，必须额外确认交易对存在、24h quote volume、价差和盘口深度。

Crypto 当前 DCA/回接必须至少满足：

- 本轮 spot price + 24h stats 成功。
- 本轮盘口或价差成功。
- 现金通道和可用 USDT 已确认或标记为用户口述。
- 若 7d/30d、链上、解锁或项目消息缺失，最多输出 `conditional_action` 或 `smaller_size`，不得 `full_near_term_deploy`。

## Dynamic Analysis Trigger Matrix

当触发以下条件时，手动 skill 必须启动或请求相应动态分析流程；若工具不可用，则必须在报告中标记缺口和动作降级。

| 触发条件 | 动态流程 | 目的 | 最大动作 |
|---|---|---|---|
| 美股战术现金空置超过 1 个交易日 | `active-alpha-paper-monitor/scripts/us_open_dynamic_scanner.py` | 动态发现 1-3 个高回报候选 | conditional_action |
| SOXL/APLD/IREN 等候选快速拉升或快速回撤 | US tactical candidate deep dive | 判断追随、小仓、回踩或放弃 | conditional_action |
| 已持有美股战术仓相对成本回撤 `>=8%`，或近期高点回撤 `>=15%` | Tactical Drawdown Sentinel + news/filing scan | 先判断是否需要止损、减仓或修复，避免继续忽略亏损仓 | trim_review / conditional_action |
| 已持有高波动 AI/data-center/crypto-equity 单股出现放量破位、融资/增发/债务/项目交付风险 | Tactical Drawdown Sentinel + catalyst verification | 判断是否为 thesis 破坏或重新定价，不得只当普通波动 | exit_review_or_watch |
| 美股候选来自消息面或单日异动 | social/news/catalyst verification | 区分已确认事实、传闻和价格反应 | watch / conditional_action |
| Crypto 24h 跌幅超过 3% 或 Fear & Greed 进入 Extreme Fear | `active-alpha-paper-monitor/scripts/run_multisource_alpha_snapshot.py` | 判断是否加速 DCA、是否等待更深回撤 | conditional_action |
| NIGHT、SOL、ADA 出现回接/DCA 决策 | Binance depth + multi-source crypto check | 核对价格、盘口、成交量、流动性 | conditional_action |
| 项目/关键人物/官方消息影响 crypto thesis | `active-alpha-paper-monitor/scripts/social_key_person_monitor.py` | 判断路线图、解锁、安全、监管或叙事变化 | watch / risk_alert |
| 同一报告同时涉及美股战术与 crypto DCA | `scripts/macro_regime_snapshot.py` | 给风险偏好和 DCA 节奏提供宏观背景 | conditional_action |
| 多个候选分歧或旧结论被挑战 | `scripts/research_panel_runner.py` 或外部 subagent | 投资委员会式仲裁 | watch / conditional_action |
| 短线候选要升级为更高动作等级 | paper/walk-forward/recommendation calibration | 检查历史样本和胜率 | execute_now_candidate only if gates pass |

动态分析流程必须遵守被动边界：只在用户本次调度内运行，不后台循环，不下单，不转账。

## Objective-Oriented Arbiter

主 agent 在所有数据和动态流程之后，必须做目标导向仲裁。

### US Tactical Sleeve

目标：优先让动态可部署战术资金池朝月度 ROI `100%` 进攻目标前进，同时保持长期资金、保护仓和 DCA 独立。

报告必须回答：

- 当前现金是否已经产生机会成本。
- 是追随当前强势、等回踩、切入其他候选，还是继续现金等待。
- 每个候选在 1-5 个交易日和 6-10 个交易日如何验证。
- 候选是否明显优于当前可用战术选择。
- 低于双 80 时，必须标为 `conditional_action / small_probe_review / watch`。

### Crypto Long-Term DCA

目标：提高 5-10 年 10x 概率，而不是追短线涨跌。

报告必须回答：

- 本轮投入强度：`no_extra_deploy / normal_dca / accelerated_dca_light / accelerated_dca_medium / full_near_term_deploy`。
- 是否值得加大当期投入，还是保留稳定币等更深回撤。
- SOL/ADA/NIGHT/ETH/lcETH/BTC 对目标是 accelerator、satellite、tail_convexity、quality_hold 还是 drag。
- 可质押资产的时间在场、复利、锁定和机会成本是否改变买入节奏。

### Long-Term US Equity

目标：保留长期高质量仓，只有长期低估或 thesis 破坏才调整。

报告必须回答：

- CRCL/COIN 等长期相关仓是否属于保护仓、补充流动性还是战术候选。
- 长期仓不得因为短线现金焦虑被随意卖出。
- 若出现长期低估，只输出 `exceptional_add_signal`，并与短线战术资金分开。

## Report Output Requirements

每次正式报告必须包含：

- `Realtime Price Certification Panel`
- `Dynamic Analysis Trigger Panel`
- `Objective-Oriented Arbiter Panel`
- `Short-Term US Tactical Action`
- `Long-Term Crypto DCA Action`
- `Missing Data / Downgrade Panel`

如果本轮没有足够实时数据，报告开头必须直说：`no_current_action_due_to_missing_latest_data`，并列出下一步需要刷新哪个源。不要用旧价给“看起来精确”的入场/出场价。

## Plain Term Notes

- `实时价格认证`：确认这次报告使用的是本轮抓到的当前价格，而不是旧价格。
- `动态分析触发`：当普通数据不够时，自动加跑更细的扫描或研究流程。
- `目标导向仲裁`：最后不是看哪个资产热门，而是看哪个动作更接近你的短期或长期目标。
- `conditional_action`：可以人工确认后按条件执行，但不是高把握强买入。
