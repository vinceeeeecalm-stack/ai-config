# Dynamic Scan Pool Policy

## Purpose

The scan pool must be rebuilt on every active crypto paper run. The configured symbol list is only a baseline and fallback. The effective scan universe should change with live market atmosphere, liquidity, participation, recent price behavior, recovery queue evidence and social/key-person sentiment.

This policy applies to:

- `validation_progress_runner.py --dynamic-scan-pool`
- `fast_crypto_paper_auto_trader.py`
- `daily_crypto_paper_auto_trader.py`
- `sunday_crypto_realistic_paper_loop.py`

It is paper-only. It never authorizes live orders.

## Inputs

Each dynamic scan pool run should use:

- Binance public spot 24h ticker breadth for eligible USDT spot pairs.
- Quote volume, trade count and 24h move for liquidity and participation.
- BTCUSDT and ETHUSDT 24h direction as broad market anchors.
- BTCUSDT/ETHUSDT/SOLUSDT/BNBUSDT short-horizon 1h and 4h anchor changes as the market-atmosphere check.
- Existing open paper symbols, which must always stay monitored.
- Explicit user symbols, which must be preserved.
- Strategy recovery queue symbols, which may be added for retest visibility.
- Latest social/key-person handoff when fresh enough.
- Active new-chain event watches plus cross-venue DEX discovery. These are
  retained as `cross_venue_watch_candidates` even when no Binance pair exists.
- Fresh United States crypto legislative-event handoff, including chamber,
  procedural stage, official floor-schedule status and any unconfirmed timing
  claims. Weekend runs must cover the next 72 hours.

Stablecoins, fiat-like pairs and leveraged tokens must be excluded from new scan candidates. If Binance market data is unavailable, fall back to open symbols, explicit symbols, core liquidity symbols and the configured baseline list.

Binance `/api/v3/exchangeInfo` is the authority for active spot-product
identity.  A filtered `symbols` query can fail for the entire batch when one
historical or non-ASCII symbol violates the request grammar.  This is a batch
transport/input failure, not evidence that every ticker row is ineligible.  In
that case the scanner must request the same endpoint's full exchange snapshot,
filter it locally to the preselected symbols, and record the batch status,
fallback status, full snapshot count and recovered definition count.  Ticker
presence, price availability and volume never substitute for this identity
evidence.  The fallback must retain all active-SPOT, permission, leveraged,
stablecoin, commodity and tokenized-security exclusions.  If both filtered and
full exchangeInfo paths fail, dynamic symbols remain unconfirmed and the run
must identify the static list as a degraded survival fallback.
`isSpotTradingAllowed=true` is not sufficient by itself: `SPOT` must be
explicitly present in `permissionSets` or `permissions`; a missing permission
field fails closed as `spot_permission_missing`.

The Binance universe is the CEX execution-research universe, not the complete
opportunity universe. A verified mainnet, bridge, canonical DEX, wallet or
launchpad event must fan out through
`NEW_CHAIN_EVENT_TO_ASSET_DISCOVERY_POLICY.md`. DEX-only candidates stay in a
separate observation pool until the contract, security, liquidity and
historical-data gates support paper evaluation.

Static asset buckets are only a starting point. If a Binance USDT spot symbol is not in the known bucket map but has enough quote volume, trade count and current movement, the scanner should classify it dynamically as a high-beta or large liquid mover for this run. This prevents new or newly active symbols from being buried as generic `market_mover` names during risk-on or selective-rotation regimes. The dynamic bucket is still only a scan-priority input; it cannot bypass liquidity, current-signal, recovery, capacity or paper-only gates.

## Freshness Gate

V2.46 adds an explicit social freshness gate:

- `validation_progress_runner.py --dynamic-scan-pool` should refresh the social/key-person handoff before building the pool when the latest handoff is older than `dynamic_social_stale_hours` (default 12 hours).
- Direct `fast/daily/sunday` loop runs do not silently trust old social data. If the latest handoff is older than `dynamic_scan_pool_social_max_age_hours` (default 12 hours), social scores are excluded from ranking and the report must show `social_handoff_freshness=stale`.
- A stale or missing social handoff does not stop Binance market-breadth scanning; it only downgrades the sentiment overlay to `neutral_or_missing_social`.
- Stale positive sentiment must never expand the scan pool. Stale risk alerts must not be ignored in real-risk reporting; they should be re-collected through the social monitor before being used as current trade context.

## Market State Machine

The scanner classifies each run into one market regime:

| Regime | Trigger Shape | Pool Behavior |
|---|---|---|
| `risk_off_rebound_watch` | Weak breadth, many hard sellers, or BTC/ETH both under pressure | Contract width; raise BTC/ETH/BNB/TRX-style liquid core floor; cap high-beta and meme slots; only treat rebounds as paper/watch candidates after liquidity and current-signal gates. |
| `risk_on_momentum` | Broad positive breadth, BTC/ETH positive, strong top-20 momentum | Expand width; increase high-beta and large-cap leader slots; keep core anchors; allow confirmed social catalyst names to enter watch/paper evaluation. |
| `selective_high_beta_rotation` | Not broad risk-on, but enough strong gainers and top-volume acceleration | Slightly expand width; prioritize high-beta, volume acceleration and confirmed narratives while preserving core anchors. |
| `mixed_selective` | No clear broad regime | Keep configured width; balance liquid leaders, infrastructure/large-cap names and confirmed catalyst candidates. |

The 24h regime is then adjusted by a short-term anchor overlay:

| Short-Term State | Trigger Shape | Pool Behavior |
|---|---|---|
| `risk_appetite_accelerating` | 1h and 4h anchor baskets both positive, with most anchors rising | If 24h breadth is not risk-off, modestly expand high-beta and momentum candidates. |
| `risk_appetite_fading` | 1h and 4h anchor baskets both negative, with most anchors falling | Downgrade any broad risk-on reading to selective, contract high-beta, and raise core/liquid floors. |
| `rebound_attempt` | 1h anchors positive while 4h still negative | Watch liquid rebound candidates but avoid treating the bounce as a chase signal. |
| `pullback_after_strength` | 1h anchors negative while 4h still positive | Reduce late high-beta chase pressure and require stronger current-signal evidence. |
| `neutral` / `unavailable` | No clear short-term signal or kline unavailable | Use the 24h breadth/liquidity state and social freshness rules without short-term expansion. |

## Sentiment Overlay

Social/key-person information changes ranking and bucket quotas, but it cannot open a paper trade by itself.

| Sentiment State | Behavior |
|---|---|
| `positive_catalyst_cluster` | Expand confirmed social-catalyst watch slots; boost social long scores; in risk-off this remains narrow and cannot override liquidity/recovery/current-signal gates. |
| `risk_alert_cluster` | Contract high-beta and meme caps; raise core/large-cap floors; remove social-catalyst minimum; downgrade affected symbols to watch unless price/liquidity/current-signal evidence independently passes. |
| `light_positive_context` | Small ranking boost only; no quota expansion. |
| `neutral_or_missing_social` | Use market breadth, liquidity, movement, participation and recovery queue only. |

## Width Rules

The effective scan width should be visible in each report:

- risk-off: contract below configured width when protected symbols allow it.
- risk-on: expand above configured width.
- selective rotation: modest expansion.
- positive catalyst overlay: add a small width boost.
- risk-alert overlay: contract width.

Open paper symbols, explicit symbols and recovery queue symbols form a protected floor so existing risk and requested symbols are never dropped from monitoring.

## Bucket Quotas

The pool should not become one-note. It must report selected counts by bucket:

- `core_defensive`
- `large_cap_or_infra`
- `high_beta`
- `meme_liquid`
- `market_mover`
- `social_catalyst`

Risk-off raises minimum core/large-cap slots and caps high-beta/meme slots. Risk-on raises high-beta minimums but keeps core anchors. Selective rotation adds a social-catalyst floor only when sentiment is positive and not dominated by risk alerts.

For bucket accounting, dynamically classified unknown movers such as `high_beta_dynamic` count toward `high_beta`. Stablecoin or fiat-like bases, including USD-stable variants, must not count toward any opportunity bucket.

## Report Requirements

Every dynamic pool report must show:

- selected symbols
- open symbols preserved
- recovery queue symbols preserved
- market regime and atmosphere
- short-term anchor state, 1h/4h average anchor change and overlay decision
- sentiment state and overlay
- social handoff freshness, age, max age and stale/missing reason
- configured max symbols vs effective max symbols
- width multiplier and reason
- pool bias
- bucket min/max slots, selected counts and quota status
- top ranked candidates with score, bucket, 24h move, volume, social long score and social risk score
- active chain-event watches, cross-venue source status and DEX-only candidates
- each DEX-only candidate's event-to-asset derivation and security/capacity blockers

If data is missing or stale, the report must state fallback reason and avoid presenting the static list as a fresh market scan.

When legislation is market-relevant, the report must also state whether the
move is supported by a confirmed official event, an unconfirmed expected
window, or only price action. A vote rumor may expand `social_catalyst` watch
coverage only after the legislative radar runs; it cannot become a trade
trigger or probability.

## Safety Rules

- Dynamic scan pool affects only watchlist ordering and paper-only sampling.
- Social or narrative data cannot bypass current signal, K-line freshness, liquidity, spread, depth, cross-source validation, capacity, recovery, recent-loss or learning gates.
- `risk_alert_cluster` can increase monitoring priority, but it cannot become a long trigger by itself.
- No private APIs, no live orders, no withdrawals, no margin/futures/perpetuals.
- Reports and experiments must keep `live_orders_enabled=false`.

## V2.50 Enforcement

The scanner must not treat the configured `symbols` array as the actual opportunity universe when market data is available. Every scheduled or manual active crypto paper run should rebuild the effective pool from the current Binance USDT spot universe, market breadth, 1h/4h anchor atmosphere, liquidity, participation, recovery queue and fresh social/key-person sentiment. The static list is only a survival fallback for offline fixtures, failed market fetches, or explicit operator disablement.

Quality scout recovery exceptions are deliberately narrow. `current_signal_quality_scout_probe` may retest a weak interval only when that is the only recovery-plan blocker. Weak symbols, retired entry modes and retired strategy families take priority and must block the candidate before any paper exposure is opened.

## V2.53 Default Dynamic Pool

`validation_progress_runner.py` must treat the dynamic scan pool as the default path. Operators no longer need to remember `--dynamic-scan-pool`; the runner should rebuild the pool from market atmosphere and sentiment unless `--disable-dynamic-scan-pool` is supplied or the run is an offline fixture. The legacy flag remains accepted only for compatibility with older automation prompts.

This makes market mood and sentiment first-class inputs:

- risk-off or fading anchors contract high-beta/meme exposure and raise core/liquid floors.
- risk-on or accelerating anchors widen high-beta, liquid momentum and newly active mover coverage while preserving core anchors.
- selective rotation modestly expands toward volume acceleration, confirmed narratives and recovery-queue visibility.
- fresh positive catalyst clusters reserve social-catalyst watch slots, but stale positive social data must not expand the pool.
- fresh risk-alert clusters contract high-beta/meme/social-catalyst slots and downgrade affected symbols unless independent price/liquidity/current-signal evidence passes.

## V2.57 Mood/Sentiment Adaptive Contract

The scan pool is now a contract, not just a score preference. Every live-market crypto paper run must rebuild the effective scan universe from current market atmosphere and fresh sentiment before K-line strategy scanning:

1. Classify market atmosphere from Binance USDT spot breadth, BTC/ETH 24h direction, 1h/4h BTC/ETH/SOL/BNB anchors, volume, trade count and top mover distribution.
2. Classify sentiment from fresh social/key-person handoff only. If the handoff is missing or older than the configured max age, use `neutral_or_missing_social`.
3. Convert atmosphere and sentiment into both ranking weights and pool shape: effective width, core/high-beta/meme/social-catalyst min/max slots, and dynamic bucket handling for new movers.
4. Preserve open paper symbols, explicit user symbols and recovery-queue symbols for monitoring, but do not let protected symbols imply a fresh opportunity signal.
5. If Binance market breadth is unavailable, explicitly mark `fallback_static`; the static configured list is a degraded survival mode, not a market scan.

Reports and experiments must make the adjustment visible. At minimum they should show `market_regime`, `market_atmosphere`, `short_term_state`, `sentiment_state`, `sentiment_overlay`, configured versus effective width, bucket quotas, selected counts, top dynamic candidates and fallback/degradation reason.

This contract does not loosen paper entry rules. It only decides what gets scanned and ranked. Current signal, K-line freshness, liquidity, spread/depth, cross-source validation, recovery-plan blocks, capacity, recent-loss learning and paper-only safety gates still decide whether a paper position can open.
- risk-on or accelerating anchors widen the pool and increase liquid high-beta/momentum slots.
- selective rotation favors volume acceleration, recovery queue visibility and confirmed narratives.
- fresh positive catalyst clusters may add social-catalyst watch slots, but cannot bypass liquidity, K-line, recovery, current-signal or capacity gates.
- fresh risk-alert clusters contract speculative slots and increase monitoring priority without becoming a long trigger.

## V2.59 Market Mood and Sentiment Hard Contract

The scanner must treat market atmosphere and sentiment as pool-shaping inputs, not a post-scan label. A live-market crypto paper run is considered dynamically valid only when the run builds the pool before K-line strategy scanning and records all of:

- `market_regime`
- `market_atmosphere`
- `short_term_state`
- `sentiment_state`
- `pool_width_policy`
- `pool_shape_policy`
- `selected_symbols`
- `top_dynamic_candidates`

If any of those fields are missing, the report must mark the dynamic pool as degraded and must not present the configured static list as a fresh opportunity scan.

The pool must change shape with the environment:

- Risk-off or fading anchors: narrow the pool, raise liquid core and large-cap floors, cap high-beta/meme/new-mover slots, and keep rebound names as watch or small paper-only candidates after independent gates.
- Risk-on or accelerating anchors: widen the pool, increase liquid high-beta/momentum/new-mover coverage, and keep core anchors for monitoring.
- Selective rotation: modestly widen toward volume acceleration, recovery queue visibility and confirmed narratives while avoiding one-note exposure.
- Fresh positive catalyst cluster: reserve social-catalyst watch slots only when the handoff is fresh and market data/liquidity exist.
- Fresh risk-alert cluster: contract speculative slots, raise core/liquid floors and downgrade affected names unless price, liquidity and current-signal evidence independently pass.
- Stale or missing social data: continue Binance market-breadth scanning, but forbid sentiment-driven width expansion and label sentiment as `neutral_or_missing_social`.

`fallback_static` is allowed only when Binance market breadth is unavailable, the run is an offline fixture, or the operator explicitly disables the dynamic pool for debugging. In all other live-market runs, the static configured symbol list is only a seed/protected fallback and cannot define the effective opportunity universe.

## V2.60 Atmosphere-First Pool Rebuild

The scan pool must move with the market. A runner is not allowed to keep scanning the same effective universe merely because the configured baseline list contains those symbols. Every live-market run must first classify the current atmosphere and sentiment, then rebuild the effective universe from that classification.

Required behavior:

- If the atmosphere is `risk_appetite_accelerating`, widen the pool only into liquid high-beta, momentum and new-mover names, while keeping BTC/ETH/SOL/BNB style anchors monitored.
- If the atmosphere is `risk_appetite_fading`, contract speculative coverage, cut meme/high-beta caps, and require stronger current-signal evidence before any paper entry.
- If the atmosphere is `rebound_attempt`, keep oversold liquid rebound candidates visible, but do not treat the rebound as a chase signal until 4h anchors improve.
- If the atmosphere is `pullback_after_strength`, avoid late momentum chasing; preserve leaders, raise liquidity requirements and prefer wait/retest setups.
- If sentiment is a fresh `positive_catalyst_cluster`, reserve social-catalyst watch slots only when market data and liquidity are valid.
- If sentiment is a fresh `risk_alert_cluster`, contract speculative slots and downgrade affected names unless independent price, liquidity and current-signal evidence pass.
- If sentiment is stale or missing, label it `neutral_or_missing_social` and forbid sentiment-driven width expansion.

Reports must explain `why_pool_changed_from_static_baseline`: for example, "risk-on breadth widened high-beta coverage", "risk-alert cluster contracted meme coverage", "fading 1h/4h anchors raised core/liquid floors", or "social handoff stale, market-breadth-only pool used".

This remains a scan-selection rule, not an entry permission. A dynamically added symbol still has to pass K-line freshness, spread/depth, liquidity, validation recovery, capacity, learning, current-signal and paper-only safety gates.

## V2.55 Market Mood and Sentiment Contract

Every live-market active crypto paper run must treat market atmosphere and fresh sentiment as first-class scan-pool inputs, not optional ranking hints. The effective scan pool is invalid unless the run records:

- market regime: `risk_off_rebound_watch`, `risk_on_momentum`, `selective_high_beta_rotation`, or `mixed_selective`
- short-term anchor state from BTC/ETH/SOL/BNB 1h and 4h movement
- sentiment state from the latest fresh social/key-person handoff, or `neutral_or_missing_social` when stale/missing
- pool width policy showing configured width, effective width, multiplier and reason
- pool shape policy showing bucket min/max slots, selected counts and quota gaps
- selected symbols and top dynamic candidates with bucket, score, 24h move, volume, trades and social long/risk scores

Behavior is target-driven:

- risk-off or fading anchors must narrow speculative symbols and prioritize liquid/core/rebound watches.
- risk-on or accelerating anchors must widen the universe and reserve more high-beta/momentum slots.
- selective rotation must favor volume acceleration, recovery-queue symbols and confirmed narratives while keeping core anchors.
- fresh positive catalyst clusters may expand social-catalyst watch slots, but only after market data and liquidity exist.
- fresh risk-alert clusters must contract high-beta/meme slots and raise core/liquid floors.
- stale or missing social data must not expand the pool; it falls back to market breadth and liquidity only.

This contract applies to runner, fast, daily and Sunday paper loops. It remains paper-only and cannot override current-signal, K-line freshness, spread/depth, validation recovery, capacity, learning or safety gates.
