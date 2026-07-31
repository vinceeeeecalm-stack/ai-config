# Tactical Drawdown Sentinel Policy

## Purpose

本 policy 用于修复美股战术仓“买入后缺少持续风险提醒”的问题。它不是新的选股器，而是每次推荐新机会前必须先跑的已持仓风险哨兵。

核心目标：任何美股战术仓从成本价或近期高点出现明显回撤时，报告必须先提醒风险、解释原因、给出减仓/止损/修复计划，再讨论是否继续持有或轮动到新标的。

## Scope

适用范围：

- 当前识别出的 `deployable_tactical_position`。
- 所有非长期保护的美股战术仓和高波动单股。
- 条件补充流动性仓，例如用户明确允许参与战术轮动的 COIN 或其他标的。

默认排除：

- CRCL 等被标记为长期保护仓的持仓。若其 thesis 破坏，也必须进入长期风险复盘，但不使用战术仓止损口径。

## Required Data

每个已持有美股战术仓必须输出：

| 字段 | 说明 |
|---|---|
| `symbol` | 标的代码 |
| `quantity` | 持仓数量 |
| `cost_basis` | 券商确认成本；若缺失，用用户口述成本并标记 `user_stated_cost` |
| `current_price` | 本轮实时或准实时价格 |
| `unrealized_pnl_pct` | 相对成本的未实现盈亏 |
| `recent_high_drawdown_pct` | 相对 20/60/120 日或持仓后高点的回撤 |
| `relative_performance` | 相对 QQQ、IWM、SMH 或最相关行业 ETF 的 5日/10日/20日表现差 |
| `support_lost` | 是否跌破关键支撑或入场 thesis 失效价 |
| `volume_spike` | 成交量是否高于 20 日均量 1.5x / 2x |
| `news_risk_flags` | 融资、增发、债务、项目交付、客户集中、监管、财报或分析师风险 |
| `risk_level` | green / yellow / orange / red / black |
| `required_action` | hold / no_add / trim_review / exit_review / repair_plan |
| `next_review_deadline` | 下一次必须复盘的日期或交易条件 |
| `what_would_change_my_mind` | 哪些数据改善后才允许恢复加仓或继续持有 |

## Alert Thresholds

风险等级按最严重触发项计算。

| 等级 | 触发条件 | 最大允许动作 |
|---|---|---|
| `green` | 成本附近或盈利，趋势未破坏 | hold / evaluate candidates |
| `yellow` | 相对成本回撤 `>=8%`，或跌破短期支撑，或 5日相对 QQQ/SMH 跑输 `>=6pp` | no_add / review |
| `orange` | 相对成本回撤 `>=12%`，或 5日相对 QQQ/SMH 跑输 `>=10pp`，或跌破 20日趋势且放量 | trim_review / stop_plan_required |
| `red` | 相对成本回撤 `>=18%`，或近期高点回撤 `>=25%`，或放量破位叠加融资/增发/债务/交付/客户集中风险 | exit_review_or_repair_plan / block_new_add |
| `black` | thesis 明确破坏、流动性/财务/合规风险显著恶化，或无法验证关键持仓/价格 | no_new_action / exit_or_manual_repair_only |

高波动 AI/data-center/crypto-equity 单股可使用更紧阈值：

- `yellow`: `>=7%`
- `orange`: `>=11%`
- `red`: `>=16%`

## Mandatory Workflow

每次美股报告必须按以下顺序：

1. 读取当前美股持仓、数量、成本、长期保护标签和可动用性。
2. 为每个战术仓执行本哨兵。
3. 若任一战术仓为 `orange` 或更高，先输出 `Open Tactical Position Risk Sentinel Panel`。
4. 若任一战术仓为 `red` 或 `black`，不得输出新增买入或“继续持有即可”的结论；必须给出减仓、止损、修复或等待重新确认计划。
5. 只有哨兵结果不阻断，才进入新的 Dynamic US Equity Alpha Discovery。

## News And Filing Checks

当触发 `yellow` 或更高风险，必须补查：

- 最新公司新闻、8-K、10-Q、S-3/ATM/增发/可转债/信贷协议。
- 财报或指引变化。
- 分析师上修/下修。
- 大客户、项目交付、数据中心融资、债务成本和资本开支变化。
- 行业 ETF、相关 peers 和大盘是否同步下跌。

若无法完成新闻/filing 检查，最大动作降为 `trim_review` 或 `watch`，不得恢复 `execute_now`。

## Report Language

报告必须用直白中文说明：

- “这不是普通回撤，还是和大盘同步的正常波动？”
- “从成本到现在亏了多少，从近期高点回撤多少？”
- “哪些信号本该提醒我们？”
- “现在还能拿的前提是什么？跌破哪里必须复盘或退出？”

## APLD Incident Learning Note

APLD 从用户约 `$46` 成本附近跌到约 `$30-$32` 区间时，已经触发：

- 相对成本回撤超过 `18%`。
- 相对近期高点回撤超过 `25%`。
- 多日连续跌破支撑。
- 相对 QQQ/IWM 明显跑输。

此类情况未来必须进入 `red` 哨兵，而不是继续作为普通候选持有。
