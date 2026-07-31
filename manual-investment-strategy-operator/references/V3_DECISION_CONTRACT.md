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

Sample rules: `n<10` is `judgment_only`; `10–29` may be `wide_interval`; only `n≥30` without lookahead leakage may be `calibrated`.

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

1. current direct decision;
2. research decision;
3. execution decision and real amount;
4. entry/holding/exit or review calendar;
5. probability event and scenarios;
6. evidence chain;
7. local data downgrades;
8. audit appendix.
