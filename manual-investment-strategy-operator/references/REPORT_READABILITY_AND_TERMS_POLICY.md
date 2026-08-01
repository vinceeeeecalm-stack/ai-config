# Report Readability And Terms Policy

## Purpose

Investment reports must be decision-readable. The user should not need to already know quant, macro, on-chain, or trading jargon to understand the conclusion.

This policy applies to manual reports, subagent summaries, action cards, and handoff explanations that may appear in the final report.

## Core Rules

- Use plain Chinese first. Keep English terms only when they are market-standard labels or API/source field names.
- The first time a technical term appears in a report, add a short note in parentheses.
- Do not let jargon replace judgment. After a technical term, state the practical meaning for this portfolio.
- The final action list must use direct language: buy, wait, hold, reduce, do not deploy, review date.
- If a report includes many technical terms, add a short `术语小注` section near the end.
- Subagent outputs may stay structured, but the arbiter report must translate the key terms before giving the user an action.
- For a single-candidate request, do not lead with portfolio infrastructure, audit status, committee mechanics or action-code jargon. Lead with the asset and the trade decision.
- Mark every decision-driving statement as one of: `事实` (directly sourced), `推导` (calculation shown), or `判断` (uncertain inference). Never use the same declarative tone for all three.
- A probability without a reproducible sample and method is not a measured probability. Present it as a range, label it `主观情景权重`, and explain which new fact would move the range.
- The first screen must contain exact prices and dates. Background detail comes after the complete entry/exit path, not before it.

## Required Short Notes

Use concise notes like these, not long textbook explanations:

| Term | User-facing note |
|---|---|
| walk-forward | 用过去一段训练，再用后面一段验证，防止只适合历史数据 |
| OOS / out-of-sample | 样本外验证，指没有参与调参的数据 |
| base rate | 历史同类机会的大概成功率 |
| lookahead bias | 未来函数，指误用了当时还不知道的数据 |
| EV / expected value | 期望值，综合胜率和盈亏比后的平均预期 |
| drawdown | 回撤，从高点跌下来的幅度 |
| Sharpe | 收益相对波动是否划算的指标 |
| slippage | 滑点，实际成交价比看到的报价更差 |
| spread | 买卖价差，买一和卖一之间的成本 |
| order book depth | 盘口深度，能承接多少买卖而不明显冲击价格 |
| funding | 合约资金费率，反映多空拥挤度；本系统只作情绪参考 |
| OI / open interest | 未平仓合约量，反映杠杆资金参与度 |
| liquidity | 流动性，能否顺利买卖且成本不高 |
| TVL | 链上锁仓规模，粗略看生态资金沉淀 |
| revenue / fees | 协议收入/费用，用来判断真实使用需求 |
| unlock | 代币解锁，未来新增流通可能带来卖压 |
| staking APY | 质押年化收益，通常会变化且不等于无风险收益 |
| front-load DCA | 前置定投，把未来一部分计划投入提前到本期 |
| time-cost risk | 时间成本风险，因为等待而少拿质押收益或错过长期修复 |
| long-term low-value zone | 长期价值低位，指按 5-10 年视角看价格/估值已经有吸引力，不等于短线不会再跌 |
| thesis | 长期投资逻辑 |
| catalyst | 催化剂，可能推动价格变化的事件 |
| risk gate | 风险门，决定建议是否被降级或阻断 |
| execute_now | 可人工确认后立即执行，不代表系统自动下单 |
| conditional_action | 条件行动，等价格或事件触发后再考虑 |
| paper_only | 只做模拟，不动真实资金 |
| no_deploy | 不投入真实资金 |
| research_committee_degraded | 多研究员证据不足，本轮不能升级为强操作 |
| double-80 gate | 双80门槛：真实目标达成概率>=80%，执行准备度>=80分 |
| progressive learning ramp | 渐进学习爬坡：先记录 60%-79% 的学习型建议，靠复盘逐步校准到 80%+ |
| small_probe_review | 小额试探复盘：只表示可小规模观察/条件计划，不代表自动买入 |
| passive dispatch | 被动调度：用户触发一次，系统跑一次完整报告，不后台自动交易 |
| stop_and_report_status | 收口汇报：本轮已经到可用检查点，停止长循环并告诉用户下一步 |
| goal_mechanism_ready | 机制可用：取数、报告、记录、复盘、归因和下一步队列已经能跑 |
| goal_complete | 目标完成：真实财务结果已经证明达到目标，不是单次报告通过 |

## How To Explain Scores

- `forecast_probability_pct` must be described as: “目标时间内达到目标价的真实预测概率，不是热度分。”
- `execution_readiness_score` must be described as: “执行准备度，衡量数据、流动性、止损、资金来源是否齐全，不是上涨概率。”
- `confidence_pct` from subagents must be described as: “研究员对自己判断的把握，不等同于交易成功率。”
- `data_quality` must be described with the consequence: verified can support clearer sizing; degraded/disputed/stale/missing forces smaller size, watch, or block.

## Action List Language

Prefer:

- “不追，等回踩到 X-Y 再评估。”
- “可以放入观察，但不能动真实资金。”
- “这条数据只说明市场情绪偏热，不能单独作为买入理由。”
- “如果到某日期仍未触发，现金最多等待 1-2 个交易日后重新评估。”

Avoid:

- “因 OOS/EV/funding/OI 通过，建议执行。”
- “alpha 显著，risk-adjusted return improved。”
- “该资产 beta 高，convexity 强，因此配置。”

Instead, translate:

- “历史样本外验证还不够，所以只能模拟。”
- “如果方向判断对，潜在收益比亏损空间更划算，但样本不足。”
- “波动弹性大，适合小比例追求高回报，但不能当核心稳健仓。”

## Report Placement

Every report should include:

- Inline notes for uncommon terms at first use.
- A front-loaded `风险提示卡` and `市场直觉地图` when the report contains any current buy/sell/hold/trim decision.
- A compact `术语小注` section when the report contains three or more technical terms.
- A `这对你意味着什么` sentence after any major data panel, especially macro, research committee, data quality, and paper validation.
- Audit panels must translate `warning / blocked_by_evidence / partially_proven / degraded` into plain Chinese by using the shared `scripts/audit_warning_explainer.py` mapping or an equivalent output. The explanation must say what the gap affects, who can fix it, and how it limits the allowed action.
