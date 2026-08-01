# US Crypto Legislative Event Radar Policy

## Purpose

Capture market-moving United States crypto legislation before the event window
without confusing a committee markup, a House vote, a Senate floor vote,
reconciliation or a presidential signature.

This is an event-discovery and verification contract:

`bill watch -> chamber state -> official agenda -> secondary expectation ->
affected assets -> market confirmation -> watch/paper handoff`

It never authorizes a live order.

## Required State Machine

Track each bill independently through:

1. `introduced`
2. `committee_markup_scheduled`
3. `committee_advanced`
4. `floor_schedule_pending`
5. `floor_vote_scheduled`
6. `floor_passed`
7. `other_chamber_or_reconciliation_pending`
8. `presented_to_president`
9. `signed_or_vetoed`

Always record `current_chamber`. House passage cannot be described as Senate
passage. Committee advancement cannot be described as a scheduled floor vote.

## Source Order

Use primary sources before market commentary:

1. House Clerk roll calls and published House floor schedule.
2. Senate floor schedule, floor activity and official vote schedule.
3. Relevant House/Senate committee markup pages and official bill text.
4. Congress bill-status pages or API when accessible.
5. Named lawmakers' official releases for negotiation context.
6. Reputable reporting and prediction markets only as secondary expectations.
7. Social posts, screenshots and reposts only as unconfirmed claims.

Each source must retain `source_url / checked_at / source_tier / chamber`.
Robots, paywalls or endpoint failures are local degradations and must be shown.

## Vote Verification Gate

Use only these labels:

- `official_floor_vote_scheduled`: the bill ID or title appears on a published
  official floor schedule with a date or vote window.
- `official_committee_event_scheduled`: an official committee agenda lists the
  bill; this is not a floor vote.
- `not_on_published_floor_schedule`: current House and/or Senate schedules were
  checked and the bill is absent.
- `expected_window_unconfirmed`: lawmakers or reputable reporting discuss a
  possible window, but no official floor item exists.
- `rumor_or_secondary_claim`: the timing comes only from social/secondary text.
- `schedule_unavailable`: official agenda evidence could not be obtained.

Never convert “could start consideration,” “leaders want a vote,” “next week,”
or a prediction-market move into `official_floor_vote_scheduled`.

## Weekend-to-Monday Handoff

Every Saturday/Sunday morning and evening scan must look forward at least 72
hours and check:

- House floor schedule;
- Senate floor and vote schedule;
- relevant committee schedule;
- bill stage and remaining procedural steps;
- material text, ethics, DeFi, stablecoin or jurisdiction disputes;
- prediction-market or industry expectations, labeled secondary;
- crypto price breadth, spot volume, funding/OI and affected-asset response.

Missing either chamber's official schedule makes the legislative event layer
`degraded`. It must not erase market scanning, but it blocks claims that a vote
is confirmed or that no legislative catalyst exists.

## Asset Transmission

Map legislation to assets explicitly:

- broad market structure: BTC, ETH, SOL and liquid sector beta;
- exchange/broker registration: COIN, HOOD and related listed equities;
- stablecoin provisions: USDC ecosystem, CRCL and payment/RWA infrastructure;
- DeFi provisions: affected protocols only after text-level confirmation;
- token classification: named or clearly covered assets only.

The derivation must separate:

- `FACT`: bill stage, text, official schedule and market data;
- `DERIVED`: which business model or token category is affected;
- `JUDGMENT`: expected direction, magnitude and timing.

Legislative importance is not a price probability. A candidate still needs
price, volume, liquidity, risk and historical-event checks.

## Report Contract

Output:

- `bill_id / bill_name`
- `current_chamber / current_stage / remaining_steps`
- `official_schedule_status / scheduled_at`
- `official_sources / secondary_claims / source_failures`
- `lookahead_start / lookahead_end / next_review_at`
- `affected_assets / transmission_logic`
- `market_confirmation_status`
- `headline_gap_risk`
- `what_changed_since_prior_scan`
- `what_invalidates`
- `max_active_action`

Unconfirmed timing caps the legislative signal at `watch`. Confirmed scheduling
may support `paper_only` only after independent market and risk gates pass.

## CLARITY July 2026 Incident Replay

The frozen replay must prove:

- House passage on 2025-07-17 does not satisfy Senate passage.
- Senate Banking Committee advancement on 2026-05-14 establishes
  `committee_advanced`, not a floor vote.
- A secondary “Monday vote” claim cannot override published schedules.
- If the July 27 House schedule is pro forma and the Senate's published vote is
  a nomination, the result is `not_on_published_floor_schedule`.
- The event remains a material `watch` because negotiations or a later schedule
  change can still move crypto.
- Later price gains never rewrite the earlier event status or create a
  fabricated entry probability.
