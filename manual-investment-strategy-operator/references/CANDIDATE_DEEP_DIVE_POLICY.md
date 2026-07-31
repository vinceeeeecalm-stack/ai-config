# Candidate Deep Dive Policy

## Purpose

This policy forces every recommended asset to be decision-ready. A candidate cannot be presented as a main action unless the report explains the entry, exit, timing, probability, execution readiness, data quality, and failure conditions.

It applies to:

- US equity tactical alpha candidates.
- Crypto tactical candidates.
- DCA pairs.
- Long-term core or satellite additions.
- Protected-hold or trim signals.

## Candidate Deep Dive Gate

Every candidate must produce a `candidate_card` before it can appear in an action list.

Required fields:

| Field | Meaning |
|---|---|
| `candidate_type` | `us_tactical` / `crypto_tactical` / `dca` / `long_term` / `hold_trim` |
| `rail` | `us_equity_rail` / `crypto_rail` / `external` |
| `symbol_or_pair` | Ticker or pair |
| `entry_zone` | Buy/add zone, or `no_new_entry` for hold-only |
| `entry_deadline` | Latest time to act before the setup expires |
| `target_price_or_scenario` | Target price for tactical trades, or scenario target for DCA/long-term |
| `target_time_window` | Tactical window or DCA review period |
| `forecast_probability_pct` | True probability of reaching the target within the window |
| `execution_readiness_score` | 0-100 execution readiness points, not probability |
| `probability_basis` | Historical sample, event analog, walk-forward/paper, multi-source data, or why insufficient |
| `target_before_stop_probability_pct` | Probability that the target is reached before the stop. |
| `stop_before_target_probability_pct` | Probability that the stop is reached before the target. |
| `expected_mae_pct_5d / 10d / 20d` | Common worst interim drawdown before each review horizon. |
| `stress_gap_pct` | Severe overnight or event-gap loss assumption. |
| `reward_risk_ratio` | Expected upside divided by planned downside. |
| `binary_event_calendar` | Earnings, macro or company events within ten trading days. |
| `financing_and_dilution_snapshot` | SEC-backed debt, ATM, convertibles, preferred stock, share count and stock compensation. |
| `capital_funding_gap_status` | Whether funding plausibly covers the operating/buildout plan. |
| `contract_quality_snapshot` | Contract timing, customer concentration, build cost, financing cost and time to cash generation. |
| `valuation_expectation_risk` | Risk that strong future execution is already priced in. |
| `position_size_from_stress_loss` | Size set from a loss budget under stress, not desired return. |
| `stop_or_invalid` | Stop loss, thesis break, or setup invalidation |
| `take_profit_or_review` | Take-profit zone or next review rule |
| `latest_exit_or_review_date` | Latest forced exit or review date |
| `position_size_plan` | Amount, shares, percent of pool, or DCA split |
| `data_quality_status` | `verified` / `disputed` / `stale` / `missing` |
| `action_allowed` | `execute_now` / `conditional_action` / `watch` / `paper_only` / `no_deploy` |
| `goal_role` | `monthly_tactical_return` / `long_term_10x_dca` / `protected_hold` / `cash_wait` |
| `one_week_test` | 1-5 trading day test: trigger, target, stop, review date |
| `two_week_trend` | 6-10 trading day trend validation and failure handling |
| `monthly_goal_path` | How the action helps or protects the monthly tactical goal |
| `trade_clock_state` | `fresh_pullback_entry` / `extended_chase_risk` / `trend_intact_wait_pullback` / `breakout_confirmed_small_probe` / `thesis_broken_no_entry` |
| `price_as_of / price_source / data_age_minutes / data_freshness_status` | Exact market-data cutoff, source and freshness. |
| `entry_window_start / entry_window_end` | Calendar start and expiry of the setup in Asia/Shanghai. |
| `allowed_session` | `us_regular_session_only` / `crypto_24x7_closed_bar_only` / `review_only_no_trade`. |
| `entry_trigger / no_entry_if_not_triggered` | Exact price/confirmation rule and hard no-buy state before it occurs. |
| `expected_holding_days` | Minimum and base holding expectations in both calendar and trading days. |
| `max_holding_days` | Maximum holding in calendar and trading days. |
| `target_1_price_or_scenario / target_1_evaluation_window` | First take-profit or scenario checkpoint and date window. |
| `target_2_price_or_scenario / target_2_evaluation_window` | Second take-profit or scenario checkpoint and date window. |
| `time_stop` | Exit/review rule when price does not progress in time. |
| `event_exit_date / event_handling_plan` | Earnings, FOMC, unlock, regulatory or other event handling and cutoff. |
| `post_exit_state / auto_relay_forbidden` | Sold proceeds become cash pending manual reallocation; no automatic relay. |
| `current_deployable_cash_usd / settlement_constraint` | Real settled rail cash and T+ settlement or exchange availability constraint. |
| `execution_action / observation_action` | Real-deployment ceiling and independent watch/paper/alert state; `no_deploy` must not erase an observation signal. |
| `observation_trigger / impulse_monitoring_required / impulse_check_deadline` | Exact closed-bar signal and how long it remains eligible for read-only monitoring. |
| `candidate_coverage_matrix` | Per-candidate market, official-event, social/news and asset-risk routing proof with timestamps and sources. |
| `baseline_frozen / historical_baseline_mutation_forbidden` | Preserve the original forward-looking record; later evidence creates a new linked signal. |
| `current_direct_decision` | `enter_now / small_entry_now / do_not_enter_now`; this is the primary theoretical decision at the frozen current price. |
| `current_state_already_evaluated / primary_action_is_future_trigger` | Prove the recent path was already evaluated and forbid a future trigger from replacing the current decision. |
| `decision_price_ceiling / decision_valid_until` | Maximum acceptable current price and expiry of the direct decision. |
| `current_state_vector` | Frozen returns, trend, volatility, volume, liquidity, derivatives and cross-asset regime. |
| `risk_adjusted_path` | Closed-bar Sharpe/Sortino/information-ratio/drawdown path quality, lane, provenance, base-versus-adjusted rank and paper-only promotion status. |
| `historical_cycle_conditioning` | Predefined analog features, sample, target-first/stop-first, MFE/MAE, pullback-before-target and missed-upside statistics. |
| `macro_event_conditioning` | FOMC/earnings/regulatory stage, expected outcome, consecutive-event state, regime-matched analogs and event windows. |
| `derivatives_and_flow / buy_now_vs_wait` | Options or funding/OI/flow evidence and a probability-weighted buy-now-versus-wait comparison. |
| `discovery_origin / catalyst_derivation_chain` | Whether the asset came from a known-market scan or an official event; show event → ecosystem → asset → market confirmation → decision. |
| `affiliation_status / venue_discovery` | Separate official affiliation from narrative association and state how a CEX or DEX-only asset was discovered. |
| `new_chain_security_gate` | For new-chain assets: contract, honeypot/sellability, tax, owner privileges, LP, holder/deployer concentration and capacity status. |

## Probability Rules

- `forecast_probability_pct` is the true target-achievement probability, not confidence language.
- `execution_readiness_score` is operational readiness, not probability.
- US equity tactical and crypto tactical `execute_now` requires both `forecast_probability_pct >= 80` and `execution_readiness_score >= 80`.
- US equity tactical candidates must also pass `PRE_ENTRY_DOWNSIDE_AND_CAPITAL_RISK_GATE.md`. Target probability cannot override a higher stop-first probability, weak reward/risk, event gap or capital-risk block.
- If historical sample, paper/walk-forward, multi-source confirmation, or catalyst verification is missing, the candidate cannot be higher than `watch`.
- If the asset has already made an extreme move and the next target has poor risk/reward, probability must be lowered.

## Tactical Candidate Requirements

For short-term US equity or crypto candidates, the report must include:

- 1D / 5D / 20D / 60D trend and relative strength.
- Volume or quote-volume change versus recent baseline.
- Key support, resistance, entry zone, stop, and target.
- Catalyst split into confirmed fact, market rumor, social heat, and price reaction.
- Benchmark comparison:
  - US equity: compare with the current dynamically identified `deployable_tactical_position`.
  - Crypto: compare with adding `SOL`, `ADA`, or holding current `ETH/lcETH`.
- Risk/reward check: expected upside should be at least 2x the planned downside. If not, downgrade.
- For US equities, estimate 5/10/20-day maximum adverse excursion, target-before-stop and stop-before-target probabilities, and a gap-through-stop loss before calculating size.
- For capital-intensive or unprofitable companies, inspect current SEC filings for debt, ATM/S-3, convertibles, preferred shares, share-count growth, stock compensation and the remaining funding gap.
- A goal-linked time plan. For US tactical candidates this must answer: what happens this week, what invalidates the trade within two weeks, and how the trade contributes to the monthly tactical return target.
- A distinction between `trend_follow_small_probe` and `advantage_reentry` when the user has already sold at a favorable price. The main action should favor `advantage_reentry`; `trend_follow_small_probe` must stay small and conditional.
- If the opportunity originated from a platform/chain launch, a complete
  `FACT -> DERIVED -> JUDGMENT` catalyst derivation. Do not start with the token
  and retrofit the news after it rises.
- DEX-only candidates must be compared by contract and pair address, not ticker
  alone. Unknown affiliation or missing sellability/holder/LP evidence blocks
  `enter_now` even when liquidity and momentum are strong.

## DCA and Long-Term Requirements

DCA and long-term candidates do not need 80% short-term target probability, but must include:

- `thesis_confidence_pct`.
- `long_term_thesis_score` with the main scoring drivers.
- `goal_gap_contribution`: `accelerator / neutral / drag / tail_convexity`.
- `macro_regime_dca_pace`: `accelerate / normal / split_more / wait_for_pullback`.
- `micro_thesis_score` and the top drivers.
- `staking_compounding_summary` for stakeable assets.
- `missing_data_downgrade` with `no impact / smaller size / conditional only / block`.
- `dca_batch_plan`.
- 1-year, 3-year, and 5-year scenarios.
- Thesis break conditions.
- Next scheduled review date.
- Whether staking yield, unlock delay, and liquidity affect the recommendation.
- A concise data quality summary with source timestamps and downgrade reasons.
- A goal-impact note explaining whether this recommendation improves the 5-year/10-year 10x path.
- For stakeable assets, the 5-year/10-year compounding multiplier and the price multiple still required to reach 10x after staking.

Short-term `paper_only` or `no_deploy` status does not automatically block a long-term small DCA candidate. Long-term DCA must be judged by thesis quality, data quality, liquidity, supply pressure, portfolio fit, and goal impact.

## Output Discipline

- The final action list must be derived from candidate cards only.
- When the user requests one current recommendation, apply `SINGLE_BEST_CANDIDATE_DEFAULT_POLICY.md`: rank the full internal universe, expose exactly one primary candidate, and keep runners-up to a one-line rejection note.
- Every probability must declare `probability_type` (`historical_path` / `event_estimate` / `scenario_weight`), sample size, formula or adjustment log, and confidence limitation. If a reproducible base rate is unavailable, use a range and mark it `judgment_only`; do not print a precise single-point success probability.
- Separate the evidence chain into `FACT` (source-backed observation), `DERIVED` (shown calculation), and `JUDGMENT` (explicit inference). A judgment may affect sizing or ranking but must not be phrased as a verified fact.
- Candidate cards must pass `scripts/recommendation_execution_calendar_gate.py`; a missing calendar, freshness, holding-period, event, funding, signal-continuity, candidate-role coverage or post-exit field caps the action at `no_deploy`.
- Candidate cards must also pass `scripts/historical_cycle_event_conditioning_gate.py`. The main user-facing action may not be a future trigger: it must say whether the frozen current state is investable now. A new state requires a new timestamped record.
- A candidate with missing entry, exit, probability, or invalidation cannot be a main recommendation.
- If no candidate passes the gate, the report must say `no_action` or `watch`, then name the trigger that would reopen the trade.
- US equity candidate cards must include `why_better_than_current_tactical_position`; they must not use a hardcoded benchmark such as SOXL unless SOXL is explicitly the current identified tactical position.
