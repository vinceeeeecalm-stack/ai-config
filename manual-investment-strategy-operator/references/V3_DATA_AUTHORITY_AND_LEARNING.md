# V3 Data Authority and Learning

## PortfolioStateV2 Precedence

Resolve each field independently:

1. A newer user-confirmed completed trade or balance statement.
2. A newer broker, exchange or wallet export/screenshot.
3. The current position override.
4. The legacy ledger for cost, lot, staking and history only.

When timestamps are equal, direct account evidence outranks inference. Never infer deployable cash from a completed sale unless the resulting settled balance is confirmed.

Every resolved field contains:

- `value`
- `as_of`
- `source`
- `confidence`
- `evidence_id`

Every conflict contains:

- `key`
- `selected_evidence_id`
- `rejected_evidence_id`
- `selected_value`
- `rejected_value`
- `reason`

Current fixed baseline until newer evidence:

- `NIGHT = 0`
- `ENA = 0`
- `USDT = 0`
- `us_equity_cash_usd = 0`

## EvidenceSnapshotV2

Generate one immutable snapshot per run. All researchers and candidate validators must reference its `snapshot_id`.

Each numeric evidence item contains:

- `evidence_id`
- `category`
- `symbol`
- `metric`
- `value`
- `unit`
- `as_of`
- `source`
- `source_role`
- `max_age_seconds`

Freshness is computed against `cutoff_at`; stale records remain visible but cannot satisfy `require_fresh=true`. Default limits come from `config/v3_data_source_registry.json`.

| Category | Fresh | Degraded after |
|---|---:|---:|
| Spot/quote | 5 minutes | 15 minutes |
| Options/funding/OI/order book | 15–30 minutes | 60 minutes |
| Daily OHLC/volume | latest completed session/bar | next completed session/bar |
| On-chain/DeFi/fund flow | 24 hours | 72 hours |
| SEC/IR/project announcement | latest known filing/announcement | when a newer official item exists |
| Macro release | latest official release | when the next release is published |

Use primary or official sources where available. A fallback source must be labeled and cannot silently overwrite a conflicting primary source.

Missing one category only degrades conclusions that depend on it. It does not prove that no opportunity exists elsewhere.

## Historical Conditioning

- Freeze the decision timestamp and use only data available at that time.
- Enter simulations on the next realistically tradable bar, not the signal bar.
- Include spread, fees and slippage.
- Align cross-asset series by timestamp.
- When one bar touches target and stop, use the documented conservative ordering.
- Report sample size, market regime, setup definition and holdout use.
- Do not use final holdout observations to select parameters.

FOMC and event analogs must use prior events only. Small event samples widen uncertainty; they do not become fabricated point probabilities.

## Recommendation Immutability

After validation and append:

- do not change decision price
- do not change probability
- do not change entry/exit window
- do not change evidence snapshot
- do not rewrite the original thesis after observing the outcome

Corrections create a new recommendation with `supersedes_recommendation_id` and a reason.

## Review Calendar

Use explicit ISO timestamps in this order:

1. `review_due_at`
2. `latest_exit_or_review_at`
3. `latest_exit_or_review_date`
4. `entry_deadline`

V2 must never parse day counts from natural-language `time_window`. Legacy fallback may parse simple duration forms such as `2-6 calendar days`; strings containing calendar years or ISO dates are not duration expressions.

## Controlled Learning

Promote immediately only when the gap is deterministic:

- schema bug
- security or privacy bug
- wrong data precedence
- incorrect date calculation
- false-green validation
- missing required source timestamp

For analytical heuristics, require either:

- three independent forward incidents, or
- a documented historical/holdout test with adequate evidence.

Every promoted change records:

- `omission`
- `root_cause`
- `general_rule`
- `affected_components`
- `validation_result`

The machine-readable promotion ledger is `config/v3_learning_promotions.json`.

Single-day prices and one-off news remain in the daily report or research backlog.

## Calibration

Group outcomes by:

- request mode
- asset class
- setup ID
- forecast horizon
- probability event

Report resolved count, hit/failed/not-triggered counts, Brier score when applicable, MFE/MAE and target-before-stop rate. Do not pool long-term DCA with tactical or earnings-event trades.
