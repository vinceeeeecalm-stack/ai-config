# Pre-Entry Downside and Capital Risk Gate

## Purpose

This gate moves drawdown control before a US tactical entry. A bullish target is
not enough. Before a candidate can become a main action, the report must test
whether the position is more likely to hit the stop first, whether an overnight
gap can exceed the planned loss, and whether financing, dilution, valuation or
event risk can invalidate the setup.

The gate applies to US tactical single stocks, leveraged ETFs, tactical adds,
and rotation candidates. It also applies when an existing tactical holding is
being considered for averaging down or continued holding after a material loss.

## Required Risk-First Card

Every in-scope candidate must provide:

| Field | Plain meaning |
|---|---|
| `target_before_stop_probability_pct` | Probability that the target is reached before the stop within the stated window. |
| `stop_before_target_probability_pct` | Probability that the stop is reached before the target. |
| `expected_mae_pct_5d` / `10d` / `20d` | Expected maximum adverse excursion: the worst interim drawdown commonly seen before the review date. |
| `stress_gap_pct` | A severe overnight or event-gap loss assumption. |
| `reward_risk_ratio` | Expected upside divided by planned downside. |
| `binary_event_calendar` | Earnings, FOMC, CPI/PCE, court, financing or other binary events in the next ten trading days. |
| `financing_and_dilution_snapshot` | SEC filings, ATM/S-3, convertible debt/preferred shares, new debt, share-count growth and stock compensation. |
| `capital_funding_gap_status` | Whether available funding plausibly covers the announced buildout and operating plan. |
| `contract_quality_snapshot` | Contract start date, customer credit/concentration, build cost, financing cost, renewal terms and time to cash generation. |
| `valuation_expectation_risk` | Whether the price already assumes unusually strong execution or growth. |
| `price_extension_state` | 20-day, 60-day and distance-from-52-week-high context. |
| `positive_news_failure_count` | Number of verified positive catalysts that failed to produce durable relative strength. |
| `position_size_from_stress_loss` | Size derived from a stress loss budget, not from desired profit. |
| `what_would_change_my_mind` | Observable evidence that upgrades or invalidates the decision. |

Missing downside fields are not neutral. Missing `target_before_stop`, MAE,
stress-gap, event-calendar or financing data limits the candidate to `watch`.

## Decision Rules

1. `forecast_probability_pct` and execution readiness cannot pass this gate by
   themselves. If `stop_before_target_probability_pct` is greater than or equal
   to `target_before_stop_probability_pct`, the candidate is `no_deploy`.
2. A full tactical position requires `reward_risk_ratio >= 2.0`. A ratio from
   `1.5` to `<2.0` is at most `small_probe_review`; below `1.5` is `no_deploy`.
3. A binary event inside five trading days blocks full-size entry unless the
   report explicitly underwrites bull/base/bear outcomes and the stress gap.
   Without that work, the maximum action is `paper_only` or `watch`.
4. A candidate up more than 30% in 20 trading days or 50% in 60 trading days
   while within 10% of its 52-week high is `extended_chase_risk`. The main
   action must wait for a pullback or a new evidence-backed base.
5. For an unprofitable, capital-intensive company, any two severe capital flags
   block a new add. Severe flags include share-count growth above 10% year over
   year, a recent ATM/S-3/convertible/preferred issuance, material new secured
   debt, an uncovered funding gap, or stock compensation above 10% of revenue.
6. A material SEC financing filing after entry forces a fresh underwriting in
   the next manual dispatch. The old recommendation cannot be reused.
7. Contract headline value cannot be treated as current equity value. For major
   leases or orders, discount for construction time, customer concentration,
   financing cost, completion risk and termination/renewal terms.
8. If verified positive news is followed by a high-volume decline or material
   underperformance, mark one `positive_news_failure`. One failure is orange;
   two in the same setup are red and block averaging down.
9. Position size must be derived from maximum acceptable portfolio loss divided
   by the stress loss estimate. A desired monthly return cannot increase size.
10. A breached stop or invalidation is locked. It cannot be moved farther away
    to preserve the old trade. Continuing the position requires a new thesis,
    new recommendation ID, fresh downside card and explicit human confirmation.

## Leveraged ETF Addendum

- State that the product targets a multiple of the underlying index's **daily**
  return; it does not promise the same multiple over several days.
- Analyze the underlying index, breadth, rates and major components, not just the
  leveraged ETF chart.
- Default review is 1-3 trading days. Carrying beyond five trading days requires
  daily recertification; ten trading days is an absolute maximum.
- A stop price is not a guarantee. The card must show a gap scenario and the
  maximum loss if the market opens through the stop.
- Earnings clusters, FOMC and inflation data require explicit event approval.

## Existing-Position Incident Rule

When a user confirms a real holding but no recommendation record exists, create
an `incident_backfill` before issuing a new recommendation on the same rail. The
backfill records what is known, what is inferred, the missing original thesis,
the observed drawdown and the process failure. It must not fabricate an entry
reason or probability.

For the July 2026 APLD/SOXL review, see
`US_EQUITY_DRAWDOWN_INCIDENT_REVIEW_20260720.md`.

## Action Ceiling

| Gate result | Maximum action |
|---|---|
| Complete, favorable, no severe flags | Continue to Target Achievement Gate |
| One material warning | `conditional_action / small_probe_review` |
| Missing downside or filing evidence | `watch` |
| Stop-first probability not lower than target-first probability | `no_deploy` |
| Two severe capital flags or repeated positive-news failure | `block_new_add` |

This gate never places an order. Every action still requires human confirmation.
