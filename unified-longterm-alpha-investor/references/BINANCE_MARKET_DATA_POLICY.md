# Binance Market Data Policy V2.12

Binance 在 V2.12 中作为 crypto 重点交易所数据源，用于价格、走势、成交量、订单簿深度和流动性校验。当前阶段只读取 market data，不使用下单、提现、账户或私有交易权限。

## 1. 安全边界

- 真实 key 只允许放在本地环境变量 `BINANCE_API_KEY`。
- 不把 key 写入 skill、配置、报告、日志、experiment 或 recommendation history。
- 不使用 Binance 下单、提现、账户余额、交易历史或私有账户 endpoint。
- 如果 key 权限包含交易或提现，当前 skill 仍只能把它当作 market-data key；报告必须提示建议改成只读或无交易权限 key。
- API 自动调仓仍为 disabled。

## 2. 数据优先级

Binance 用于：

| 数据 | 用途 | 要求 |
|---|---|---|
| spot exchange info | symbol discovery | 确认可交易 spot pair |
| spot ticker price | 最新价格 | 与 CMC/CoinGecko/Kraken 交叉校验 |
| 24h ticker stats | 24h涨跌、成交额、成交笔数 | 判断短期趋势与流动性 |
| klines | 1h/4h/1d走势、7d/30d表现 | 不得使用未来K线 |
| order book | bid/ask spread、深度 | 判断可执行性 |
| recent trades | 成交活跃度 | 辅助验证流动性 |

Futures/perpetual 数据只能作为情绪和风险背景，不得支持合约、永续、期货或杠杆交易建议。

## 3. Symbol Discovery

每次涉及 crypto 战术或 NIGHT 时，先检查候选 spot pair：

| 资产 | 默认候选 |
|---|---|
| NIGHT | `NIGHTUSDT`, `NIGHTUSD`, `NIGHTBTC`, `NIGHTFDUSD`, `NIGHTUSDC` |
| BTC | `BTCUSDT`, `BTCFDUSD`, `BTCUSDC` |
| ETH | `ETHUSDT`, `ETHBTC`, `ETHFDUSD`, `ETHUSDC` |
| ADA | `ADAUSDT`, `ADABTC`, `ADAFDUSD`, `ADAUSDC` |
| SOL | `SOLUSDT`, `SOLBTC`, `SOLFDUSD`, `SOLUSDC` |

若主 pair 不存在：

- 标记 `pair_missing`。
- 不得把 Binance 缺失解释为看涨或看跌。
- 继续使用 CoinMarketCap、CoinGecko、Kraken、DeFiLlama、项目公告或其他可验证来源。
- NIGHT 若没有 Binance spot pair，仍保持 block 新增，直到至少两个独立价格/流动性源验证。

## 4. 趋势字段

Binance 面板必须尽量输出：

- `symbol_pair`
- `last_price`
- `price_change_24h_pct`
- `quote_volume_24h`
- `trade_count_24h`
- `trend_1h`
- `trend_4h`
- `trend_1d`
- `return_7d`
- `return_30d`
- `realized_volatility_7d`
- `max_drawdown_30d`
- `data_timestamp`

缺少 7d/30d K线时，不能输出 7d/30d 强结论。

## 5. 流动性字段

每个可交易 pair 必须尽量输出：

| 字段 | 说明 |
|---|---|
| `bid_ask_spread_pct` | 最优买卖价差 |
| `order_book_depth_1pct_usd` | 当前价上下1%深度 |
| `order_book_depth_2pct_usd` | 当前价上下2%深度 |
| `quote_volume_24h` | 24h报价币成交额 |
| `trade_count_24h` | 24h成交笔数 |
| `liquidity_status` | high/normal/below_threshold/low_liquidity/unknown |

默认门槛：

- 24h quote volume >= $1M 且 spread <= 0.5%: 可进入战术候选。
- 24h quote volume $500k-$1M 或 spread 0.5%-1.0%: `below_threshold`，只能小仓/paper。
- 24h quote volume $200k-$500k 或 spread 1.0%-2.0%: `low_liquidity`，不新增。
- 24h quote volume < $200k 或 spread > 2.0%: block 新增，只允许退出/风险降低。
- 数据缺失: `unknown`，不得新增。

## 6. 风险标签

| 标签 | 含义 | 处理 |
|---|---|---|
| `verified` | Binance 与至少一个聚合/交易所源一致，且流动性达标 | 可进入风险模型 |
| `pair_missing` | Binance 无 spot pair | 不作为负面信号，但不得用 Binance 支持行动 |
| `low_liquidity` | 成交额/深度/价差不足 | 禁止新增，只允许退出或观察 |
| `stale` | 数据过期或时间戳异常 | 禁止强建议 |
| `disputed` | 与 CMC/CoinGecko/Kraken 偏差超过阈值 | 降级或 block |

Crypto 价格偏差阈值沿用数据校验策略：<=1% verified，1%-3% disputed_minor，>3% disputed。

## 7. NIGHT 专项规则

NIGHT 在 V2.12 需要单独通过 Binance/多源校验：

- 若 Binance 有 NIGHT spot pair，报告必须显示 pair、K线、成交额、价差、深度、成交笔数。
- 若 Binance 显示低流动性或与其他源冲突，NIGHT 继续 block 新增。
- 若 Binance 验证强趋势和高流动性，也只能先进入 paper 或小仓候选，除非历史校验足以支持 `forecast_probability_pct >= 80%`。
- 未完成多源流动性验证前，NIGHT 不得使用 $3000 战术资金池。

## 8. 报告要求

每份涉及 crypto 战术的报告必须包含 Binance 数据面板：

```markdown
| 资产 | Binance pair | last | 24h% | 24h volume | spread | depth 1% | 1h/4h/1d trend | 状态 | 处理 |
|---|---|---:|---:|---:|---:|---:|---|---|---|
| NIGHT | NIGHTUSDT / pair_missing | $X | X% | $X | X% | $X | up/down/mixed | verified/low_liquidity | no add/paper |
```

