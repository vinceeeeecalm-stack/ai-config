# Position First Recommendation Flow

本流程规定：每次执行手动交易推荐或 DCA 分析时，必须先分析当前持仓状态，再推荐 DCA 交易对，最后才输出完整执行计划。

## 运行顺序

固定顺序如下：

1. `Portfolio Comparison Panel`：当前/昨日/7日/30日持仓水平对比。
2. `Position Gate`：当前持仓状态。
3. `Goal Gap Gate`：5年/10年 10x 缺口、资产贡献标签和复利后所需价格倍数。
4. `Macro Regime Gate`：宏观环境只决定 DCA 节奏，不直接触发单币强买入。
5. `Asset Micro Thesis Gate`：只对本次相关资产做微观评分。
6. `Staking Compounding Gate`：可质押资产必须计算 5年/10年复利效果。
7. `Missing Data / Downgrade Gate`：缺失数据必须映射到建议降级。
8. `DCA Pair Gate`：推荐 DCA 交易对。
9. `Action Plan Gate`：本期可执行动作。
10. `Risk Gate`：风险否决、降级和失效条件。
11. `Review Gate`：复盘时间和下一轮数据要求。

不得跳过 `Portfolio Comparison Panel` 和 `Position Gate` 直接输出买入建议。

## Portfolio Comparison Panel

每次报告开头必须用表格展示：

- Current / 1D / 7D / 30D 的总资产变化。
- Crypto rail / US equity / cash / staked 的价值变化。
- ETH/lcETH、SOL、ADA、NIGHT、CRCL、SOXL、COIN 的主要贡献。
- 数据口径：是否假设持仓数量不变，历史价格是否估算。

如果数据不完整，仍需输出表格，但必须标注 `estimated`、`stale` 或 `missing`。

## Position Gate

每次报告必须先输出以下内容：

| 字段 | 说明 |
|---|---|
| total_portfolio_value | 总资产估值，注明 API proxy / screenshot / ledger 口径 |
| crypto_value_pct | Crypto 占比 |
| us_equity_value_pct | 美股占比 |
| crypto_cash_or_stablecoin | crypto rail 可用稳定币/现金 |
| us_equity_cash | us equity rail 可用现金，若未知必须标记 missing |
| staking_status | lcETH/ETH、ADA、SOL 等质押、APY、奖励、解锁或流动性 |
| overweight_assets | 超配资产，例如 ETH/lcETH 过重 |
| underweight_assets | 低配资产，例如 SOL 增长仓不足 |
| blocked_assets | 被风险模型 block 或 no_deploy 的资产 |
| data_quality | verified / disputed / stale / missing |

如果持仓账本损坏、总资产无法计算或现金通道缺失，报告可以继续输出观察，但不得输出强交易建议。

## DCA Pair Gate

DCA 交易对必须在持仓状态之后输出。

Crypto rail 交易对不使用固定推荐顺序。每次必须根据当前持仓、目标函数、数据质量、长期 thesis、市场情绪和现金通道动态生成候选。常见角色如下：

| 角色 | 可能交易对 | 默认处理 |
|---|---|---|
| growth_engine | SOLUSDT 或其他通过长期评分的增长主线 | 低配、回调、数据 verified 时优先 |
| long_duration_convex | NIGHTUSDT / NEARUSDT / SUIUSDT / LINKUSDT / TRXUSDT 等 | 长期 thesis 高时可小额 DCA，不因短期 no_deploy 永久否决 |
| staking_satellite | ADAUSDT 或其他可质押资产 | 计入复利，但避免只因 APY 过度加仓 |
| liquidity_anchor | BTCUSDT | 仅极端低估、系统性恐慌或需要流动性锚时启用 |
| existing_core | ETHUSDT / lcETH proxy | 超配时不新增，只跟踪 staking、解锁和估值 |
| opportunity_cash | USDT | 回调、数据冲突或等待更好入场时使用，不长期闲置 |

推荐 DCA 交易对时必须给出：

| 字段 | 说明 |
|---|---|
| primary_pair | 本期第一推荐交易对 |
| secondary_pair | 第二推荐交易对 |
| satellite_pair | 长期高凸性或机会候选，如适用 |
| avoid_pairs | 本期不推荐或暂停交易对 |
| amount_usd | 本期建议金额 |
| dynamic_weight_pct | 本期建议权重和较上期变化 |
| split_plan | 分几笔买入 |
| buy_now_amount | 现在可执行金额 |
| trigger_prices | 后续触发价格 |
| invalidation | 失效条件 |
| confidence_reason | 推荐理由，不得只写分数 |
| data_quality_summary | 数据来源、时间戳、verified/degraded/disputed/stale/missing |
| goal_impact | 为什么该分配提高 5年/10年 10x 目标达成概率 |
| goal_gap_contribution | accelerator / neutral / drag / tail_convexity |
| macro_regime_dca_pace | accelerate / normal / split_more / wait_for_pullback |
| micro_thesis_score | 价格位置、链上/生态、供应、流动性、资金流和风险评分 |
| staking_compounding_summary | 5年/10年复利倍数和达到 10x 所需价格倍数 |
| missing_data_downgrade | no impact / smaller size / conditional only / block |

DCA 报告只比较本次真正影响推荐、观察、减仓或风险判断的标的。不得硬性输出固定代币长表。

## Action Plan Gate

行动清单必须按人工可执行顺序输出：

1. 可立即执行。
2. 条件触发执行。
3. 只观察或 paper。
4. 本期明确不做。

每条行动必须包含：

- `action_id`
- `action`
- `pair_or_symbol`
- `amount_or_quantity`
- `rail`
- `trigger`
- `stop_or_invalid`
- `take_profit_or_review`
- `forecast_probability_pct`
- `execution_readiness_score`
- `risk_decision`
- `human_confirmation_required`

## Risk Gate

风险模型可以否决或降级 DCA 推荐。

必须检查：

- 现金是否在正确 rail。
- 质押资产是否被错误当作可用现金。
- ETH/lcETH 是否继续超配。
- SOL 是否出现链上/流动性/监管/生态风险。
- ADA 是否只是因为 APY 而被过度加仓。
- ADA 若处于深度折价、数据 verified 且未超配，是否应允许上调到 15%-22% crypto target；若生态/TVL/开发者/资金流弱于 SOL，必须保持卫星定位。
- 卫星仓是否未通过长期 thesis、数据质量和流动性检查却被当作主仓。
- NIGHT 等高凸性资产是否被短期评分永久否决，或因单日上涨被强行加仓。
- BTC 是否被误当成默认 DCA 主线。
- 数据是否缺失、过期或冲突；若是，推荐必须降级。
- 宏观、微观、质押、解锁或链上数据是否缺失；若是，必须输出 Missing Data / Downgrade Panel。

## Review Gate

报告结尾必须说明：

- 下一次复盘时间。
- 哪些价格或事件会触发重新调度。
- 哪些数据缺口需要补齐。
- 是否需要更新账本、质押收益或现金通道。

## 默认输出骨架

```text
1. 当前持仓状态
2. 本期 DCA 交易对推荐
3. 本期可执行操作
4. 条件触发操作
5. 不推荐/暂停操作
6. 长期 DCA 路径影响
7. 风险与失效条件
8. 下次复盘
```
