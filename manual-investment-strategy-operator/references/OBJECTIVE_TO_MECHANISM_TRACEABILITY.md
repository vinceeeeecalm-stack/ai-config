# Objective To Mechanism Traceability

本文件把用户的双重目标拆成可审计机制。它不保证收益；它确保每次调度优先用已确认战术资金和同通道闲置资金争取月度 ROI `100%`，并独立使用每月 DCA、质押复利与价值增长追求 5 年 10x、10 年 10x 兜底，同时保留人工确认和学习闭环。

术语备注：`traceability` 是“目标能追溯到哪个面板、脚本和证据”；`mechanism` 是“系统流程”，不是收益承诺。

## Requirement Matrix

| 用户目标 | 必须回答的问题 | 负责面板/流程 | 负责脚本/证据 | 缺失时最大动作 |
|---|---|---|---|---|
| 5 年内尽量 10x，10 年 10x 作为兜底 | 当前组合离 5年/10年 10x 还差多少年化？哪些仓位提高或拖慢目标？这些仓位的 5年/10年价格情景是否支撑目标？ | `Goal Gap Panel`, `Asset Goal Contribution Panel`, `Long-Term Price Scenario Panel`, `Goal Execution Dashboard` | `goal_path_projection.py`, `asset_goal_contribution.py`, `long_term_price_scenario_panel`, `goal_execution_dashboard.py` | `goal_tracking_only / watch` |
| 每月约 `$1,000` crypto DCA | 本月买什么、买多少、等什么价，是否比持现金更接近目标？ | `DCA Pair Gate`, `Comprehensive Data Quality Gate`, `Candidate Deep Dive` | `manual_dispatch_run.py`, crypto market snapshot, `recommendation_history.json` | `conditional_action / smaller_size / watch` |
| DCA 必须长期主义而非固定买 BTC | SOL/ADA/NIGHT/ETH/BTC/USDT 谁更符合当前目标函数？质押复利能补偿多少价格涨幅要求？5年/10年 base/bull 情景是否值得占用新增本金？ | `Long-Term Goal DCA Engine`, `Long-Term Price Scenario Panel`, `Staking Compounding Model`, `Asset Micro Thesis Matrix` | `asset_goal_contribution.py`, `build_daily_report_context.py`, staking/APY evidence, DeFi/onchain/project sources | `watch / no_deploy` |
| 每次调度主动取市场和情绪 | 本轮是否刷新了价格、宏观、链上、新闻、社交、资金流、美股候选？ | `Fresh Market Intelligence Panel`, `Macro Regime Panel`, `Crypto Key Person Intelligence Panel` | `market_data_source_preflight.py`, `macro_regime_snapshot.py`, active handoff | `market_intelligence_degraded; no execute_now` |
| 战术资金月度 ROI `100%` 进攻目标 | 当前动态战术仓是否继续持有、卖出、回补或接力？一个主推荐和最多两个备选如何排序？ | `Ranked Tactical Choices`, `US Tactical Performance Panel`, `Tactical Rotation Relay` | 历史样本外回归、scanner handoff、同通道现金证据 | `paper_only / conditional_action` |
| 美股短线只在高把握时执行 | 目标价在目标时间内达到的真实概率是否 `>=80%`？执行准备度是否 `>=80`？ | `Target Achievement Gate`, `Candidate Deep Dive Gate` | recommendation records, paper validation, walk-forward evidence | `watch / paper_only` |
| 手动 skill 是被动调度，不自动交易 | 这次是否只是用户触发的报告？有没有下单、转账或后台循环？ | `Manual Dispatch Strategy Contract`, `Current Turn Closeout Gate` | `manual_dispatch_run.py`, `next_dispatch_readiness.py`, `next_goal_execution_queue.py` | `status_report_only` |
| 策略要从 60% 学到 80%+ | 本轮新增建议是否可复盘？已有建议和 paper 样本是否到期？错误是否归因？ | `Recommendation History Gate`, `Learning Review Calendar`, `Progressive Learning Confidence Panel` | `recommendation_history.py`, `recommendation_outcome_reviewer.py`, `learning_review_calendar.py`, `progressive_learning_iteration_audit.py` | `paper_only / conditional_action` |
| 多研究员减少旧结论锚定 | 是否有独立角色质疑旧结论、补宏观/链上/社交/美股/回测证据？ | `Research Committee Gate`, `Prior Thesis Challenge Panel` | `research_panel_runner.py`, `research_committee_quality_auditor.py`, `research_subagent_output_collector.py` | `research_committee_degraded; no execute_now` |
| 两路现金独立 | Crypto cash 与美股 cash 是否分别确认？未到账资金是否被误用？ | `Position Gate`, `Cash Rails Panel`, `Missing Data / Downgrade Panel` | portfolio ledger, broker/exchange screenshots or exports | `conditional_only / block sizing` |

## Dispatch Acceptance Checklist

正式手动调度必须同时满足：

1. 有唯一 `run_id`。
2. 非 `--smoke`，且没有把结构测试当成投资报告。
3. 已刷新或明确降级市场、宏观、情绪、链上/DeFi、社交和美股候选数据。
4. 输出 5年/10年 10x 目标数学和当前组合目标缺口。
5. 输出长期价格情景：至少覆盖本轮相关的 ETH/SOL/ADA/NIGHT 或其它被推荐/回避资产，说明 `5y base`、`10y base`、`10x适配`、`DCA含义` 和降级原因。
6. 输出 `$1,000/月` DCA 或用户指定金额的动态两档计划。
7. 输出动态战术仓、月度 ROI `100%` 目标缺口、一个主推荐与最多两个合格备选，以及最多两档入场/出场。
8. 所有行动都写入 recommendation history，或者明确标记写入失败并降级。
9. 运行 `report_integrity_audit.py` 和 `objective_coverage_audit.py`。
10. 输出 `current_turn_closeout`：继续短任务、正式报告、深研，或本轮收口。
11. 明确 `goal_mechanism_ready` 与 `goal_complete` 的差别。

如果其中任一项缺失，本轮只能作为降级参考，不能作为强执行建议。

## Current Evidence Priorities

当前最重要的不是继续写更多规则，而是补可复盘证据：

| 优先级 | 缺口 | 为什么重要 |
|---|---|---|
| P0 | 继续关闭至少 10 个 paper trades | 没有足够模拟样本，就不能把短线系统升级为真实执行 |
| P0 | 至少 10 个 recommendation resolved outcomes | 没有真实建议结果，就不能把 80% 写成校准概率 |
| P0 | 美股 settled cash / buying power | 决定战术仓 sizing，避免满仓时误判可动用资金 |
| P1 | lcETH 成本、赎回、费用、解锁条款 | 决定 ETH/lcETH 是否是长期拖慢项或可持有核心 |
| P1 | ADA/SOL 完整 lot 与质押奖励 | 决定真实收益、DCA 权重和复利贡献 |
| P1 | 6+ evidence-verified research roles | 决定 Research Committee 是否解除降级 |

## Output Contract

报告的最终行动清单必须用普通中文回答：

- 现在做什么。
- 等什么价格或事件。
- 不做什么。
- 为什么这更接近 5年/10年 10x 或美股战术收益目标。
- 哪些数据缺失导致只能观察或条件执行。

专业词第一次出现时必须加注释。示例：`paper` 是模拟交易样本；`execute_now` 是可立即人工确认的动作，不是自动下单；`drawdown` 是回撤，即从高点跌下来的幅度。
