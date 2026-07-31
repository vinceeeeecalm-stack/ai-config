# Impulse Capture Engine Policy

## 目的

`impulse_capture_engine` 用来捕捉 NIGHT 式的短时拉升、放量突破和回踩再上攻。它不是情绪判断器，而是市场微结构扫描器：先看价格、成交、盘口和跨资产环境，再把新闻/社交作为辅助解释。

当前版本只允许输出 `watch / paper_only / conditional_action`。它不自动真实下单，不调用私有交易 API，不撤单，不移动真实资金。

## 能捕捉什么

| 阶段 | 含义 | 典型信号 | 最高动作 |
|---|---|---|---|
| `early_watch` | 异动刚出现 | 5m 成交额达到滚动中位数 3-5 倍，主动买入占比升高，价格守住短线支撑 | `watch` |
| `pre_breakout` | 上攻前的蓄力 | 5m 成交额 8-10 倍以上，1m/5m 高低点抬高，卖盘深度变薄，BTC/ETH 不拖累 | `paper_only` |
| `trigger` | 突破触发 | 收盘站上关键阻力，1m 或 5m 成交额极端放大，主动买入占比继续高于 60%-65% | `paper_only` 或 `conditional_action` |
| `post_trigger_validation` | 突破后验证 | 3-5 根短周期 K 线不跌回突破位，量能没有迅速枯竭 | `conditional_action` |
| `fakeout_or_exhaustion` | 假突破/衰竭 | 跌回突破位、主动买入占比转弱、放量下跌、盘口卖压恢复 | `risk_alert` 或 `no_deploy` |

## Crypto 特征

动态发现进入异动排序前必须先执行资产资格过滤：稳定币/法币基准对与
`UP/DOWN/BULL/BEAR` 杠杆代币不得成为上涨候选。该过滤必须覆盖新出现的
稳定币代码（例如 `RLUSD/USDT`），并由 scanner self-test 做回归验证；否则
本轮动态扫描标记为 degraded，稳定币信号只能作为市场结构异常，不得进入
机会排名。

动态成交榜中的每个候选还必须通过 `exchangeInfo` 的
`TRADING + isSpotTradingAllowed` 资格确认。仅有 ticker/行情但无法确认现货交易
资格的代币化证券、失效交易对或异常代码必须写入淘汰审计，不能进入 Crypto
机会排名。动态池还要用公开资产身份目录交叉检查；一旦同 symbol 出现 ETF、
公司股票、Backpack Securities、Robinhood Token 或 tokenized equity 身份，
即使交易所行情端点可用，也从 Crypto 候选中排除并记录身份匹配。公共主端点
出现 418、地域或限流错误时，可按固定顺序回退到 Binance
官方公共数据镜像；报告必须保存每个端点的成功/失败次数，不能把回退隐藏成
单一数据源成功。

每个 crypto 候选至少计算：

- `return_1m_pct / return_5m_pct / return_15m_pct`
- `quote_volume_1m / quote_volume_5m / quote_volume_15m`
- `volume_multiple_vs_median / volume_multiple_vs_average`
- `taker_buy_quote_ratio`
- `trade_count_multiple`
- `bid_ask_spread_bps`
- `depth_1pct_bid_usd / depth_1pct_ask_usd`
- `depth_2pct_bid_usd / depth_2pct_ask_usd`
- `near_support / near_resistance / breakout_level`
- `btc_eth_sol_bnb_anchor_state`
- `social_or_news_freshness_status`

若候选来自新链或 DEX-only 市场，还必须运行
`NEW_CHAIN_EVENT_TO_ASSET_DISCOVERY_POLICY.md`，并补充：

- `chain_id / contract_address / pair_address / pair_created_at`
- `dex / quote_asset / affiliation_status`
- `unique_traders / buys / sells / volume_to_liquidity`
- `holder_and_deployer_concentration`
- `honeypot_tax_owner_lp_status`
- `catalyst_derivation_chain`

Binance 缺少交易对不得让资产从 observation pool 消失。无法获得可靠
闭合 K 线或安全/持仓证据时最高为 `watch`，不能套用 CEX 的触发概率。

## 美股特征

美股开盘窗口可以复用该机制，但必须经过 `US_OPEN_DYNAMIC_SCANNER_POLICY.md` 与 manual skill 二次审查。美股侧重点：

- 分钟级成交量相对均值倍数
- 盘前/开盘跳空是否被承接
- 行业 ETF 或大盘锚点是否支持
- bid/ask 价差和成交量是否足够
- 新闻/财报/订单/政策催化是否确认
- 当前战术仓是否真的弱于新候选

## 假突破过滤

出现以下任一情况，必须降级：

- 突破后 3-5 根短周期 K 线重新跌回触发价。
- 成交额放大但主动买入占比低于 45%，说明放量可能来自卖压。
- bid/ask spread 过宽，或 1% 深度无法承接计划仓位。
- BTC/ETH/SOL/BNB 锚点同步转弱。
- 社交消息单源、过期、搬运或身份未验证。

## 概率和动作约束

- `impulse_score_points` 只是异动强度，不是预测概率。
- 只有积累足够历史样本、paper 命中记录和当前多源确认后，manual skill 才能把某个动作升级为真实操作草案。
- `conditional_action` 表示“人工确认后可考虑”，不是自动买入。
- 如果缺少盘口、成交额或 K 线新鲜数据，最高只能 `watch`。
- 每个结果必须同时输出 `execution_action` 与 `observation_action`。前者受现金、研究和事件门控约束；后者只描述是否需要盯盘或 paper 验证。`execution_action=no_deploy` 不得自动把后者变成“无信号”。
- 若候选同时出现超卖/事件冲击、负 funding、OI 快速上升，应输出 `squeeze_watch` 和精确闭合 K 线触发，即使真实现金为零。funding/OI 仍不能直接授权交易。
- 新异动发生在旧报告之后时，必须创建新的 `impulse_id / triggered_at / baseline_recommendation_id`；原推荐的价格、概率和动作保持冻结，不得事后改写。
- 量价触发只能使用已闭合的 1m 数据和最新完整 5m 桶；突破阻力必须在该 5m 桶开始前由更早数据确定。尚未闭合的最新 K 线不得进入触发计算。

## 报告字段

每次输出应包含：

- `impulse_id`
- `symbol`
- `stage`
- `current_price`
- `support_zone`
- `breakout_level`
- `invalidation_level`
- `volume_multiple`
- `taker_buy_ratio`
- `spread_bps`
- `depth_1pct_usd`
- `anchor_state`
- `social_news_status`
- `recommended_max_action`
- `execution_action`
- `observation_action`
- `observation_trigger`
- `candidate_event_coverage_status`
- `official_event_status`
- `requires_event_relay`
- `triggered_at`
- `historical_baseline_mutation_allowed=false`
- `why_now`
- `what_would_invalidate`

术语备注：

- `主动买入占比`：成交中更像主动吃卖盘的比例，越高越说明买方更急。
- `盘口深度`：当前价格附近能承接多少买卖金额，深度薄时更容易暴涨暴跌。
- `假突破`：价格短暂冲过阻力后很快跌回去，通常说明追高资金被套。
