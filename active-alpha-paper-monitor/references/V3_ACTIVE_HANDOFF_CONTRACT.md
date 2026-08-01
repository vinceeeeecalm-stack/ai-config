# V3 Active Handoff Contract

## Purpose

Transfer opportunity evidence from Active to Manual without transferring execution authority.

## Required Top-Level Fields

- `schema_version = active-handoff-v3`
- `handoff_id`
- `generated_at`
- `snapshot_id`
- `snapshot_as_of`
- `runtime_mode`
- `candidates`
- `blocked_candidates`
- `data_gaps`
- `research_committee_status`
- `discovery_top3`
- `ranked_historical_comparison`
- `stage_timings`
- `committee_requirement`
- `max_active_action`
- `live_orders_enabled = false`
- `private_api_used = false`

Allowed `max_active_action`:

- `watch`
- `paper_only`
- `risk_alert`

## Discovery Candidate Fields

- `symbol`
- `asset_class`
- `market_time / current_price`
- `return_1m_pct / return_5m_pct / return_15m_pct`, or `return_1d_pct / return_5d_pct`
- `turnover / relative_volume / trade_count / vwap`
- `spread_bps / depth_bid_usd / depth_ask_usd`
- `anchor_state` including market, BTC and ETH where relevant
- `confirmed_catalyst_present`
- `discovery_score`
- `ranking_reason / hard_rejection_reasons`

Validated Top3 must add `setup_id`, current signal, entry observation, target,
stop, liquidity, event status, basic reward/risk, validation status and a
closed-bar risk path. When reproducible history exists, each Top3 item also
adds sample size, out-of-sample win-rate interval, conservative EV, expected
return, Profit Factor, max drawdown, walk-forward stability and evidence IDs.
These are historical comparison fields, not current forecast probability.
Full current probability, financing/dilution underwriting, portfolio cash and
execution decision remain rank-1 Manual fields.

当 `discovery_origin = new_chain_event` 时还必须输出：

- `originating_official_event`
- `ecosystem_transmission`
- `venue_discovery`
- `contract_address / pair_address / chain_id`
- `affiliation_status`
- `catalyst_derivation_chain`
- `security_capacity_status`
- `discovery_latency`
- `why_now / what_invalidates`

没有 CEX 交易对不是 blocker。缺少合约、卖出能力、持仓集中度或 LP
证据时最高为 `watch`；这些字段不得用叙事热度或价格涨幅代替。

Allowed candidate modes:

- `intraday_scalp`
- `tactical_1_7d`
- `event_trade_1_3w`

Active must not create `longterm_dca` recommendations. It may supply long-term facts, but Manual owns long-term ranking and portfolio marginal allocation.

## Shared Snapshot Rule

All candidate numbers must reference evidence IDs in one EvidenceSnapshotV2. If a researcher obtains newer data:

1. append it to the shared snapshot,
2. record the superseded evidence,
3. rerun affected candidate calculations,
4. keep one final snapshot ID for the handoff.

Do not merge independently fetched prices inside committee prose.

## Probability Rule

Discovery and scanner ranking must not emit forecast probability. They emit
only `discovery_score` and, after Top3 validation, `setup_quality_score`.
Probability is estimated only for the frozen Top1 event in Manual.

`probability_event` identifies one outcome and horizon. Bull/Base/Bear scenario weights are separate fields and sum to 100.

- `judgment_only`: fewer than 10 comparable observations
- `wide_interval`: 10–29
- `calibrated`: at least 30, with no lookahead and an untouched holdout

Paper win rate, selection score and execution readiness are not forecast probabilities.

There is no universal 80% entry gate. `n<10` is judgment-only watch/paper;
`n=10–29` uses a wide interval and may support `small_entry_now` only when
conservative EV is positive, reward/risk is at least 2, the realtime signal is
complete and account risk is at most 0.25%; `n>=30` may support `enter_now`
only with an untouched no-lookahead holdout, positive conservative lower-bound
EV, reward/risk at least 2 and account risk at most 0.5%.

## Fast Funnel

1. Batch discovery returns `DiscoveryCandidateV1` Top3 within 15 seconds P95.
2. Only Top3 receive book continuity, catalyst, chase, anchor, basic
   reward/risk and bounded historical-comparison validation; Top1 selection
   target is 45 seconds.
3. Only Top1 receives full research; its card target is 120 seconds and the
   total target is 180 seconds.

For US equities, public screeners and the bounded 1D/5D return vector are
batched before Top3. One-year charts, benchmark paths, the risk-free series,
current-position comparison and news are post-discovery work. A validated
Top1 always emits a complete decision card; unavailable SEC/IR, earnings
expectation, historical analog, derivatives or committee evidence appears as
named blockers instead of a null card.

Discovery never calls probability, portfolio cash, a full financing audit,
the Research Committee, or execution permission. A source failure degrades
only the affected source and cannot be described as an empty market scan when
another source or fallback pool remains available.

Committee tiers are `none` for discovery, two roles for an intraday small
entry, four relevant roles for a 1–7 day full position, and six or more roles
for binary events, major long-term allocations, or new high-risk assets.

## Blocked Candidates

Retain near-miss candidates with:

- `symbol`
- `setup_id`
- `primary_blocker`
- `failed_fields`
- `nearest_pass_distance`
- `next_review_at`
- `price_change_24h_pct`
- `volume_ratio_24h`
- `verified_catalyst_count_24h`
- `evidence_ids`

“No formal candidate” is valid. It must not be represented as “the scanner found nothing” when source failures prevented scanning.

## Manual Arbitration

Manual must independently add:

- current PortfolioStateV2
- portfolio concentration
- real deployable cash
- settlement
- protected holdings
- long-term objective
- final risk gate

Active handoff is evidence, not an order or final recommendation.

Manual converts the handoff into exactly one primary recommendation and zero to
two qualified alternatives. Alternatives are omitted when their historical
quality is insufficient. The same snapshot and strategy/config digest must
produce the same order, and every lower rank must explain its material deficit
versus rank 1.
