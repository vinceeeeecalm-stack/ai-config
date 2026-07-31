# Staking Valuation and Dynamic BTC Sizing V2.12

本文件定义每次运行必须执行的质押估值、解锁流动性和 BTC 动态仓位模块。它把一次性专项报告升级为主策略逻辑。

## 1. 触发条件

任一条件满足时必须运行本模块：

- 组合持有 ETH/lcETH/LsETH/cbETH、ADA、SOL 或其他可质押资产。
- 报告包含 crypto DCA、BTC 建仓、ETH-like 加仓、质押、解押、再平衡或现金通道建议。
- 质押资产权重超过组合 20%，或单一质押资产超过组合 35%。
- 数据源显示质押 APY、解锁时间、slash、托管、收据代币价格或公开市场深度发生重大变化。
- 用户质疑 BTC 权重、收益型资产权重或现金/稳定币部署顺序。

Daily Monitor 可以只输出异常摘要；Weekly/Monthly/Quarterly 必须输出完整表格。

## 2. 必需数据

质押资产必须尽量取得并标记质量状态：

| 字段 | 说明 |
|---|---|
| `staking_provider` | Coinbase、Ledger、协议、验证者或托管方 |
| `provider_service_type` | liquid_staking_receipt / delegated_self_custody / exchange_staking / defi_protocol |
| `receipt_token_symbol` | lcETH/LsETH/cbETH 等，非收据代币可为空 |
| `underlying_asset` | ETH/ADA/SOL 等 |
| `receipt_conversion_ratio` | 收据代币兑底层资产比例；缺失时不得强估值 |
| `redeemable_underlying_quantity` | 当前可赎回底层资产数量 |
| `account_mark_value` | 用户账户或截图账面价值 |
| `spot_proxy_value` | 底层资产 spot proxy |
| `public_market_proxy_value` | 收据代币公开市场 proxy，必须标记流动性 |
| `estimated_apy` | 当前账户实际 APY 优先于官网区间 |
| `reward_quantity` / `reward_value_usd` | 奖励数量和美元估值 |
| `staking_unlock_delay_days` | 从质押状态转成可卖出/可转稳定币的预估时间 |
| `instant_unstake_fee_pct_if_available` | 若有即时解押/快速退出费用 |
| `slash_risk` / `provider_risk` | 协议、验证者、托管和平台风险 |
| `count_as_immediate_liquidity` | locked/unlocking/unknown 必须为 false |

## 3. 估值口径

估值优先级：

1. 账户导出或官方账户界面的可赎回数量、兑换比例和账面价值。
2. 底层资产多源 spot 价格乘以可赎回底层数量。
3. 收据代币公开市场价格，仅作辅助 proxy；低成交量时必须降级。
4. 用户截图估值可作为持仓权重主口径，但必须标记为 account_mark。

ETH/lcETH/LsETH/cbETH 这类资产不得简单按 `收据数量 * ETH spot` 生成强行动建议，除非兑换比例和可赎回底层数量已验证。

## 4. 解锁与流动性判断

`unlock` 在报告中定义为：从当前质押或收据状态，转成可直接卖出或转成稳定币的实际路径、时间和摩擦成本。

默认处理：

- Coinbase ETH / LsETH-like: 需要验证账户级可赎回 ETH、普通 unstake 等待、instant unstake 是否可用、费用和公开市场深度。
- Ledger ADA: Cardano 委托通常不应视为长期锁定，但仍需考虑链上转账、交易所入账、卖出价差和实际 APY。
- Ledger SOL: 需要考虑约一个 epoch 的 deactivation/cooldown；报告中默认按 2-4 天区间处理，除非账户显示不同。
- 所有质押资产在未完成解锁/到账前，不得用于满足即时现金流动性或短期战术买入资金。

## 5. 收益感知 BTC 动态仓位

BTC 的主角色是分散锚、流动性锚和抗单生态风险锚，不是质押收益引擎。

| 场景 | BTC 目标 |
|---|---:|
| 当前起步或 ETH-like 已明显超配 | 3-5% 总组合 |
| 正常成熟、质押估值和解锁已验证 | 5-8% 总组合 |
| ETH 质押解锁/估值不透明，或市场进入风险规避 | 8-12% 总组合 |
| 硬上限 | 15% 总组合 |

不允许在没有新 thesis 的情况下把 BTC 默认推到大仓位。BTC 到达 3-5% 后，新增 crypto DCA 必须重新比较 ETH-like/SOL/ADA/现金通道的预期收益、质押收益和风险折扣。

## 6. 动态 DCA 顺序

当 ETH-like 已超配且 BTC 为 0 或偏低：

1. 先补 `crypto_rail` 生产性流动性到底线。
2. 用新增资金把 BTC 补到 3-5%。
3. 暂停新增 ETH-like，直到兑换比例、解锁、slash、托管和集中度风险被验证且权重回到合理区间。
4. ADA 若实际 APY 低于 2% 且权重高于 5%，进入降权候选。
5. SOL 若 APY 和网络风险可接受，可在 Risk-On 后从小仓逐步提高。
6. 任何真实 trim、unstake、跨通道转账和策略参数变化都需要人工确认。

## 7. 风险门

直接 `block`：

- 用 locked/unlocking/unknown 质押资产满足现金需求。
- 用未验证的收据代币公开市场价格生成强买卖建议。
- 在 ETH-like 已超配且解锁/估值未验证时继续 DCA 加仓 ETH-like。
- 将 BTC 作为收益最大化仓位大幅提高，且没有风险规避或 dominance thesis。

至少 `downgrade`：

- 收据兑换比例缺失。
- 账户 APY 与官网区间明显不一致。
- 公开市场 proxy 成交量低或价格偏离账户账面。
- 解锁时间长于建议行动窗口。
- 税务 lot 缺失导致减仓/解押摩擦无法估算。

## 8. 报告输出

Weekly/Monthly/Quarterly 报告必须包含：

- 质押估值与解锁面板。
- 账户账面、底层 spot proxy、收据公开市场 proxy 的差异。
- 预估年化质押收益和解锁/即时退出摩擦。
- BTC 当前权重、动态目标区间、金额和数量。
- ETH-like/ADA/SOL 的 DCA、持有、降权或恢复条件。
- 本模块产生的 `recommendation_id`、方向性 `forecast_probability_pct`、`risk_decision` 和 `execution_readiness_score`。

Daily Monitor 只需输出异常：APY 大幅变化、slash/安全事件、解锁失败、价格 proxy 冲突、BTC 目标区间被突破。

## 9. 学习闭环

每次本模块生成建议后，Monthly Review 必须复盘：

- BTC 小锚是否改善组合回撤或流动性。
- 质押收益是否弥补了价格波动和解锁摩擦。
- ADA/SOL/ETH-like 的实际 APY 与报告假设是否偏离。
- 减少/增加 BTC 权重的建议是否发生 timing error 或 position_sizing_error。
- 若连续两期表现恶化，提出回滚 BTC 目标或质押权重参数，但仍需人工确认。
