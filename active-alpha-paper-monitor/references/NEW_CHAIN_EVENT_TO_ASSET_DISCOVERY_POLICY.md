# New Chain Event-to-Asset Discovery Policy

## Purpose

Capture time-sensitive opportunities created when a major broker, exchange,
wallet, protocol or infrastructure provider launches a permissionless chain.
The workflow must discover assets before they appear on a centralized exchange
or a static symbol list.

This is an event-to-candidate discovery contract:

`official event -> chain watch -> DEX pools -> narrative/entity mapping ->
security and capacity gates -> watch/paper handoff`

It never authorizes a live order.

## Event Watch

Monitor official newsroom, project blog, documentation and verified public
accounts for:

- public mainnet, L2/L3, appchain or chain migration;
- canonical bridge, wallet or RPC availability;
- day-one DEX, oracle, stablecoin, launchpad or trading-tool support;
- large distribution channels adding the new chain;
- material regulatory restrictions or security incidents.

An event announcement creates `event_watch`. A verified public-mainnet launch
creates a 72-hour `ecosystem_watch`, even when no token candidate exists yet.
Extend the watch only with new official or on-chain evidence.

## Cross-Venue Discovery

Do not require a Binance or other CEX pair. During an active ecosystem watch,
attempt:

- GeckoTerminal new and trending pools;
- DEX Screener pair/token data;
- the official chain explorer or Blockscout;
- canonical DEX, bridge and launchpad sources;
- project website and verified social links.

Source failures are local degradations. Preserve the event watch and show which
venue could not be queried.

For each pool retain:

- chain ID, network slug, DEX and pair address;
- token contract and quote asset;
- pair creation time;
- price, liquidity, 5m/1h/6h/24h volume and change;
- transactions, buys/sells and unique traders where available;
- market cap/FDV and volume-to-liquidity ratio;
- exact source, capture time and freshness.

## Entity and Narrative Mapping

Build an explicit derivation chain. Example:

`Robinhood official mainnet -> Robinhood Chain -> historical "Cash Cat"
identity -> CASHCAT contract -> Uniswap pool -> liquidity/participation
expansion`

State affiliation separately:

- `official_asset`
- `official_ecosystem_partner`
- `unaffiliated_narrative_asset`
- `unknown_affiliation`

Narrative similarity is not affiliation. Unknown affiliation blocks
`paper_only`.

## Security and Capacity Gate

Before `paper_only`, require:

- the same contract verified by at least two independent sources;
- clean honeypot/sellability result;
- disclosed buy/sell tax;
- no uncontrolled mint, blacklist, pause or confiscation privilege;
- LP status and concentration understood;
- deployer and top-holder concentration checked;
- sufficient liquidity for the proposed paper notional;
- real trader participation, not only transaction count;
- no evidence that one wallet or coordinated cluster dominates volume.

Missing security or holder evidence limits the candidate to `watch`. A failed
sellability, malicious privilege or capacity check becomes `risk_alert`.

## Catalyst Derivation Output

Every discovered candidate must explain:

1. `originating_official_event`
2. `ecosystem_transmission`
3. `asset_selection_reason`
4. `market_confirmation`
5. `narrative_or_utility_link`
6. `affiliation_status`
7. `security_capacity_status`
8. `why_now`
9. `what_invalidates`
10. `discovery_latency`

Keep `FACT`, `DERIVED` and `JUDGMENT` separate. Do not translate narrative
strength, ranking score or social heat into a forecast probability.

## Action Ceiling

| Evidence state | Maximum action |
|---|---|
| Event announced, product unknown | `watch` |
| Mainnet live, no verified asset | `watch` |
| Pool discovered, market evidence incomplete | `watch` |
| Strong market evidence, security/holder evidence missing | `watch` |
| Market, identity, security and capacity gates pass | `paper_only` |
| Sellability, privilege or capacity failure | `risk_alert` |

Manual V3 must create a separate tactical recommendation before any theoretical
`enter_now` or `small_entry_now` conclusion. Real execution still requires
settled cash and human confirmation.

## CASHCAT Incident Replay

The frozen replay must prove:

- 2026-06-02: Robinhood product event announcement creates only event watch.
- 2026-07-01: official public mainnet creates ecosystem watch.
- Once a CASHCAT pool is visible, it appears as a discovered candidate even
  without a Binance pair.
- Missing contract/holder evidence prevents hindsight promotion beyond watch.
- Later media returns never mutate the earlier record or become a fabricated
  entry probability.

Treat “new chain cultural leader may gain reflexive attention” as an analytical
heuristic. It needs three independent forward incidents or a documented
holdout test before promotion.
