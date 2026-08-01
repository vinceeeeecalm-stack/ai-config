# Recommendation History Policy

本文件定义手动投资报告的建议记录、到期复盘和学习闭环。它把报告中的操作草案转换成可追踪记录，但不代表自动交易。

## Core Rule

每次报告只要输出 `execute_now`、`conditional_action`、`paper_only`、`watch`、`no_deploy`、`hold`、`trim_review` 或 DCA 草案，都必须写入推荐历史。

默认账本：

- `recommendations/recommendation_history.json`

辅助脚本：

- `scripts/recommendation_history.py`
- `scripts/recommendation_outcome_reviewer.py`
- `scripts/recommendation_calibration_packager.py`

常用命令：

- `summary`: 输出总建议数、状态、资产类别和资金桶摘要。
- `due-review`: 找出评估窗口已到期但仍是 `pending` 的建议。
- `calibration`: 按真实预测概率桶统计命中、失败和 Brier score。
- `export-panel`: 输出日报可直接嵌入的 Recommendation History Panel。

校准确认包：

```bash
python3 manual-investment-strategy-operator/scripts/recommendation_calibration_packager.py \
  --markdown-output manual-investment-strategy-operator/reports/YYYY-MM-DD-recommendation-calibration-package.md
```

该包只做三件事：

1. 汇总当前 hit/failed resolved 样本距离校准门槛还差多少。
2. 把已到期且可复盘的 draft reviews 转成 `confirmation_queue`。
3. 生成需要人工核对后才能执行的 `recommendation_history.py review` 命令。

它不会自动改写 `recommendation_history.json`，也不会把 `superseded` 记录计入 hit/failed 校准。

如果账本无法读取、无法写入或与当前 `strategy_version` / `config_hash` 不一致，报告必须标记 `recommendation_history_degraded`，不得输出新的 `execute_now`，只能输出 `watch`、`conditional_action`、`hold`、`no_deploy` 或修复提示。

## Required Recommendation Fields

每条建议至少包含：

| 字段 | 说明 |
|---|---|
| `recommendation_id` | 唯一ID，建议格式 `YYYYMMDD-symbol-bucket-seq` |
| `run_id` | 来源运行ID |
| `strategy_version` | 当前策略版本 |
| `config_hash` | 当前配置哈希 |
| `generated_at` | 生成时间 |
| `asset_class` | `crypto` / `us_equity_spot` / `cash` / `stablecoin` |
| `symbol` | 标的或交易对 |
| `bucket` | `core` / `satellite` / `tactical_spot` / `reserve` |
| `action` | buy/add/hold/trim/sell/watch/stake/unstake/no_deploy |
| `direction` | bullish/bearish/neutral/risk_reduction |
| `time_window` | 评估窗口 |
| `data_quality_status` | verified/disputed/stale/missing/degraded |
| `validation_status` | not_tested/backtested/bias_checked/paper_validated/human_confirmed |
| `promotion_status` | research_only/paper_validated/human_confirmed_live_candidate/api_ready_candidate |
| `risk_decision` | allow/downgrade/block/manual_review_required |
| `outcome_status` | pending/hit/failed/not_triggered/expired/invalidated |

战术或真实候选还必须包含：

| 字段 | 说明 |
|---|---|
| `forecast_probability_pct` | 真实目标达成概率，不是执行度 |
| `execution_readiness_score` | 执行准备度 points |
| `entry_range` | 入场区间 |
| `target_range` | 目标区间或情景 |
| `stop_or_invalid` | 止损或 thesis 失效条件 |
| `forecast_invalid_if` | 提前失效条件 |
| `capital_sleeve` | long_term_core/growth_satellite/tactical_alpha_sleeve |
| `cash_rail_source` | crypto_rail/us_equity_rail/external_tactical_cash_pool |
| `target_before_stop_probability_pct` | 在评估窗口内先到目标而不是先到止损的概率 |
| `stop_before_target_probability_pct` | 在评估窗口内先到止损而不是先到目标的概率 |
| `expected_mae_pct_5d_10d_20d` | 5/10/20日常见最大不利波动 |
| `stress_gap_pct` | 隔夜或事件跳空压力损失 |
| `financing_and_dilution_snapshot` | 美股候选的融资、稀释、债务和资本缺口摘要 |
| `price_as_of / price_source / data_age_minutes / data_freshness_status` | 推荐冻结价格的时间、来源、年龄和新鲜度 |
| `entry_window_start / entry_window_end / allowed_session` | 允许观察或人工入场的日期区间和交易时段 |
| `entry_trigger / no_entry_if_not_triggered` | 精确触发；未触发时必须禁止买入 |
| `expected_holding_days / max_holding_days` | 自然日与交易日口径的最短、基准与最长持有期 |
| `target_1_price_or_scenario / target_1_evaluation_window` | 第一目标及预计检查窗口 |
| `target_2_price_or_scenario / target_2_evaluation_window` | 第二目标及预计检查窗口 |
| `time_stop / event_exit_date / event_handling_plan` | 时间止损与财报/FOMC/解锁/监管事件处理 |
| `post_exit_state / auto_relay_forbidden` | 卖出后进入待人工分配现金，禁止自动接力买入 |
| `current_deployable_cash_usd / settlement_constraint` | 当前真实可部署现金和结算约束 |
| `execution_action / observation_action` | 真实执行上限与独立观察状态；`NO_DEPLOY` 不等于停止盯盘 |
| `observation_trigger / impulse_monitoring_required / impulse_check_deadline` | 闭合 K 线异动条件、是否继续观察及截止时间 |
| `candidate_coverage_matrix` | 候选在市场、官方事件、社交新闻、资产专项风险四类角色中的时间戳与来源覆盖 |
| `baseline_frozen / historical_baseline_mutation_forbidden` | 冻结原始前视判断；后续事件只能创建新记录或关联信号 |

DCA 草案还必须包含可复盘的长周期时机字段：

| 字段 | 说明 |
|---|---|
| `long_horizon_timing_decision` | 完整的长周期 DCA 时机判断对象 |
| `dca_timing_decision` | `accelerated_dca / near_price_entry / limit_order_wait / hold_stablecoin_until_trigger` |
| `staking_wait_cost_usd` | 等待期间少拿的估算质押收益 |
| `required_pullback_to_wait_pct` | 继续等待至少需要换来的回调折扣 |
| `time_in_market_bias_score` | 时间在场倾向分；分数越高，越说明长期低位、质押和低配因素支持先买一档 |
| `wait_requires_specific_pullback_trigger` | 若为 true，继续等待必须有明确回调幅度、期限和失效条件 |
| `dynamic_buy_strategy` | 近价第一档、等待限价、前置 DCA 或稳定币等待触发的具体执行模式 |
| `waiting_burden_of_proof_comment` | 为什么等待不是默认动作，以及等待需要证明哪些条件更优 |
| `front_load_extra_months` | 是否前置未来 0 / 0.25 / 0.5 / 1.0 个月 DCA |
| `front_load_amount_usd` | 本轮建议前置的金额 |
| `near_term_total_budget_usd` | 本轮近期待投入上限，含前置金额 |

这些字段用于复盘“当时为什么现在买、为什么等限价、为什么前置或不前置”。缺少这些字段时，DCA 建议只能算自然语言草案，不能算完整学习样本。

所有正式候选还必须通过 `config/recommendation_execution_calendar_schema.json` 与 `scripts/recommendation_execution_calendar_gate.py`。任一字段缺失时，记录可以保留为诊断证据，但最大动作必须降为 `no_deploy`。

## Outcome Review

到期建议必须复盘并写入 `outcome_reviews`：

- `hit`: 到达目标、行动建议有效，或 DCA 分批逻辑改善入场成本。
- `failed`: 触发止损、方向错误、thesis 破坏或风险规则失效。
- `not_triggered`: 未进入入场区间。
- `expired`: 时间窗口结束但结果不明确。
- `invalidated`: 新数据使原建议提前失效。
- `superseded`: 新一轮同标的/同资金桶/同 `action_source` 建议替代了旧 pending 计划；不计入命中率、失败率或 Brier 校准。

原止损或失效条件一旦触发，记录必须优先标记为 `failed` 或
`invalidated`。不得把止损向更不利方向移动来保留旧建议；如要继续持有，
必须建立新的 thesis、新 recommendation ID、完整风险卡并再次人工确认。

每次复盘必须写：

- `reviewed_at`
- `outcome_status`
- `actual_return_pct` 或 `actual_notes`
- `attribution`
- `what_should_change`
- `proposed_change_id`，如需要策略迭代

## Attribution Categories

错误归因必须使用以下类别之一或多个：

- `data_error`
- `timing_error`
- `regime_error`
- `fundamental_error`
- `onchain_error`
- `catalyst_error`
- `position_sizing_error`
- `risk_rule_error`
- `execution_assumption_error`
- `validation_error`
- `overfit_error`
- `staking_valuation_error`
- `expected_value_error`
- `dynamic_positioning_error`
- `research_committee_error`
- `manual_execution_delay`

## Monthly Learning Output

月度报告必须从推荐历史汇总：

- 总建议数、命中率、失败率、未触发率。
- Crypto / 美股分开统计。
- Core / Satellite / Tactical 分开统计。
- 预测概率桶校准：80%+、60%-79%、低于60%。
- `execute_now` / `conditional_action` / `watch` 分组结果。
- 错误归因排名。
- 待人工确认的 `proposed_changes`。

不足 20 条同类建议或不足 3 个完整月度窗口时，只能输出观察，不得大幅改权重。

## Progressive Learning Confidence

系统允许从 60% 左右的学习型判断开始积累经验，而不是要求第一轮就达到 80% 真实胜率。

| 概率段 | 记录方式 | 最大动作 |
|---|---|---|
| `<60%` | 记录为低优先级观察或不部署 | `watch / no_deploy / hold` |
| `60%-69%` | 记录为初始学习样本，重点看是否触发入场与方向 | `watch / paper_only / conditional_action` |
| `70%-79%` | 记录为较强学习样本，要求更清晰目标价和失效条件 | `paper_only / conditional_action / small_probe_review` |
| `>=80%` | 记录为强候选，但仍需执行度、风险门、现金通道和人工确认 | `conditional_action / execute_now_candidate` |

每条 `60%-79%` 建议必须写入 `probability_basis` 和 `what_would_change_my_mind`。后续复盘时如果多次命中，才允许把同类信号的 proposed change 推到更高权重；如果多次失败，必须降低该信号或缩短持有窗口。

## Supersede Hygiene

每次日报写入新建议时，必须避免旧 pending 建议无限堆积。

默认流程：

1. 新建议先通过完整字段校验。
2. 若证据、入场区间、目标、止损和评估窗口没有实质变化，复用当前活动记录并更新 `last_reaffirmed_at`，不得新建重复建议。
3. 只有出现新的可审计证据或计划发生实质变化时，才写入新记录并启用 `scripts/recommendation_history.py add --supersede-open-equivalent`。
4. 若旧建议与新建议拥有相同 `asset_class`、`symbol`、`bucket`、`capital_sleeve` 和 `action_source`，且旧建议仍为 `pending`，则旧建议标记为 `superseded`。
5. `superseded` 记录保留原始判断和 supersede 审计字段，但不进入 `due-review`，也不参与 hit/failed 概率校准。
6. 只有最新 pending 建议作为当前可复盘计划；历史 superseded 记录用于追踪“当时为什么这么想”，不能当作策略成功或失败样本。
7. 到达原止损、目标或最晚评估日后必须先做 outcome review；不得用新建议把本应命中/失败的记录直接 supersede。
8. 如确实需要把某条 superseded 历史记录转换成校准样本，必须由人工运行独立市场窗口复盘并显式写入 `hit/failed/not_triggered/expired/invalidated`；默认工具不得自动转换。

## Real-Trade Incident Backfill

用户确认已发生真实买入，但 recommendation history 不存在原始记录时，系统必须在同一通道继续推荐前创建 `incident_backfill`：

- 记录用户确认或券商核验的数量、成本、成交时间、当前回撤和数据质量。
- 明确列出缺失的原始 thesis、目标、止损、概率和仓位依据。
- 不得事后编造这些缺失字段，也不得把 backfill 计入策略命中率。
- backfill 必须给出 `process_failure_attribution`、`repair_action` 和下一次复盘日。

## Proposed Changes

学习结果只生成 `proposed_changes`，不得自动改策略。

每条变更必须包含：

- `proposed_change_id`
- `source_recommendation_ids`
- `source_run_id`
- `current_strategy_version`
- `target_strategy_version`
- `change_type`
- `current_value`
- `proposed_value`
- `evidence`
- `rollback_plan`
- `approval_status`

`approval_status` 只能是 `pending`、`approved`、`rejected`、`revised`。只有用户人工确认后，才能进入下一版配置。

## Safety

- 不在账本、日志或报告中保存 API key、secret、bearer token。
- 推荐历史是研究和复盘记录，不是交易指令。
- 当前版本不自动真实下单，不调用下单、提现或保证金接口。
