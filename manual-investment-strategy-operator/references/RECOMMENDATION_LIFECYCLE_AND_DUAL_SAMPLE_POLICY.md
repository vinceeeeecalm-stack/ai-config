# Recommendation Lifecycle and Dual Sample Policy

## Purpose

Manual owns the final decision, but a recommendation is not complete when it is
written. Every frozen judgment must remain traceable until it is reviewed or
closed.

The control product may persist and monitor these contracts, but it must not
invent missing decision-time evidence.

## Required lifecycle

Every new formal recommendation must produce a `RecommendationLifecycleV1`:

```text
evidence_frozen
→ decided
→ monitoring
→ entry_triggered / not_triggered
→ position_open / no_execution
→ target / stop / time_exit / review_due
→ outcome_draft
→ reviewed
→ closed
```

Required fields:

- `lifecycle_id`
- `recommendation_id`
- `rail`
- `request_mode`
- `strategy_family`
- `entry_mode`
- `strategy_version`
- `market_regime`
- `goal_refs`
- `evidence_snapshot_id`
- `frozen_at`
- `monitoring_contract`
- `execution_state`
- `outcome_state`
- `next_check_at`
- `review_due_at`
- `supersedes_id`

An active lifecycle without `next_check_at` is a contract failure. Tactical and
event recommendations must also have an entry deadline, target, stop and time
exit. Long-term DCA and existing-position decisions use a thesis review date,
concentration condition and thesis invalidation; they do not receive invented
short-term stops.

Superseding a recommendation creates a new lifecycle linked by
`supersedes_id`. It never overwrites the old decision, evidence or path.

## Daily ordering

The daily run must process old judgments before discovering new symbols:

```text
load active lifecycles
→ update price paths and account evidence
→ evaluate entry, target, stop, expiry and thesis conditions
→ create due outcome drafts
→ scan new opportunities
→ freeze one shared EvidenceSnapshotV2
→ write new decision or formal no-action
→ schedule next checks
→ update sample ledgers and report
```

The absence of a new candidate does not skip the old-lifecycle sweep.

## Dual sample ledgers

### ObservationSampleV1

Includes every frozen judgment:

- accepted and rejected candidates;
- not-triggered opportunities;
- hold, pause-add and no-action decisions;
- superseded decisions.

It may be used for coverage, trigger rate, filter effectiveness, missed
opportunities, no-action quality and calibration. Legacy records without a full
decision contract remain `legacy_observation` and must not enter return or win
rate metrics.

### TradeSampleV1

Includes only Paper fills or owner-confirmed live fills with:

- strategy version and entry mode;
- executable entry and exit;
- fees and slippage;
- MFE, MAE and holding time;
- unambiguous outcome or an explicit `ambiguous_path`.

Paper and live capital, Crypto and US equities, and long-term and tactical
strategies remain separate cohorts.

## Optimization order

The decision order is:

```text
cash, evidence and hard-risk gates
→ monthly / 5-year / 10-year goal contribution
→ net expectancy, drawdown, Sharpe and Sortino
→ win rate, trigger rate and execution quality
```

Sharpe, Sortino and Information Ratio are secondary timing and ranking evidence.
They cannot establish project quality, authorize execution or replace the
long-term thesis.

## Review and learning

`MarketPathObservationV1` may mark market triggers, but a market trigger is not
an owner fill. Live P&L requires both an `ExecutionReceipt` and post-trade
portfolio reconciliation.

`OutcomeReviewV2` appends classification and attribution without editing the
source recommendation. Rule promotion requires either three independent
forward recurrences or historical evidence plus a separate holdout. A single
win or loss cannot change the formal strategy.

Legacy records must be assigned one of:

- `evaluable_legacy`
- `legacy_observation`
- `audit_only`

Missing probabilities, frozen prices, evidence or dates must never be created
with hindsight.

