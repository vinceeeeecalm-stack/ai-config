# Daily Dual-Window Execution Calendar and Learning Policy

## Purpose

This policy turns a recommendation from background analysis into a time-bounded manual decision plan. It also defines the single daily `08:30 / 23:30` Asia/Shanghai research loop.

It never authorizes live orders, transfers, staking, automatic rotation, private APIs, wallet signatures or external messages.

## Canonical Timezone and Outputs

- Timezone: `Asia/Shanghai`.
- Morning slot: `08:30` every day.
- Evening slot: `23:30` every day.
- One automation must contain both hour slots and branch by local trigger time.
- Morning report: `reports/YYYY-MM-DD-0830-morning-investment-console.md`.
- Evening report: `reports/YYYY-MM-DD-2330-evening-review.md`.
- The evening report must locate the same Beijing date's morning report. A delayed run after midnight may review the previous Beijing date only when the router explicitly identifies it.

## Execution Calendar Gate

Every formal US-equity or crypto candidate, including `watch`, `paper_only` and `no_deploy`, must comply with `config/recommendation_execution_calendar_schema.json`.

The record must state:

1. Recommendation generation time, price timestamp, source, age and freshness status.
2. `entry_window_start`, `entry_window_end`, `allowed_session` and an exact `entry_trigger`.
3. `no_entry_if_not_triggered=true`. Calendar dates are not standing orders.
4. Minimum/base holding expectations in both calendar and trading days through `expected_holding_days`, plus hard maximums through `max_holding_days`.
5. First and second target or scenario, and the evaluation window for each.
6. Price/thesis stop, `time_stop`, event handling, `event_exit_date`, and `latest_exit_or_review_date`.
7. `post_exit_state=cash_pending_manual_reallocation` and `auto_relay_forbidden=true`.
8. Real `cash_rail_source`, `current_deployable_cash_usd`, and settlement constraint. Unsettled proceeds and unconfirmed buying power are zero deployable cash.
9. Separate `execution_action` from `observation_action`, state the closed-bar observation trigger/deadline, and attach a candidate-specific coverage matrix for market, official event, social/news and asset-specific risk roles.
10. Freeze the baseline. A later event or impulse creates a new timestamped signal linked to the baseline; it never rewrites the prior price, probability or action.
11. Store and validate `baseline_snapshot_sha256`; force `live_orders_enabled=false`, `private_api_used=false` and `human_confirmation_required=true`. This calendar gate never authorizes live execution and caps an otherwise valid `execute_now` record at conditional pending downstream gates.

The ordinary manual dispatch must actually run the read-only impulse scanner, merge its symbol-matched observation into candidate cards, and invoke the calendar/signal-continuity validator before recommendation-history publication. A policy statement or standalone self-test is not an end-to-end gate.

Missing any field makes the calendar gate `failed` and caps the recommendation at `no_deploy`. Passing the calendar gate does not pass Research Committee, downside/capital, Double-80, cash or human-confirmation gates.

## 08:30 Morning Branch

The morning run must completely read and obey the applicable installed versions of:

- `manual-investment-strategy-operator`
- `active-alpha-paper-monitor`
- `unified-longterm-alpha-investor`

It must then:

1. Refresh portfolio quantities, costs, both cash rails, staking/lock status, evidence gaps and recommendation history. User-confirmed US-equity cash remains `$0` until a fresh broker source proves otherwise.
2. Refresh macro regime: rates, 10Y/2Y, DXY, volatility, inflation, main equity trend, risk appetite and sector rotation.
3. Refresh crypto market regime: BTC/ETH trend, 1d/7d/30d, funding, OI, liquidations, order-book liquidity, stablecoin/chain/DeFi flows and fresh official/social narrative evidence.
   On Saturdays and Sundays, run Active sibling
   `scripts/us_crypto_legislative_event_radar.py` with at least a 72-hour
   lookahead. On all other days, run it whenever United States crypto market
   structure, stablecoin, DeFi, token-classification or regulatory action is
   active. The report must distinguish committee action, House action, Senate
   action, reconciliation and presidential action.
4. Run position-first risk review before new discovery. CRCL remains protected; APLD or COIN cannot be assumed sold or automatically used as funding.
5. Dynamically scan, then output at most 1–2 US-equity and 1–2 crypto candidates. `今日无合格标的` is a valid and preferred result when gates fail.
6. Freeze every candidate's morning timestamp, price, probability, entry/stop/targets and action grade in the report and recommendation history. Do not overwrite this baseline later in the day.
7. Run `scripts/recommendation_execution_calendar_gate.py` before treating any candidate card as complete.

Every candidate must explain what it does, thesis, price, date/time entry window, trigger, funding source, batch amount, targets, stop, holding period, latest exit/review, event calendar, equity financing/dilution or token unlock, target-before-stop, stop-before-target, MAE/gap pressure and action level.

## 23:30 Evening Branch

The evening run must read the morning report and recommendation history before reading end-of-day outcomes.

If the morning report is missing, the run must not invent or backfill a morning baseline. It may instead review same-Beijing-date ad-hoc recommendations only when their pre-evening `recommendation_id`, generation/price timestamps, probability, entry, targets, stop and action are already frozen in recommendation history. Label this scope `ad_hoc_recommendation_review`, never `morning_review`. Missing morning attribution does not cancel the read-only market-safety and impulse-continuity pass for the day's formal candidates; if neither morning nor a valid same-day frozen record exists, attribution remains blocked but current risk/impulse alerts still run.

For every morning candidate it must calculate or explicitly mark unavailable:

- change from the frozen morning price;
- maximum favorable excursion and maximum adverse excursion since the morning timestamp;
- whether entry triggered, and the first timestamp it triggered;
- target/stop distance and whether either barrier was touched first;
- whether the main trend, event thesis or action grade changed.

It must check new company announcements, SEC filings, financing/dilution, guidance and regulatory news after the morning cutoff. For crypto it must refresh funding/OI, liquidations, order book, chain/stablecoin/DeFi flows, token/unlock/regulatory events and narrative changes.

The evening branch must also refresh published House/Senate floor schedules
for active crypto legislation. A secondary “tomorrow/Monday vote” claim is a
material omission if not investigated, but it remains unconfirmed unless the
official schedule lists the bill. Record both the missed watch item and the
verification result; do not reward the rumor merely because prices later rose.

Every morning judgment must be labeled `maintained / upgraded / downgraded / invalidated / not_triggered`. The evening run must never change the frozen morning entry price or probability. A changed thesis requires a new recommendation ID and a stated reason.

Every candidate must show both action tracks in the evening report: `execution_action` may remain `NO_DEPLOY` because cash/research/event gates fail, while `observation_action` may independently upgrade to `watch / paper_only / conditional_action / risk_alert` on a newly timestamped signal. The latter is not an order and cannot use hindsight to claim the earlier entry triggered.

The evening report must contain a `Morning Omission Audit` that separates:

- one-off market events that belonged only in that day's report;
- missing facts or data sources;
- reusable checklist/schema/gate defects;
- reasoning or timing errors;
- unavailable evidence that could not reasonably have been known at 08:30.

It must generate outcome-review drafts and update recommendation history only where the outcome is auditable. It never places a real order.

## Controlled Skill Learning

Only reusable process improvements may enter a skill:

- schema fields;
- checklists and gates;
- authoritative data sources or freshness rules;
- deterministic tests and audit coverage;
- repeatable error handling.

Daily prices, one-off news, a single winner/loser and hindsight-only observations stay in the daily report or research backlog.

A deterministic omission that breaks an existing hard requirement may be fixed immediately. Noisy or judgmental observations require at least three independent recurrences before promotion to a stable skill rule.

Every skill change must append an audit entry with:

| Field | Meaning |
|---|---|
| `omission` | What the morning or evening process missed |
| `root_cause` | Why the system missed it |
| `general_rule` | Forward-looking reusable rule, written without hindsight |
| `affected_files` | Exact files changed |
| `evidence_count` | Immediate deterministic defect or recurrence count |
| `validation_result` | Tests and smoke result |
| `rollback_plan` | How to revert if forward evidence worsens |

Skill changes must remain minimal and must not convert end-of-day price action into a fake 08:30 signal. After a skill/config/script change, run the Iteration Sync Hook with `--with-smoke`. A failed smoke leaves the iteration incomplete.

## Safety State Machine

`research` → `watch/paper/conditional/no_deploy` → `human review` → `possible future manual action`

On any exit:

`sold` → `unsettled proceeds` → `settled cash` → `cash_pending_manual_reallocation` → `new independent review`

There is no automatic relay from one asset into another.
