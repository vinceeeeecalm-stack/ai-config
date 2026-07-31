# Portfolio Ledger Schema V2.17

这是持仓账本的规格，用于同步成本、现金、质押状态、建议和结果。当前阶段不要求脚本自动执行交易，但报告必须按此结构思考和输出。

## 1. Account Summary

| 字段 | 类型 | 说明 |
|---|---|---|
| `as_of` | string | 账本更新时间 |
| `base_currency` | string | 默认 USD |
| `total_value` | number | 总资产 |
| `cash_usd` | number | 美元现金 |
| `stablecoin_value` | number | 稳定币价值 |
| `crypto_value` | number | crypto总市值 |
| `us_equity_value` | number | 美股现货总市值 |
| `staked_value` | number | 质押资产市值 |
| `liquid_value` | number | 可立即调配资产 |
| `cash_rails` | object | 两路现金通道状态 |
| `external_tactical_cash_pool` | object | $3000外部战术资金池，未确认前不计入任何现金通道 |
| `strategy_version` | string | 当前策略版本 |
| `config_hash` | string | 当前配置哈希 |
| `last_run_id` | string | 最近一次运行ID |
| `data_quality_status` | enum | verified/disputed/stale/missing |
| `last_verified_at` | string | 最近校验时间 |
| `ledger_pricing_policy` | string | 截图数量、API估值等口径 |
| `screenshot_account_summary` | object/null | 截图总额仅作 reconciliation |

## 2. Holding

| 字段 | 类型 | 说明 |
|---|---|---|
| `holding_id` | string | 唯一ID |
| `asset_class` | enum | `crypto` / `us_equity_spot` / `cash` / `stablecoin` |
| `account_rail` | enum | `crypto_rail` / `us_equity_rail` |
| `symbol` | string | 标的 |
| `name` | string | 名称 |
| `bucket` | enum | `core` / `satellite` / `tactical_spot` / `reserve` |
| `strategy_horizon` | enum | daily / weekly / monthly / quarterly / multi_year |
| `status` | enum | Core Stake/Hold, Core Hold, Active Add/Trim, Tactical Spot, Exit/Thesis Broken |
| `quantity` | number | 总数量 |
| `liquid_quantity` | number | 可卖数量 |
| `locked_quantity` | number | 锁定数量 |
| `avg_cost` | number | 平均成本 |
| `cost_basis_status` | enum/string | full_avg_cost_available / partial_lots_known_preexisting_unknown / missing_full_lot_history / not_applicable 等 |
| `known_cost_basis_usd` | number/null | 已验证 lot 的成本合计；不得替代完整 `avg_cost` |
| `known_cost_basis_quantity` | number/null | 已验证 lot 的数量合计 |
| `known_avg_cost` | number/null | 已验证 lot 的均价，仅用于部分成本覆盖 |
| `known_quantity_pct` | number/null | 已验证数量占当前数量比例 |
| `cost_basis_notes` | string/null | 成本覆盖口径、未知 lot 和阻断项 |
| `current_price` | number | 当前价 |
| `market_value` | number | 当前市值 |
| `api_valuation` | object/null | API估值详情 |
| `unrealized_pnl` | number | 未实现盈亏 |
| `realized_pnl` | number | 已实现盈亏 |
| `target_weight` | number | 目标权重 |
| `current_weight` | number | 当前权重 |
| `dynamic_action` | enum/null | hold_core/conditional_hold/hold_until_trigger/add_candidate/small_probe_candidate/trim_on_strength/trim_on_invalidation/sell_or_exit/watch_no_add |
| `short_term_score_points` | number/null | 短期走势、催化剂和量能评分 |
| `long_term_thesis_score_points` | number/null | 长期 thesis、收益、基本面/链上评分 |
| `position_pressure_score_points` | number/null | 仓位超配、杠杆、现金通道和流动性压力 |
| `exit_plan_quality_score_points` | number/null | 止损、止盈、持有窗口、失效条件完整度 |
| `thesis` | string | 持有逻辑 |
| `entry_reason` | string | 入场理由 |
| `stop_or_invalid` | string | 止损或失效条件 |
| `targets` | array | 目标价/目标区间 |
| `planned_exit_time` | string/null | 战术仓或短线计划退出时间；条件持有必须有时间或条件 |
| `data_quality_status` | enum | verified/disputed/stale/missing |
| `last_verified_at` | string | 最近校验时间 |
| `recommendation_id` | string/null | 关联建议 |
| `run_id` | string/null | 来源运行 |
| `strategy_version` | string | 策略版本 |
| `config_hash` | string | 配置哈希 |
| `validation_status` | enum | not_tested/backtested/bias_checked/paper_validated/human_confirmed |
| `promotion_status` | enum | research_only/paper_validated/human_confirmed_live_candidate/api_ready_candidate |
| `risk_decision` | enum/null | allow/downgrade/block/manual_review_required |
| `outcome_status` | enum/null | pending/hit/failed/not_triggered/expired/invalidated |

### 2A. API Valuation

| 字段 | 类型 | 说明 |
|---|---|---|
| `quantity_source` | string | screenshot/account_export/user_confirmed |
| `api_price_basis` | string | API价格来源和合成方法 |
| `api_composite_price` | number | API综合价格 |
| `api_market_value` | number | 数量 * API综合价格 |
| `screenshot_market_value` | number/null | 截图显示估值，仅作对照 |
| `valuation_delta_pct` | number/null | API估值相对截图估值偏差 |
| `valuation_basis_used` | string | 采用口径 |

## 3. Lot

每次买卖都应维护 lot：

| 字段 | 类型 | 说明 |
|---|---|---|
| `lot_id` | string | 唯一ID |
| `date` | string | 交易日期 |
| `action` | enum | buy/sell/stake/unstake/reward |
| `quantity` | number | 数量 |
| `price` | number | 成交价格或奖励入账价格 |
| `fees` | number | 手续费 |
| `cost_basis` | number | 成本 |
| `source` | string | 用户确认/API/导入 |
| `data_quality_status` | string | verified/missing_cost_basis/disputed/stale |

如果仅有部分 lot 已知，必须保持 holding-level `avg_cost=null`，并使用 `known_cost_basis_usd`、`known_cost_basis_quantity` 和 `lots[]` 表示局部覆盖。不得用截图当前价值、估算价格或用户未确认的历史仓位反推完整成本。

## 4. Crypto Staking

Crypto持仓可含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `staking_allowed` | boolean | 是否允许质押 |
| `staked_quantity` | number | 质押数量 |
| `staking_provider` | string | 平台/钱包/协议 |
| `estimated_apy` | number | 预估年化 |
| `reward_quantity` | number | 累计奖励 |
| `reward_value_usd` | number | 奖励估值 |
| `lock_status` | enum | liquid/locked/unlocking |
| `unlock_available_at` | string/null | 可解锁时间 |
| `slash_risk` | enum | low/medium/high/unknown |
| `provider_risk` | enum | self_custody/exchange/defi_protocol/unknown |
| `tax_note` | string | 税务提示 |
| `staking_unlock_delay_days` | number/null | 预估解锁延迟 |
| `count_as_immediate_liquidity` | boolean | locked/unlocking 时必须为 false |
| `provider_service_type` | enum/string | liquid_staking_receipt/delegated_self_custody/exchange_staking/defi_protocol |
| `receipt_token_symbol` | string/null | lcETH/LsETH/cbETH 等收据代币 |
| `underlying_asset` | string/null | ETH/ADA/SOL 等底层资产 |
| `receipt_conversion_ratio` | number/null | 收据代币兑底层资产比例 |
| `redeemable_underlying_quantity` | number/null | 可赎回底层资产数量 |
| `account_mark_value` | number/null | 账户账面估值 |
| `spot_proxy_value` | number/null | 底层 spot proxy 估值 |
| `public_market_proxy_value` | number/null | 收据代币公开市场 proxy 估值 |
| `instant_unstake_fee_pct_if_available` | number/null | 即时退出费用比例 |
| `staking_valuation_status` | enum/string | verified/disputed/stale/missing/verified_with_basis_difference |
| `staking_data_sources` | array | 平台文档、账户截图、API、公开市场 proxy 来源 |

质押奖励不得混入买入成本，应作为 reward lot 单独记录。

## 5. US Equity Spot

美股持仓不得包含期权、保证金、做空或期货字段。杠杆ETF仅允许作为短期战术字段记录：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ticker` | string | 股票/ETF代码 |
| `exchange` | string | NYSE/NASDAQ等 |
| `earnings_date` | string/null | 财报日期 |
| `is_etf` | boolean | 是否ETF |
| `is_leveraged_or_inverse` | boolean | 普通现货/普通ETF为 false；杠杆ETF为 true 且必须符合短期战术规则 |
| `leverage_factor` | number/null | 杠杆ETF倍数，如2或3 |
| `underlying_index` | string/null | 底层指数或行业 |
| `max_holding_trading_days` | number/null | 杠杆ETF必填，绝对最多10 |
| `planned_exit_time` | string/null | 杠杆ETF必填 |
| `hard_stop_pct` | number/null | 杠杆ETF必填 |
| `take_profit_plan` | string/null | 杠杆ETF必填 |
| `long_term_core` | boolean | 是否长期核心 |

## 5A. Binance Market Data Snapshot

Crypto 持仓或候选可含 Binance 行情快照：

| 字段 | 类型 | 说明 |
|---|---|---|
| `binance_pair` | string/null | 例如 NIGHTUSDT，或 null |
| `binance_pair_status` | enum | verified/pair_missing/low_liquidity/stale/disputed |
| `binance_last_price` | number/null | Binance 最新价 |
| `binance_quote_volume_24h` | number/null | 24h报价币成交额 |
| `binance_trade_count_24h` | number/null | 24h成交笔数 |
| `binance_bid_ask_spread_pct` | number/null | 买卖价差 |
| `binance_depth_1pct_usd` | number/null | 上下1%深度 |
| `binance_trend_1h_4h_1d` | object/null | 1h/4h/1d走势 |
| `binance_data_timestamp` | string/null | 数据时间戳 |

## 5B. External Tactical Cash Pool

`external_tactical_cash_pool` 必须记录：

| 字段 | 类型 | 说明 |
|---|---|---|
| `amount_usd` | number | 默认 3000 |
| `rail_assignment_status` | enum | pending_dynamic_assignment/crypto_rail_candidate/us_equity_rail_candidate/split_candidate/confirmed |
| `confirmed_rail` | string/null | 已确认到账通道 |
| `deployment_tier` | enum | no_deploy/paper_only/small_probe/partial_deploy/full_deploy |
| `maximum_deploy_amount` | number | 本期最大建议部署金额 |
| `maximum_planned_loss` | number | 本期最大计划亏损 |
| `human_confirmation_required` | boolean | 必须为 true |
| `count_as_available_cash` | boolean | 未确认到账前必须为 false |

## 6. Recommendation History

每条建议必须保存：

| 字段 | 类型 | 说明 |
|---|---|---|
| `recommendation_id` | string | `YYYYMMDD-symbol-bucket-seq` |
| `run_id` | string | 来源运行 |
| `strategy_version` | string | 策略版本 |
| `config_hash` | string | 配置哈希 |
| `asset_class` | enum | crypto/us_equity_spot/cash/stablecoin |
| `symbol` | string | 标的 |
| `action` | enum | buy/add/hold/trim/sell/watch/stake/unstake |
| `dynamic_action` | enum/null | 目标导向动态动作 |
| `direction` | enum | bullish/bearish/neutral/risk_reduction |
| `forecast_event` | string/null | 被预测的具体事件 |
| `forecast_window` | string/null | 预测时间窗口 |
| `forecast_probability_pct` | number/null | 真实预测概率/把握程度 |
| `forecast_base_rate` | number/null | 基础概率 |
| `forecast_evidence_adjustment` | string/null | 概率上调或下调依据 |
| `forecast_invalid_if` | string/null | 预测失效条件 |
| `execution_readiness_score` | number/null | 执行准备度 points，不是概率 |
| `short_term_score_points` | number/null | 短期走势评分 |
| `long_term_thesis_score_points` | number/null | 长期 thesis 评分 |
| `position_pressure_score_points` | number/null | 仓位压力评分 |
| `exit_plan_quality_score_points` | number/null | 退出计划质量评分 |
| `upside_target_pct` | number/null | 目标收益 |
| `downside_stop_pct` | number/null | 计划止损 |
| `payoff_ratio` | number/null | 赔率 |
| `expected_value_pct` | number/null | 期望值 |
| `friction_adjusted_ev_pct` | number/null | 摩擦后期望值 |
| `historical_sample_count` | number/null | 可比历史样本数 |
| `historical_hit_rate_pct` | number/null | 历史命中率 |
| `out_of_sample_hit_rate_pct` | number/null | 样本外命中率 |
| `capital_sleeve` | enum/null | long_term_core/growth_satellite/tactical_alpha_sleeve |
| `cash_rail_source` | enum/null | crypto_rail/us_equity_rail/external_tactical_cash_pool |
| `entry_range` | string/null | 入场区间 |
| `target_range` | string/null | 目标区间 |
| `stop_or_invalid` | string | 止损或 thesis 失效 |
| `time_window` | string | 评估窗口 |
| `confidence` | enum | high/medium/low |
| `data_quality_status` | enum | verified/disputed/stale/missing |
| `validation_status` | enum | not_tested/backtested/bias_checked/paper_validated/human_confirmed |
| `promotion_status` | enum | research_only/paper_validated/human_confirmed_live_candidate/api_ready_candidate |
| `risk_decision` | enum | allow/downgrade/block/manual_review_required |
| `friction_assumptions` | object | 费用、滑点、价差、税务、流动性、解锁延迟 |
| `outcome_status` | enum | pending/hit/failed/not_triggered/expired/invalidated |

## 7. Experiment Records

每次运行必须保存：

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | string | `YYYYMMDD-run-type-seq` |
| `run_type` | enum | daily/weekly/monthly/quarterly/event/backtest/paper |
| `created_at` | string | 运行时间 |
| `strategy_version` | string | 策略版本 |
| `config_hash` | string | 配置哈希 |
| `data_sources` | array | 数据源与时间戳 |
| `data_quality_summary` | object | 数据质量汇总 |
| `report_path` | string | 报告路径 |
| `recommendation_ids` | array | 建议ID |
| `proposed_change_ids` | array | 参数变更建议ID |
| `errors` | array | 错误 |
| `degradations` | array | 降级 |

## 8. Proposed Changes

参数变更只写入待确认区域：

| 字段 | 类型 | 说明 |
|---|---|---|
| `proposed_change_id` | string | `pc-YYYYMM-seq` |
| `source_run_id` | string | 来源运行 |
| `current_strategy_version` | string | 当前版本 |
| `target_strategy_version` | string | 拟生成版本 |
| `change_type` | enum | weight/risk/data_validation/staking/allocation/reporting |
| `current_value` | any | 当前值 |
| `proposed_value` | any | 建议值 |
| `evidence` | string | 依据 |
| `rollback_plan` | string | 回滚条件 |
| `approval_status` | enum | pending/approved/rejected/revised |

## 9. Ledger Safety

- 账本无法解析时，停止交易建议。
- 总资产无法计算时，停止交易建议。
- locked/unlocking/staked资产不得计入可立即卖出流动性。
- 用户未确认的交易不得写入真实账本，只能进入 pending updates。
- 没有 `run_id`、`strategy_version` 和 `config_hash` 的建议不得作为强行动建议。
- `promotion_status = research_only` 的策略不得写入真实交易草案。
- API自动调仓字段只可出现在未来路线图，不得在当前账本中作为执行状态。
- 不得只因 `current_weight > target_weight` 生成强制卖出；必须结合 `dynamic_action`、失效条件和风险模型。
