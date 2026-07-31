# Active Goal Execution Matrix

本文件是当前 active goal 的执行矩阵。它把用户原始目标、现有 skill 机制、当前证据状态和下一步动作放在一张表里，避免后续调度时把“机制可用”误写成“收益目标已完成”。

术语备注：`active goal` 是当前长期任务；`execution matrix` 是执行矩阵，意思是每个目标都要能对应到证据、脚本和下一步。

## Current Verdict

| 项目 | 当前状态 | 直白解释 |
|---|---|---|
| 机制是否可用 | `goal_mechanism_ready=true` | 已经有手动调度、取数、报告、建议记录、学习日历和审计链路。 |
| 财务目标是否完成 | `goal_complete=false` | 真实 5年/10年 10x 还没有发生，不能标记完成。 |
| 当前最高动作 | `paper_only` | 只允许模拟/观察；真实交易必须人工确认，且目前强动作证据不足。 |
| 下一次有效推进 | 到期 paper 复盘或用户触发 fresh 正式报告 | 未到期时不应空跑长任务。 |

## Requirement To Execution Map

| 原始目标 | 每次调度必须做什么 | 当前机制 | 当前缺口 | 完成前动作上限 |
|---|---|---|---|---|
| 5年内尽量 10x，10年 10x 兜底 | 重算当前本金、每月 DCA、目标终值和所需年化 | `Goal Gap Panel`, `Goal Execution Dashboard` | 真实收益结果尚未出现 | `watch / conditional_action` |
| 每月约 `$1,000` crypto DCA | 根据目标差距、持仓比例、宏观、链上、质押和流动性决定买什么 | `DCA Pair Gate`, `Asset Goal Contribution`, `Staking Compounding Model` | lcETH 成本/赎回、ADA/SOL 完整 lot 和质押奖励仍需补证据 | `conditional_action` |
| DCA 不能默认买 BTC | 动态比较 SOL、ADA、NIGHT、ETH/lcETH、BTC、USDT 与其他候选 | `Long-Term Goal DCA Engine` | 高凸性资产仍需要流动性、解锁和项目数据验证 | `watch / small_dca_review` |
| 每次调度主动获取市场数据和情绪 | 刷新价格、成交量、宏观、资金流、链上、新闻、关键人物和美股候选 | `Fresh Market Intelligence Panel`, `Research Committee Gate` | 数据源或 subagent 不齐时必须降级 | `no execute_now` |
| 美股战术仓月度/季度 `50%+` | 动态识别战术仓，判断持有、卖出、回补或接力候选 | `US Tactical Performance`, `Tactical Rotation Relay` | 美股 settled cash / buying power 仍缺券商证据 | `paper_only / conditional_action` |
| 强动作需要高把握 | 目标价真实达成概率 `>=80%` 且执行准备度 `>=80` | `Target Achievement Gate`, `Candidate Deep Dive Gate` | paper 样本和 recommendation resolved 样本不足 | `paper_only` |
| 系统要越跑越聪明 | 每条建议写入 history，到期后复盘 hit/failed/not_triggered/expired | `Recommendation History`, `Learning Review Calendar`, `Progressive Learning Audit` | recommendation resolved 结果为 0，至少先补 10 条 | `paper_only / conditional_action` |
| 手动 skill 不是自动交易 | 只在用户触发时取数和出报告；不后台下单、不自动转账 | `Passive Dispatch Runtime Policy`, `Due Learning Review Pulse` | 无阻断；但必须继续防止长循环 | `status_report_only` when not due |

## Next Trigger Rules

| 触发 | 运行什么 | 为什么 |
|---|---|---|
| 用户要求今日/周度/美股/crypto 报告 | `manual_dispatch_run.py --run-id <id> --monthly-dca 1000 --current-tactical-symbol <动态战术仓>` | 重新取数、分析、写入建议和审计。 |
| paper 样本到期 | `due_learning_review_pulse.py --timeout-seconds 180 --format json` | 只复盘到期样本；未到期自动停止。 |
| recommendation 到期 | `recommendation_outcome_reviewer.py` 草案流程 | 把建议结果变成 hit/failed/not_triggered/expired，用来校准概率。 |
| 用户补充成本/现金证据 | `goal_evidence_import_validator.py` 再到 `goal_evidence_import_preview.py` | 先只读验证和预览，不自动改账本。 |

## Do Not Do

- 不把 5年/10年 10x 目标写成已完成。
- 不把 `paper_only`、社交热度或单次行情判断写成 80% 真实概率。
- 不让普通手动调度直接运行长循环 `sunday_crypto_realistic_paper_loop.py`。
- 不在 paper 样本未到期时反复跑 validation。
- 不默认把 crypto 现金和美股现金互相挪用。

## Plain Acceptance For Each Future Report

每次正式报告必须用普通中文回答：

1. 现在应该做什么。
2. 等什么价格、事件或数据确认。
3. 哪些标的不做，为什么。
4. 这条建议如何更接近 crypto 5-10 年 10x 或美股战术 `50%+` 目标。
5. 本轮新增了哪些可复盘记录，下一次什么时候复盘。

如果报告不能回答这五点，只能作为降级参考，不能作为正式投资判断。

