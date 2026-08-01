# Portfolio Comparison Panel

每次手动交易推荐和 DCA 报告，开头必须先输出“持仓总览对比面板”。该面板必须在 DCA 推荐之前出现。

## 目标

让用户先看到当前组合水平和近期变化，再判断是否应该 DCA、买什么、买多少。

## 固定输出顺序

报告开头顺序：

1. 总体持仓水平对比。
2. 资产桶对比。
3. 主要持仓对比。
4. 现金与质押状态。
5. 结论：超配、低配、风险、可用 DCA 空间。

然后才进入 DCA 交易对推荐。

## 对比窗口

默认窗口：

| 窗口 | 说明 |
|---|---|
| current | 当前最新 API / market data |
| 1d | 与约24小时前或上一交易日比较 |
| 7d | 与约7日前或最近可用交易日比较 |
| 30d | 与约30日前或最近可用交易日比较 |

如果某一窗口没有完整数据，必须标记 `estimated`、`stale` 或 `missing`，不得伪装成精确数据。

## 必须输出的表格

### 1. 总体持仓水平

| 字段 | Current | 1D | 7D | 30D | 数据状态 |
|---|---:|---:|---:|---:|---|
| Total Portfolio Value | | | | | |
| Crypto Rail Value | | | | | |
| US Equity Value | | | | | |
| Cash / Stablecoin | | | | | |
| Staked / Locked Value | | | | | |
| Liquid Value | | | | | |

### 2. 资产桶占比

| 资产桶 | Current Weight | 1D Change | 7D Change | 30D Change | 结论 |
|---|---:|---:|---:|---:|---|
| ETH/lcETH core | | | | | |
| SOL growth | | | | | |
| ADA staking satellite | | | | | |
| Tactical crypto | | | | | |
| US equity | | | | | |
| Stablecoin/cash | | | | | |

### 3. 主要持仓变化

| 标的 | 数量 | Current Value | 1D P/L | 7D P/L | 30D P/L | 结论 |
|---|---:|---:|---:|---:|---:|---|

至少包含：

- lcETH/ETH proxy
- ADA
- SOL
- NIGHT
- CRCL
- SOXL
- COIN
- USDT / cash

## 对比口径

默认口径：

- 持仓数量来自账本、截图或用户更新。
- 当前价格来自 API 综合数据。
- 历史对比默认假设持仓数量不变，用历史价格重建估值。
- 若用户在窗口内有买卖或转账而账本未更新，必须标记为 `position_quantity_assumed_constant`。

## 数据质量标签

允许标签：

- `verified`
- `estimated`
- `stale`
- `missing`
- `degraded`
- `disputed`

## 结论字段

对比面板之后必须输出结构化结论：

| 维度 | 结论 |
|---|---|
| Portfolio trend | rising / falling / mixed |
| Main driver | 最大正贡献或负贡献 |
| Concentration risk | high / medium / low |
| Cash readiness | ready / low / critical |
| DCA readiness | allow / conditional / wait |
| Rebalance pressure | high / medium / low |

## 与 DCA 的关系

DCA 推荐必须引用对比面板结论，例如：

- 如果 ETH/lcETH 继续超配，禁止新增 ETH。
- 如果 SOL 低配且 7D/30D 回调后仍保持流动性，优先 DCA SOL。
- 如果现金 readiness 为 `critical`，必须提高 USDT 机会仓。
- 如果某卫星仓 1D 暴涨但 30D 仍弱，不得追涨，只能 watch。
