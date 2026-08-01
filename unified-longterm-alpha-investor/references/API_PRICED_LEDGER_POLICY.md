# API Priced Ledger Policy V2.17

截图、账户导出和手动输入只用于确认数量、成本、账户归属、质押状态和奖励记录。估值、价格、涨跌幅、成交量和执行价格必须由本次运行的 API/交易所/多源价格决定。

## 1. 账本字段优先级

| 字段 | 主来源 | 说明 |
|---|---|---|
| `quantity` | 截图/账户导出/用户确认 | 截图可信度高，作为数量账本 |
| `avg_cost` | 截图/账户导出/用户确认 | 可用于盈亏分析，不用于当前估值 |
| `staking_status` | 截图/账户导出/平台文档 | 用于锁定、解锁、奖励和可流动数量 |
| `current_price` | API 多源 | 不使用截图价格作为主价格 |
| `market_value` | `quantity * api_composite_price` | 当前市值必须由 API 价格计算 |
| `portfolio_weight` | API 估值后的组合总值 | 不用截图市值直接计算权重 |
| `action_price` | API 最新价、限价区间或触发价 | 交易建议必须引用可追溯数据 |

## 2. API 综合价格

Crypto:

- 默认使用 CoinMarketCap、Binance、Kraken、DeFiLlama/CoinGecko proxy 等多源。
- 若多源偏差在阈值内，使用中位数或流动性加权价格作为 `api_composite_price`。
- Binance 可作为交易执行参考价，但不得单源决定强建议。

美股:

- 使用 Twelve Data、Finnhub、Alpha Vantage 或等价价格源。
- 若美股休市，使用最近交易日收盘，并明确日期。

## 3. 截图价格处理

截图价格和截图市值只能作为：

- 数量提取辅助。
- 成本/盈亏展示。
- 与 API 价格对比的 reconciliation item。

如果截图估值和 API 估值偏差明显：

- 不直接覆盖 API 估值。
- 报告必须显示偏差、可能原因和采用口径。
- 强行动建议只使用 API 估值口径。

## 4. 报告要求

每次报告必须显示：

| 标的 | 数量来源 | 数量 | API价格 | API估值 | 截图估值 | 偏差 | 采用口径 |
|---|---|---:|---:|---:|---:|---:|---|
| ADA | screenshot | X | $Y | $Z | $A | X% | API |

如果某标的没有 API 价格：

- 该标的市值标记 `missing_price`。
- 不可输出强买入/卖出。
- 组合总值必须显示为 degraded。

## 5. 行动建议

所有金额建议必须基于 API 估值：

- `$3000` 分配以 API 估值后的组合比例和现金缺口为准。
- 止损、止盈、入场价格使用 API/交易所价格。
- 质押资产若 API 价格可得但解锁信息缺失，仍不得当作即时现金。
