# Full-Market Deep Analysis Objective Gate

## Purpose

本 gate 定义：每次手动调度报告时，系统不仅要刷新市场数据和情绪，还必须把这些数据转换成目标导向的策略判断。

它仍然属于 `manual-investment-strategy-operator`，所以只在用户主动调度时运行；不后台轮询、不自动下单、不自动转账。

## Required Questions

每次报告必须回答：

1. 美股/战术资金是否存在足够高质量的月度收益机会。
2. Crypto/DCA 是否提高 5-10 年 10x 目标概率。
3. Crypto rail 与 US equity rail 分别有多少已确认现金，是否应该投入、等待触发或保持机动。
4. 哪些候选不能新增，原因是数据质量、流动性、估值、宏观、质押/解锁、现金通道或 thesis 风险。
5. 本轮最大允许动作是 `execute_now / conditional_action / watch / no_deploy / hold` 中的哪一种。

## Successful Dispatch Definition

一次合格的手动调度不是“抓到价格”就结束，而是必须同时满足：

- 已刷新本次市场数据和情绪数据，或明确列出失败来源。
- 已用全盘数据重新挑战上一轮结论。
- 已把美股战术资金映射到月度收益目标。
- 已把 crypto/DCA 映射到 5-10 年长期收益目标。
- 已给出明确操作结论、等待触发条件、不做事项和复盘时间。

若无法满足这些条件，本轮最大动作必须降级为 `conditional_action / watch / no_deploy / hold`。

## Inputs

- `fresh_market_intelligence_snapshot`
- 当前持仓、成本、质押、现金通道
- 5年/10年目标差距
- 战术资金月度 ROI 100% 进攻目标进度与季度报告检查点
- Crypto 多源价格、走势、成交量、流动性、funding/OI、链上/DeFi、项目与关键人物消息
- 美股大盘、行业轮动、动态候选、消息催化和当前战术仓
- 宏观、资金流与市场情绪
- 数据质量、缺失数据和降级原因

## Output Rules

报告必须包含 `Full-Market Deep Analysis Panel`，并用直白中文输出：

- `月度战术收益目标`：当前战术资金应继续持有、换仓、等待回踩、等待突破，还是不部署。
- `5-10年长期目标`：当前 DCA 应加速、正常分批、等待回调，还是暂停新增。
- `现金通道`：两路资金是否已确认，是否允许使用，最多等待多久。
- `最大允许动作`：本轮 action ceiling。
- `不应该做什么`：哪些看似诱人的动作会偏离目标或绕过风控。

## Decision Rules

- 数据摘要不等于策略判断；只列价格、新闻和情绪不算通过。
- 每条主建议必须说明它如何服务月度收益目标或 5-10 年长期目标。
- 情绪、社交、单日涨幅不能单独提升动作等级。
- 缺少目标映射时，不得输出新的 `execute_now`。
- 如果没有高质量机会，可以明确输出 `hold / watch / no_deploy / conditional_action`。

## Plain Notes

- `action ceiling`：本轮允许的最大动作等级。它不是收益预测，而是风控后的操作上限。
- `目标映射`：把数据翻译成“这是否帮助你更接近月度或长期目标”。
- `cash rail`：资金所在通道。Crypto 软件里的现金和美股券商里的现金不能默认互通。
