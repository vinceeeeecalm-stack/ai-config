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
- `max_active_action`
- `live_orders_enabled = false`
- `private_api_used = false`

Allowed `max_active_action`:

- `watch`
- `paper_only`
- `risk_alert`

## Candidate Fields

- `candidate_id`
- `symbol`
- `asset_class`
- `request_mode`
- `setup_id`
- `probability_event`
- `probability_type`
- `probability_value_or_range`
- `sample_size`
- `current_signal`
- `decision_price`
- `price_as_of`
- `evidence_ids`
- `entry_observation`
- `targets`
- `stop`
- `time_stop`
- `event_plan`
- `liquidity`
- `derivatives_or_options`
- `financing_dilution`
- `historical_conditioning`
- `validation_status`
- `paper_status`
- `blockers`
- `risk_adjusted_path` when closed-bar history is available
- `max_candidate_action`

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

`probability_event` identifies one outcome and horizon. Bull/Base/Bear scenario weights are separate fields and sum to 100.

- `judgment_only`: fewer than 10 comparable observations
- `wide_interval`: 10–29
- `calibrated`: at least 30, with no lookahead and an untouched holdout

Paper win rate, selection score and execution readiness are not forecast probabilities.

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
