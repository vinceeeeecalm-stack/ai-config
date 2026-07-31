# Goal 10x Execution Plan

本文件把长期目标从“报告结论”固化成每次手动调度必须执行的计划。它不保证收益；它定义如何在数据、风险和人工确认都通过时，把新增资金和现有战术仓尽量导向 5 年/10 年 10x 目标。

## Objective

目标分两层：

- Aggressive path: 当前本金在 5 年内达到 10x 或以上。
- Fallback path: 当前本金在 10 年内达到 10x 或以上。

默认新增资金为 `$1,000/月`，进入 crypto rail 后用于 DCA。美股目标不是全账户月度翻倍，而是让可动用战术仓在月度/季度尽量实现 `50%+` 的机会收益；长期保护仓不为短线目标牺牲。

## Objective Traceability

每次正式调度必须能把用户目标追溯到具体机制。完整矩阵见 `OBJECTIVE_TO_MECHANISM_TRACEABILITY.md`；本计划只保留执行层摘要：

| 目标 | 机制入口 | 审计证据 |
|---|---|---|
| 5年/10年 10x | `Goal Gap Panel`, `Goal Execution Dashboard` | 当前组合价值、目标所需年化、5/10 年路径 |
| `$1,000/月` crypto DCA | `DCA Pair Gate`, `Candidate Deep Dive` | DCA 金额、两档入场、复盘时间、recommendation record |
| 长期高凸性与质押复利 | `Asset Goal Contribution`, `Staking Compounding Model` | APY、锁定/解锁、达到 10x 仍需价格倍数 |
| 美股月/季 `50%+` 战术收益 | `US Tactical Performance`, `Tactical Rotation Relay` | 当前动态战术仓、目标差距、入场/出场、现金来源 |
| 主动取数和情绪 | `Fresh Market Intelligence`, `Research Committee` | 数据源、时间戳、缺失/降级、subagent 证据 |
| 持续学习 | `Recommendation History`, `Learning Review Calendar` | pending/due/resolved 建议、paper 样本、错误归因 |

若任一目标无法追溯到证据，报告必须降级，并把该缺口进入下一步 backlog。

## Target Math

每次调度必须重算：

| 目标口径 | 说明 | 必须输出 |
|---|---|---|
| Current-principal 10x | 当前组合价值乘以 10 | 5年/10年所需年化、不同情景终值 |
| Cumulative-capital 10x | 当前组合价值加未来 DCA 本金后整体乘以 10 | 作为极端路径，不作为默认执行基准 |
| Survival path | 低年化、卫星失败或高凸性资产不兑现 | 是否还能保留10年目标机会 |
| Base path | 主线 DCA + 质押复利 + 少量战术 alpha | 是否接近10年10x |
| Bull path | SOL/高凸性资产和美股战术都成功 | 是否支持5年10x |

10x 目标不能被简化成固定比例买入。若目标缺口扩大，系统可以提高高凸性候选的研究优先级，但不能绕过数据质量、现金通道、流动性和人工确认。

## Crypto DCA Engine

每次运行顺序：

1. 确认 crypto rail 现金或新 DCA 到账；未到账不得假设可用。
2. 用 API/公开行情重估持仓价值；截图价格只用于对账，不能用于入场价。
3. 运行 `Goal Gap Panel` 和 `Staking Compounding Model`。
4. 将持仓标记为 `accelerator / neutral / drag / tail_convexity`。
5. 在 `SOL / ADA / NIGHT或其他高凸性候选 / USDT机会仓 / BTC机会锚 / ETH不新增` 之间动态分配。

默认方向：

| 资产桶 | 默认动作 |
|---|---|
| `SOL` | 主要新增候选；低配、数据 verified、宏观不过热时优先 |
| `ADA` | 深度折价质押卫星；只在生态和流动性未恶化时小额 |
| `NIGHT` | 高凸性尾仓；价格、深度、float、解锁和项目数据冲突时不新增 |
| `ETH/lcETH` | 已严重超配时不新增；只跟踪解锁、估值和质押风险 |
| `BTC` | 机会型流动性锚；只有极端恐慌或需要降低组合风险时启用 |
| `USDT` | 机会仓，不长期闲置；通常保留当月 DCA 的 `15%-25%` |

每次 DCA 最多两档：

- `near_price_small_entry`: 小额近价介入。
- `better_pullback_main_entry`: 更优回调主仓。

## US Equity Tactical Engine

美股分成长期保护仓和战术仓：

| 类型 | 规则 |
|---|---|
| 长期保护仓 | 如 CRCL；默认不为短线轮动卖出，除非 thesis 破坏或极端超配 |
| 小型周期仓 | 如 COIN；只在机会显著强于当前战术仓时可作为补充流动性 |
| 战术仓 | 动态识别，不 hardcode SOXL；若当前战术仓仍最强，则不换仓 |
| 杠杆 ETF | 只允许短期，默认 1-5 个交易日复盘，绝对不超过 10 个交易日 |

美股 `execute_now` 必须同时满足：

- 目标时间内达到目标价的真实预测概率 `>=80%`。
- 执行准备度 `>=80`。
- 有明确入场、止损、止盈、最晚退出日。
- 资金来源已经确认，并且不会动用长期保护仓。

未通过双 80 时，最多输出 `watch / conditional_action / paper_only`。

## Progressive Learning Ramp

目标系统不要求第一轮调度就证明 80% 真实胜率。正确路径是：

1. 从 `60%-69%` 的学习型候选开始记录：只允许 `watch / paper_only / conditional_action`。
2. 当同类建议积累 10 个 resolved 结果后，输出初步校准，不改变核心权重。
3. 当同类建议积累 20 个以上 resolved 结果并覆盖至少 3 个周/月窗口后，才允许提出小幅 proposed changes。
4. 只有当候选本身达到 `forecast_probability_pct >=80`、`execution_readiness_score >=80`，并且 Research Committee、策略晋级、数据质量、现金通道和人工确认全部通过时，才允许进入 `execute_now_candidate`。

因此，低于 80% 不代表系统不能学习；它代表系统还不能把该判断包装成高把握执行。

## Research Committee Requirement

每次生成计划必须经过至少 6 个研究角色：

- `prior_thesis_challenge_agent`
- `portfolio_state_agent`
- `macro_regime_agent`
- `crypto_market_agent`
- `onchain_defi_agent`
- `social_news_agent`
- `us_equity_alpha_agent`
- `backtest_validation_agent`

若少于 6 个角色成功返回，标记 `research_committee_degraded`，不得输出新的 `execute_now`。

## Failure Conditions

以下情况自动降级：

| 条件 | 降级 |
|---|---|
| 美股或 crypto 现金通道未确认 | `conditional only` |
| 价格源冲突或过期 | `watch` |
| 质押解锁、费用、APY缺失 | `smaller size / hold only` |
| NIGHT 等高凸性资产缺 float、解锁、深度 | `block new add` |
| 美股候选缺历史样本或 walk-forward | `watch / paper_only` |
| 杠杆ETF无退出日期 | `block add / trim review` |

## Required Output

每次报告必须输出：

- 当前组合价值和 1D/7D/30D 变化。
- `Goal Execution Dashboard`：把 5年/10年 10x 目标数学、crypto DCA 主线、美股战术缺口、research/paper/recommendation 验证缺口和下一步 backlog 放在同一入口。
- 5年/10年目标路径测算。
- 本月 `$1,000` DCA 两档计划。
- 美股战术仓状态、现金接力状态和最多两个入场/出场区间。
- Research Committee 分歧和最大允许动作。
- 缺失数据与降级原因。
- 明确本次“现在做什么 / 等什么 / 不做什么”。

## Action Plan Data Binding

报告里的行动价位必须来自当次数据快照，而不是从旧报告复制：

- Crypto 两步 DCA 价格使用当次 `crypto_market_panel` 的 Binance spot last/bid/ask、24h high/low、成交额、funding/OI 和 Fear & Greed 状态生成；缺少快照时只能输出 `watch/conditional`，不能给强买入。
- 美股战术接力价格使用当次 `us_tactical_performance_panel` 的当前动态战术仓价格生成；scanner 候选只在 `forecast_probability_pct >= 80` 且 `execution_readiness_score >= 80` 时才允许替代当前战术仓。
- `active-alpha` scanner 产出的候选若缺少完整 `research_panel`，只能进入动态候选审查表，不能直接成为 `execute_now`。
- 最终操作清单必须压缩成表格：通道/资产、动作上限、价格/金额动作、触发条件、失效/复盘。超过两档的支撑/阻力只能写入观察说明。

## Report Integrity Audit

每次完整报告生成后，应运行 `scripts/report_integrity_audit.py` 验证关键不变量：

- 报告包含 5年/10年 10x 目标数学、持仓总览、Research Committee、Crypto DCA、美股战术接力和最终操作清单。
- final context 已绑定 `crypto_snapshot_json` 和 `us_scanner_json`。
- Research Committee 至少 6 个角色且本次未降级。
- DCA 与美股战术行动表遵守最多两步。
- `execute_now` 未在缺少双80、人工确认或数据质量时被放开。
- 本次 `run_id` 的建议已经写入 `recommendation_history.json`，用于后续复盘、命中率、错误归因和 proposed changes。

一键入口 `scripts/manual_dispatch_run.py` 必须传播这些审计结果：

- 只有报告生成成功、`report_integrity_audit` 通过、`objective_coverage_audit` 通过，顶层 dispatch summary 才能显示 `status=ok`。
- 若任一报告审计失败，顶层必须显示 `status=report_audit_failed`，即使底层 Markdown 已经生成。
- 若用户为了检查结构而跳过 recommendation history 写入，审计失败是正确结果；这种报告不能作为目标导向操作报告使用。
- 若使用 `--allow-degraded-market-intelligence` 生成降级报告，报告必须展示降级原因，并保持 `execute_now_allowed=false`。

## Objective Coverage Audit

每次完整报告还必须运行 `scripts/objective_coverage_audit.py`，检查报告是否真正覆盖用户原始目标，而不是只通过格式审计：

- 5年/10年 10x 目标数学存在且绑定当前持仓。
- `$1,000/月` crypto DCA 同时进入目标测算、DCA guidance 和 recommendation history。
- DCA 与美股战术建议都具备结构化金额、入场区间、入场截止日、目标/复盘窗口、失效条件和 `action_source`。
- 美股战术池按月/季 `50%+` 目标追踪，且排除长期保护仓。
- 两路现金通道分离，跨 rail 转移必须人工确认。
- Research Committee、Recommendation History 和双80门槛共同阻止未校准 `execute_now`。
- active-alpha paper ledger 已接入；样本量、paper 净收益、最大回撤和 closed trade 数不足时，目标覆盖审计必须保留 warning，并阻止把策略宣传为已验证。

该审计通过不等于收益目标可保证达成；它只证明本轮报告已经覆盖目标约束、执行计划和复盘证据。

## Goal Execution Dashboard

`scripts/goal_execution_dashboard.py` 是目标执行总控台。它不抓取行情、不下单、不替代报告，而是读取当前 `daily-context-final.json`、recommendation history、strategy promotion evidence 和 validation sample audit，输出：

- 当前组合价值、`$1,000/月` DCA、5年/10年 10x 所需年化。
- Crypto DCA 当前主线、次主线、尾部候选和禁新增资产。
- 美股战术池的当前价值、现金拖累、月度 `50%+` 缺口和最大允许动作。
- Research Committee、paper validation、walk-forward、recommendation calibration 的阻断状态。
- 下一步 execution backlog，明确哪些任务是验证样本、学习闭环、DCA、US tactical 或目标构建，不把 backlog 当成买入清单。

报告生成器必须把 dashboard 放在持仓和目标数学之后、策略晋级面板之前，作为后续各面板的索引。如果 dashboard 生成失败，报告只能把最大动作降级为 `watch`，并把修复 dashboard 作为 P0 backlog。
