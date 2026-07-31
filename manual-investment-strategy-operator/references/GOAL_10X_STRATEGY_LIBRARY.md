# Goal 10x Strategy Library

本文件把“可以使用的投资方法”从单次报告结论中抽离出来，作为每次调度的策略库。它不承诺收益；它定义哪些策略可以服务 5年/10年 10x 目标，哪些只能研究或模拟，哪些在当前范围内禁止。

## Objective Mapping

| 目标 | 允许策略族 | 当前用途 |
|---|---|---|
| 5年当前本金 10x 进攻路径 | 高质量增长 DCA、高凸性尾仓、少量美股战术 alpha | 提高上行空间，但必须接受更高波动和更严格数据门 |
| 10年当前本金 10x 兜底路径 | 目标导向 DCA、质押复利、核心-卫星、动态再平衡 | 组合主路径，避免单一资产失败拖垮全局 |
| 美股月/季 50%+ 战术目标 | 短中期趋势接力、事件驱动、动态候选扫描 | 只作用于可部署战术仓，不作用于 CRCL 等保护仓 |
| 生存能力 | 现金/USDT机会仓、数据降级、仓位上限、止损/失效 | 保留下一次机会和避免目标诱发过度交易 |

## Strategy Families

| strategy_id | 策略族 | 适用范围 | 晋级状态 | 主要门槛 |
|---|---|---|---|---|
| `goal_weighted_dca` | 目标导向 DCA | Crypto 新增月度资金 | `human_confirmed_live_candidate` | Goal Gap、资产贡献、宏观节奏、数据质量通过 |
| `staking_compound_satellite` | 质押复利卫星 | SOL/ADA/ETH-lcETH 等可质押资产 | `human_confirmed_live_candidate` | APY、解锁、费用、可流动性、集中度验证 |
| `core_satellite_barbell` | 核心-卫星/杠铃 | Crypto 与美股长期仓 | `research_only` for new targets | 核心仓不新增拖慢项；卫星必须有流动性和 thesis |
| `convex_tail_sleeve` | 高凸性尾仓 | NIGHT/其他小市值或新叙事资产 | `research_only` | float、解锁、深度、官方路线图、生态数据 verified |
| `dynamic_reserve_timing` | 机会仓/分批入场 | USDT/现金等待回调 | `human_confirmed_live_candidate` | 不长期闲置；明确等待触发价和最长等待期 |
| `trend_rotation_relay` | 短中期趋势接力 | 美股战术仓/杠杆 ETF | `paper_validated_required` | 2-4 周主线、双80、止损、最晚退出、现金接力 |
| `event_news_alpha` | 事件/消息驱动 Alpha | 美股和 crypto watch/paper | `paper_only` | 多源确认、价格量能确认、历史 base rate，不可单靠新闻 |
| `walk_forward_paper_validation` | 回测/模拟验证 | 所有新短线策略 | `research_required` | 样本外、过拟合检查、paper 记录、成本/滑点 |

## Strategy Selection Rules

每次报告必须先判断策略族，再判断标的：

1. 长期 crypto 新增资金默认从 `goal_weighted_dca` 开始，而不是先问“买哪个币”。
2. 可质押资产进入 `staking_compound_satellite`，但 APY 只能降低所需价格倍数，不能替代价格上涨。
3. 高凸性资产进入 `convex_tail_sleeve`；缺 float、解锁、深度或官方生态数据时只能 `watch/no_deploy`。
4. 美股战术资金进入 `trend_rotation_relay`；当前战术仓若仍是最高风险回报，不为了交易而换仓。
5. 事件驱动候选先进入 `event_news_alpha`，只能升级到 `paper_only/conditional_action`，不能直接实盘。
6. 所有新短线方法必须被 `walk_forward_paper_validation` 拦截；样本量不足时不能写成真实 80% 胜率。

## Promotion Gates

| 晋级状态 | 允许动作 | 晋级要求 |
|---|---|---|
| `research_only` | 研究、观察、进入候选卡片 | 数据源完整度初步合格，未通过历史验证 |
| `paper_only` | 模拟交易、记录预测 | 入场/出场/止损完整，风险模型允许模拟 |
| `paper_validated` | 条件化小仓草案 | 至少 20 个相似样本或 3 个不同市场窗口，正期望值且最大回撤可接受 |
| `human_confirmed_live_candidate` | 人工确认后小额/常规 DCA 或条件行动 | 数据 verified、风险门通过、现金通道确认、写入 recommendation history |
| `api_ready_candidate` | 未来自动化路线图 | 需要审批、审计日志、回滚、限额、kill switch；当前版本禁用 |

## Evidence-Driven Promotion Evaluator

每次报告必须用 `scripts/strategy_promotion_evaluator.py` 把 paper ledger、recommendation history 和最新 walk-forward / weekly-goal 实验转换成动态晋级证据，而不是只展示静态策略表。

`strategy_promotion_evaluator.py` 必须同时读取 `active-alpha-paper-monitor/scripts/validation_sample_auditor.py` 的只读证据台账。该台账负责去重 paper 快照、分离 open/closed/walk-forward/recommendation calibration 口径，并明确：

- closed paper trades 还差多少。
- 已平仓样本的本金口径是否完整。
- recommendation calibration resolved 还差多少。
- walk-forward 是否存在 `target_research_pass`。
- 哪些候选只适合继续 paper，而不是实盘买入。

短线策略晋级到 `paper_validated / conditional_action` 至少需要：

- 最新 walk-forward evidence 可读取，覆盖足够框架，并出现非偶然的 `target_research_pass` 或可解释的 `paper_only` 候选。
- closed paper trades `>=20`
- paper win rate `>=55%`
- paper net return `>=5%`
- paper max drawdown 不低于 `-15%`
- recommendation outcome reviews `>=10`
- probability calibration resolved samples `>=10`
- validation sample audit 没有 `paper_closed_sample_too_small`、`paper_closed_realized_return_below_gate`、`walkforward_no_target_research_pass`、`paper_ledger_live_orders_flag_unexpected` 或 `paper_private_api_used_unexpected`

未满足时：

- `trend_rotation_relay` 最高 `paper_only`
- `event_news_alpha` 最高 `watch/paper_only`
- `walk_forward_paper_validation` 保持 `research_required`
- manual 报告必须把 `report_readiness.execute_now_allowed=false`，追加 `strategy_promotion_evidence_gate_failed`，并继续阻止新的 `execute_now`

walk-forward 只提供研究证据，不替代 forward paper：

- `research_watch` 只能进入观察清单。
- `paper_only` 可以进入 paper 队列，但仍不能真钱执行。
- 没有 `target_research_pass` 时，不能把历史局部高收益写成真实 80% 胜率。
- walk-forward top candidates 必须在报告中显示样本数、OOS 收益、回撤和 stage，方便人工判断是否值得继续 paper。
- validation audit 的 `next_validation_queue` 只表示下一轮验证优先级，不是买入清单，不得进入最终操作建议。

## Strategy Iteration Backlog

每次报告还必须生成 `Strategy Iteration Backlog`：它回答“有没有更好的策略值得下一轮验证”，但不直接给买卖指令。默认脚本是 `scripts/strategy_iteration_backlog.py`。

候选策略必须满足：

| 要求 | 说明 |
|---|---|
| 目标绑定 | 必须说明服务 5年/10年 10x、crypto DCA、美股战术 `50%+` 或生存/再入场 |
| 数据输入 | 必须列出需要哪些价格、宏观、链上、质押、现金、新闻或 paper 数据 |
| 验证路径 | 必须说明 research -> paper -> recommendation calibration -> proposed change |
| 当前动作上限 | 没有通过验证前只能 `watch / paper_only / conditional_action` |
| 术语备注 | 必须用普通中文解释策略含义，避免只写硬术语 |

默认迭代候选：

| strategy_id | 用途 | 初始动作上限 |
|---|---|---|
| `crypto_drawdown_weighted_dca` | 用回撤、价格位置和长期 thesis 调整 DCA 权重 | `conditional_action` |
| `staking_adjusted_relative_value` | 把质押复利折算到达到 10x 仍需价格倍数 | `conditional_action` |
| `high_convexity_tail_screen` | 为 NIGHT/新叙事资产建立尾仓筛选 | `watch_or_no_deploy` |
| `us_short_mid_trend_relay` | 美股 2-4 周战术主线接力，不 hardcode 标的 | `paper_only` |
| `event_volume_confirmation_alpha` | 事件/消息必须被量价确认后进入候选 | `watch_or_paper_only` |
| `risk_regime_cash_timing` | 判断 DCA 近价买还是等回调，不让现金长期闲置 | `conditional_action` |

这些候选只能进入报告的“下一轮策略迭代候选”部分。若用户要求正式操作，仍需经过数据质量、Candidate Deep Dive、双80、推荐历史写入和人工确认。

## Sizing Principles

- Crypto DCA 使用目标权重区间，不使用机械固定比例。
- 单月 DCA 调整默认不超过 10 个百分点；更大变化必须进入 `proposed_changes`。
- 美股战术仓 sizing 来源于当前可部署战术资金池，不动用长期保护仓。
- 美股战术单笔最大可接受回撤默认 `8%-12%`，杠杆 ETF 最多持有 `10` 个交易日。
- 高凸性尾仓不能因为单日上涨扩大为主仓；必须经过流动性、供应和 thesis 验证。

## Excluded Strategies

以下策略不进入本系统当前版本：

- 期权、0DTE、融资、卖空、反向 ETF、期货、perpetual、保证金。
- 高频做市、网格套利、跨交易所套利。
- 新闻或社交热度单独触发真实交易。
- 未经 paper/walk-forward 的自动交易。
- 把杠杆 ETF 或单一小市值币作为长期 DCA 核心。

## Report Requirements

每次完整报告必须输出一个简短 `Strategy Library / Promotion Panel`：

- 本轮使用的策略族。
- 每个策略族当前最大允许动作。
- 哪些策略因为数据、样本、现金通道或风险门被降级。
- 本轮新增 recommendation 进入哪个策略族，后续如何复盘。
