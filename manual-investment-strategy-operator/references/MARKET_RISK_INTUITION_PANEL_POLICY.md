# Market Risk Intuition Panel Policy

## Purpose

本 policy 用于把复杂市场数据翻译成用户能快速理解的风险提示和市场状态。每次报告不能只给价格、指标和候选；必须先说明“市场现在像什么天气、危险在哪里、对我手上的仓位意味着什么”。

## Required Panels

每次正式报告必须在一页结论之后输出两个短面板。

### 1. Risk Warning Card

用 3-6 行直白中文说明：

- 当前最大亏损风险来自哪里。
- 哪个持仓最需要盯。
- 哪个价格/事件触发减仓、退出或禁止加仓。
- 哪个风险只是观察噪音，不应造成频繁交易。
- 当前最大动作等级：`hold / watch / conditional_action / trim_review / exit_review / no_deploy`。

示例：

```text
风险提示卡：
- 当前 APLD 最大风险不是普通大盘波动，而是高波动 AI 数据中心股在融资/债务/项目兑现压力下被重新定价。
- 它已经从用户成本约 46 跌破 30-32 区间，属于红灯；本轮优先处理亏损仓，不应新增。
- 若不能重新站回 34.5-36.5 并放量，反弹更像修复卖点，不是加仓点。
```

### 2. Market Intuition Map

用表格解释当前市场属于哪类状态。

| 状态 | 直观解释 | 对操作的含义 |
|---|---|---|
| `broad_risk_off` | 大盘一起变弱 | 少追高，优先保护现金 |
| `sector_rotation_out` | 某个赛道退潮 | 个股反弹先看板块是否同步修复 |
| `single_name_repricing` | 个股自身被重新定价 | 不能用“大盘会反弹”解释该股下跌 |
| `financing_debt_pressure` | 融资、债务、资本开支压力被市场担心 | 高波动成长股需要更紧止损 |
| `execution_delivery_risk` | 项目交付、客户或收入兑现有疑问 | 反弹需要基本面确认，不只看技术线 |
| `liquidity_air_pocket` | 流动性突然变薄，价格容易跳水 | 降低仓位，避免市价追单 |
| `momentum_overheat` | 短期涨太快、拥挤 | 追高只能小仓或等待回踩 |
| `panic_discount` | 恐慌砸出折价 | 只在 thesis 未坏时分批买，不一把梭 |

## Risk Sources To Classify

报告必须把风险至少归入以下类别之一：

- `market_beta`: 大盘或利率导致的风险。
- `sector_beta`: 行业/主题退潮，例如 AI、半导体、crypto equity。
- `single_name_fundamental`: 个股自身基本面或估值重定价。
- `financing_dilution_debt`: 增发、可转债、票据、信贷协议、融资成本。
- `execution_delivery`: 项目建设、客户交付、收入兑现。
- `liquidity_volume`: 放量破位、价差变大、成交承接不足。
- `sentiment_positioning`: 过热、拥挤、恐慌、空头挤压。
- `data_quality`: 关键数据缺失或冲突。

## Must Explain "What This Means For You"

每个主要面板后必须加一句：

```text
这对你意味着：...
```

这句话必须连接到用户当前持仓、现金通道、成本价和目标，不允许只写市场评论。

## APLD Example

当 APLD 相对成本跌幅超过 18%、相对近期高点回撤超过 25%，且公司近期存在融资/债务/项目相关 filing 时，报告应该写：

```text
这不是普通大盘波动。QQQ 只小幅回撤，而 APLD 明显跑输，说明市场在重新评估 APLD 自身风险。当前优先级不是寻找新买点，而是决定亏损仓是否修复、减仓或退出。
```

## Plain Language Requirement

禁用只写：

- `risk-off`
- `drawdown`
- `relative weakness`
- `financing overhang`

必须翻译成：

- “市场不愿意买高风险资产”
- “从高点跌下来的幅度”
- “它比大盘和同赛道跌得更多”
- “市场担心融资、债务或增发压住股价”

