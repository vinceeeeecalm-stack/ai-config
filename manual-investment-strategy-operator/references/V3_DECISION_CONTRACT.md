# V3 Decision Contract

The executable schema is `scripts/v3_decision_contracts.py`. This document explains its stable wire contract; it does not add alternate field names.

## RequestSpecV2

Required:

- `schema_version = request-spec-v2`
- `request_id`
- `request_mode`
- `query`
- `requested_at` with timezone

`request_mode` is exactly one of:

- `longterm_dca`
- `intraday_scalp`
- `tactical_1_7d`
- `event_trade_1_3w`
- `existing_position_review`
- `daily_dual_window`

Split a multi-horizon request into unique request and recommendation IDs.

## RecommendationV2 common fields

Required:

- identity: `recommendation_id / request_id / request_mode / symbol / asset_class`
- evidence: `evidence_snapshot_id / decision_price / decision_price_evidence_id / price_as_of`
- decisions: `research_decision / current_direct_decision / execution_decision`
- capital: `deployable_cash / cash_source / execution_blockers`
- time: `generated_at / decision_valid_until / review_due_at`
- lifecycle: `observation_status / execution_status / outcome_status`
- safety: `data_quality_status / human_confirmation_required=true / live_orders_enabled=false / private_api_used=false`

Newly generated tactical/DCA records should carry `risk_adjusted_path` when
closed-bar data is available. It is backward-compatible for older records and
must keep `live_gate_effect=none_until_promotion`. See
`RISK_ADJUSTED_PATH_QUALITY_POLICY.md`.

`current_direct_decision` is one of `enter_now / small_entry_now / do_not_enter_now / hold_existing / exit_now`.

`execution_decision` is one of `manual_execute_candidate / no_deploy_cash / no_deploy_evidence / no_deploy_risk / paper_only / watch / no_action`.

Zero cash does not erase research preference or the current direct decision. It requires `execution_decision=no_deploy_cash`, `deployable_cash=0` and blocker `no_deployable_cash`.

Serialized recommendations expose the fixed decision-first fields
`best_candidate / research_decision / current_direct_decision / account_state /
execution_decision / executable_amount`. Cash and execution permission may
change only the execution fields, not the research winner.

## RankedRecommendationSetV1

For `intraday_scalp`, `tactical_1_7d` and `event_trade_1_3w`, the report layer
must group validated RecommendationV2 records into one deterministic ranked
set:

- exactly one `primary_candidate` with `research_decision=preferred`;
- zero to two `qualified_alternatives` with `research_decision=eligible`;
- one shared `evidence_snapshot_id / strategy_version / config_hash`;
- per candidate `rank / symbol / historical_win_rate_interval / sample_size /
  conservative_expected_value / expected_return / profit_factor /
  max_drawdown / reward_risk / liquidity_status / decision_valid_until`;
- for each alternative, `why_ranked_lower` and the material metric delta versus
  the primary;
- `incumbent_symbol / supersedes_recommendation_id / switch_reason_codes` when
  rank 1 changes.

The compatibility field `best_candidate` remains the rank-1 symbol. Alternatives
do not become simultaneous orders. Only the primary may be a current manual
execution candidate; selecting an alternative requires a new timestamped deep
dive and human confirmation. If fewer than three candidates pass the minimum
historical-quality gate, omit the weak slots instead of filling them.

## LongTermDCAPlanV2

Required only for `longterm_dca`:

- `fundamental_quality_rank`
- `raw_upside_rank`
- `portfolio_next_dollar_rank`
- `market_capacity / adoption / value_capture / supply_dilution`
- `staking_net_yield_pct / staking_liquidity_risk`
- three five-year and three ten-year Bear/Base/Bull scenarios, each set totaling 100%
- `contribution_plan`
- `quarterly_review_at / annual_review_at`
- `thesis_invalidation`

Do not require tactical targets, price stop, trading session or event exit.

## IntradayScalpPlanV2

Required only for `intraday_scalp`: quote age no more than 60 seconds, closed
1m and 5m timestamps, VWAP, opening range, relative volume, spread, bid/ask
depth, market anchor, trigger, cancellation, stop, two targets, same-day latest
close, maximum-loss budget and position size. Default decision validity is five
minutes. Overnight is forbidden. A leveraged ETF requires confirmed underlying
evidence; a binary event or thin/unverified book blocks entry.

## TacticalPlanV2

Required for `tactical_1_7d` and `event_trade_1_3w`:

- `entry_window_start / entry_window_end / allowed_session`
- `entry_price_min / entry_price_max`
- `target_1_price / target_2_price`
- target evaluation windows
- `price_stop / time_stop / latest_exit_or_review_at`
- structured min/base/max calendar and trading holding days
- `event_plan`; event trades also require `event_exit_date`
- `historical_event_reaction`
- `derivatives_or_options`
- `volume_microstructure`
- `financing_dilution_or_unlock`
- `downside_gap_pressure`
- one `probability_event` with sample size, base probability, adjustments, limitations and calibration status
- Bear/Base/Bull scenarios totaling 100%
- `auto_relay_forbidden=true`
- `post_exit_state=cash_pending_manual_reallocation`

For a candidate with `discovery_origin = new_chain_event`, also require:

- `catalyst_derivation_chain`
- `affiliation_status`
- `venue_discovery`
- `new_chain_security_gate`

The derivation must show the official event, ecosystem transmission, asset
selection, market confirmation and invalidation. A DEX-only asset cannot be
rejected merely because it lacks a CEX pair, but missing sellability, holder,
LP or contract evidence blocks a theoretical current entry.

Sample rules: `n<10` is `judgment_only`; `10–29` must be `wide_interval`; only `n≥30` without lookahead leakage and with an untouched holdout may be `calibrated`.

The action gate has no universal 80% threshold. Conservative EV is
`p_target*target_return - p_stop*loss - friction`. A wide-interval sample may
support only `small_entry_now` with positive conservative EV, R/R≥2, a complete
realtime signal and account risk≤0.25%. `enter_now` additionally requires a
calibrated untouched holdout, positive lower-bound EV and account risk≤0.5%.

## ExistingPositionReviewPlanV2

Required only for `existing_position_review`:

- position evidence
- original and remaining horizon
- original thesis
- cost-basis status and whether exit would realize a loss
- thesis status and invalidations
- hold/trim/exit decision
- explicit next review time

A rotation requires a separate recommendation.

## OutcomeReviewV2

Append outcomes without changing the original recommendation. Keep observation, execution and outcome lifecycle separate. Record reviewed/review-due times, target/stop first-trigger times, MFE, MAE, fill/slippage and event gap. Never calculate real P&L if no real execution occurred.

## No qualified candidate

Set the mode in `no_qualified_candidate_modes` and add at least one nearest `rejected_candidate` with:

- mode and symbol
- `current_direct_decision=do_not_enter_now`
- frozen snapshot ID, price and price evidence ID
- rejection reasons, evidence gaps and nearest-pass distance

A rejected candidate is not a RecommendationV2. It must not contain probability, scenarios, targets, price stop or event-exit placeholders.

## Report order

1. rank-1 current direct decision and one-line verdict;
2. ranked primary plus qualified-alternative comparison;
3. research decision;
4. execution decision and real amount;
5. entry/holding/exit or review calendar;
6. probability event, historical validation and scenarios;
7. evidence chain;
8. local data downgrades;
9. audit appendix.
