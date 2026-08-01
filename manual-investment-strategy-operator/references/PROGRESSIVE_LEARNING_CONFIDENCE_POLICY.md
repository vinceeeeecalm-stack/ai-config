# Progressive Learning Confidence Policy

## Purpose

本 policy 定义“从 60% 学习到 80%+”的机制。系统不需要在第一天就证明所有建议都有 80% 真实胜率；它需要在每次手动调度时记录判断、复盘结果、归因错误，并用样本逐步校准概率。

80% 仍然是 `execute_now` 的强门槛，不是所有学习型建议的入场门槛。低于 80% 的建议可以进入 `watch`、`paper_only`、`conditional_action` 或小额 DCA 草案，但必须写入 recommendation history，等待后续复盘。

## Success Semantics

本系统有两个不同的“完成”口径：

- `goal_mechanism_ready`：机制已经能在每次调度时刷新数据、生成建议、记录样本、安排复盘、归因错误，并把下一轮要补的证据写入 backlog。这个状态可以在收益目标尚未达成时成立。
- `goal_complete`：真实组合已经证明 5年/10年 10x 等财务目标达成，并且短线执行证据也通过。这个状态不会因为一次报告、一次模拟交易或一次策略迭代而成立。

因此，本轮迭代的验收重点是 `goal_mechanism_ready=true` 和 `ready_for_progressive_learning_loop=true`，不是强行证明本次已经能稳定 80% 或保证收益。

## Learning-First Acceptance

用户当前认可的阶段性验收标准是“机制持续进步”，不是“本次调度必须完成目标”。也就是说：

- 初始阶段允许只有约 `60%` 的 paper/观察质量，只要每条判断都能被记录、复盘、归因。
- 每次调度必须回答“这次新增了什么证据、哪些判断被证伪、下一轮该补什么数据”。
- 每次正式手动调度默认运行 exit-only `paper validation pulse`，只复核已有 paper 仓位是否触发退出或临近到期，并把最近复盘时间写入摘要；没有到期样本时，`closed_count_delta=0` 是正常学习状态。
- 每次正式手动调度默认运行 `learning review calendar`，把 paper 到期复盘、recommendation 到期复盘和样本缺口合并展示；它只安排复盘，不自动修改账本，也不授权真实交易。日历必须区分 `due_now`（已经该复盘）和 `due_within_24h`（24小时内准备复盘），避免把“快到期”误写成“现在就复盘”。
- `80%+` 是长期校准后的强动作门槛，不是冷启动阶段的硬性成果声明。
- 当样本不足时，系统应该诚实输出 `watch / paper_only / conditional_action`，而不是为了显得有把握而升级为 `execute_now`。
- 学习机制达标可以先成立；真实收益目标达成必须等结果证明。

## Confidence Ladder

| 阶段 | 真实预测概率 | 允许动作 | 含义 |
|---|---:|---|---|
| `reject_or_watch` | `<60%` | `watch / no_deploy / hold` | 证据不足，主要用于观察和等待更好触发 |
| `learning_probe` | `60%-69%` | `watch / paper_only / conditional_action` | 初始学习阶段，可以记录模拟或条件计划，不作为强执行 |
| `calibrated_probe` | `70%-79%` | `paper_only / conditional_action / small_probe_review` | 有一定把握，可给更明确触发价和小额草案，但仍不算双80 |
| `validated_action_candidate` | `>=80%` | `conditional_action / execute_now_candidate` | 只有同时满足执行准备度、数据质量、研究委员会、策略晋级、现金通道和人工确认，才可升级 |

术语备注：`small_probe_review` 指“小额试探并重点复盘”，不是自动买入；是否执行仍由用户人工确认。

## Action Rules

- `forecast_probability_pct` 是目标时间内达到目标价或目标情景的真实预测概率，不是热度分。
- `execution_readiness_score` 是执行准备度，不是上涨概率。
- `60%-79%` 可以作为学习型建议，但报告必须写清“这是学习/条件阶段，不是高把握执行”。
- `>=80%` 也不能自动执行；还必须通过数据质量、流动性、风险回报、策略晋级、现金通道和人工确认。
- 任何阶段都不得自动真实下单、自动转账或绕过两路资金通道。

## Learning Milestones

| 样本阶段 | 最低证据 | 系统行为 |
|---|---|---|
| `cold_start` | `<10` 个 resolved 结果 | 允许 60%+ 学习型建议，但所有短线强动作保持 blocked |
| `early_calibration` | `10-19` 个 resolved 结果 | 输出初步概率校准，允许小幅调整观察权重 |
| `usable_calibration` | `20+` 个 resolved 结果且至少 3 个周/月窗口 | 可提出参数或权重 proposed changes，但需人工确认 |
| `validated_ramp` | `50+` 个 resolved 结果且多市场体制覆盖 | 才允许把稳定命中率用于更高动作等级评估 |

`resolved` 指建议已经被人工或复盘工具标记为 `hit / failed / not_triggered / expired / invalidated`。`superseded` 不计入命中率。

## Required Report Output

每次手动报告必须说明：

- 当前处于哪个学习阶段。
- 本轮建议落在哪个概率段：`<60 / 60-69 / 70-79 / >=80`。
- 本轮新增建议会如何被复盘：时间窗口、目标价/目标情景、失效条件。
- 最近建议的命中、失败、未触发、失效和错误归因。
- 从当前阶段升级到下一阶段还缺多少 resolved 样本。
- paper validation pulse 的结果：是否关闭新 paper 样本、下一个 paper 样本何时到期、样本缺口是否减少。
- learning review calendar 的结果：早期校准还缺多少样本、可用校准还缺多少样本、是否有 `due_now` 样本、是否只是 `due_within_24h` 准备复盘。

## Upgrade And Rollback

- 学习结果只能进入 `proposed_changes`，不能自动改策略权重。
- 如果连续两期校准恶化，系统必须提出降低相关信号权重或回滚上一版参数。
- 如果某个策略族在 20+ resolved 样本后仍无法超过基准，必须降级到 `watch/paper_only`。
- 如果某个策略族达到 80%+ 表观命中率但样本集中在单一行情，仍必须标记 `regime_concentration_risk`，不能直接升级。
