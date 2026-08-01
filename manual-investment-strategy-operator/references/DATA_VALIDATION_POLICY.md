# Data Validation Policy

## Purpose

This policy defines the data quality gate for manual investment reports, especially long-term DCA decisions. Reports must prefer complete, current, multi-source data. When data is incomplete or conflicting, recommendations must be downgraded instead of presented as certain.

## Required DCA Data

Every manual dispatch must first run a fresh market and sentiment refresh. This means a new report cannot rely only on the previous conclusion, cached report text, or a screenshot price. For current buy/sell/DCA/entry/exit recommendations, cached artifacts are allowed only when they were generated inside the same user-triggered dispatch. Cross-run cache, old report text, and screenshots cannot support a current action.

Before issuing a crypto DCA recommendation, gather as much of the following as available:

| Category | Required fields |
|---|---|
| Price and trend | Real-time price, 24h, 7d, 30d, 90d when available |
| Liquidity | 24h quote volume, bid/ask spread, order book depth, exchange availability |
| Derivatives risk | Funding, open interest, basis or premium when available |
| Market regime | Fear & Greed, BTC trend, broad crypto drawdown/rebound |
| Fund flows | Digital asset fund flows, ETF/fund flows, category-level rotation |
| Macro regime | Fed/rate expectations, 10Y/2Y yields, DXY, CPI/PCE, broad risk appetite |
| Project and ecosystem | Official announcements, roadmap, network usage, developer/ecosystem signals |
| Staking/yield | APY, rewards, lock/unlock, slash, provider fees, liquidity constraints |
| Supply pressure | Unlocks, redemptions, airdrops, inflation, circulating supply changes |
| Portfolio context | Current weights, cash rail, stablecoin amount, staking locked amount |

## Required Manual Dispatch Data

For any report that discusses current actions, the data gate must attempt to refresh these domains:

| Domain | Required fields |
|---|---|
| Portfolio state | Current quantities, cash rail, stablecoin, open orders, staking/lock status, cost-basis coverage |
| Crypto market | Price, 24h/7d/30d trend, volume, spread, depth, funding, OI, exchange availability |
| Crypto thesis | Official project updates, key-person intel, tokenomics/unlock, DeFi/chain usage, fund flows |
| US equity market | SPY/QQQ/IWM/SOXX/SMH, VIX, DXY, yields, sector rotation, current tactical holdings |
| US equity candidates | Market movers, unusual volume, news catalysts, earnings surprises, relative strength, active handoff |
| Sentiment | Fear & Greed, flows, news tone, social/key-person confirmation, price-volume reaction |
| Goal mapping | 5y/10y 10x gap, monthly DCA impact, tactical-sleeve monthly ROI 100% attack-goal progress |

## Source Priority

Crypto prices should use at least two sources, and preferably three:

1. Binance spot data when a liquid spot pair exists.
2. CoinMarketCap or CoinGecko market data.
3. Another exchange, DeFiLlama, project source, or official explorer where relevant.

Project and thesis data should prefer:

1. Official project docs, blog, roadmap, tokenomics, explorer, or foundation updates.
2. Primary data providers such as DeFiLlama, exchange data, or fund-flow reports.
3. Reputable news sources for market interpretation.
4. Social data only as context; it cannot support `execute_now` or strong DCA by itself.
5. Crypto key-person social intel only after identity verification and source confirmation; unverified accounts, reposts, screenshots and single-source rumors are `watch_only`.

Macro data should prefer:

1. FRED, BLS, BEA, U.S. Treasury, CME FedWatch, or equivalent primary/official sources.
2. Public market data for DXY, VIX, QQQ, SOXX, BTC dominance, and broad risk appetite.
3. CoinShares or official ETF/fund sources for digital asset flow context.

Micro thesis data should prefer:

1. DeFiLlama or official explorers for TVL, chain activity, fees, and ecosystem usage.
2. Official project updates for roadmap, upgrades, tokenomics, unlocks, and risk events.
3. Binance/CoinMarketCap/CoinGecko plus order book data for liquidity and price location.

## Freshness Rules

| Data type | Freshness target | If stale |
|---|---:|---|
| Spot price, spread, volume | <= 15 minutes for intraday reports | Mark `stale`, downgrade immediate action |
| 24h stats, funding, OI | <= 60 minutes | Mark `degraded`, require confirmation |
| 7d/30d trend | <= 24 hours | Mark `degraded` |
| Fund flows and macro/news | Latest available release | Label release date and avoid overclaiming |
| Official roadmap/tokenomics | Latest available version | Use, but label source date if known |
| Staking APY, rewards, lock/unlock | Latest provider or protocol data | Downgrade size if unavailable |
| Key-person social intel | <= 72 hours for tactical context | Mark `stale`, max action `watch` |

## Data Quality States

- `verified`: Key fields are current and consistent across required sources.
- `degraded`: Some fields are missing, stale, or from a proxy, but the conclusion can still be cautiously used.
- `disputed`: Sources conflict beyond tolerance; strong recommendations are blocked.
- `stale`: Data is too old for the claimed timing.
- `missing`: Required data is unavailable.

## Conflict Handling

Price conflicts must be handled conservatively:

- Major crypto pairs: if primary price sources differ by more than 1%, mark `disputed`.
- Low-liquidity or early tokens: if sources differ by more than 3%, mark `disputed`.
- If Binance pair is missing for a token, mark `pair_missing` and use alternate sources only for `watch` or long-term thesis unless liquidity is verified elsewhere.
- If project news is single-source or unconfirmed, it may support `watch`, not a main DCA action.
- If a key-person social item is unverified, single-source, a repost, a screenshot, or a rumor, it may support `watch` only.
- If an official social item reports security, tokenomics, delisting or regulatory risk, pause affected new DCA until the item is verified against price, liquidity and official follow-up sources.
- If macro data is missing, DCA cadence must be downgraded to `split_more` or `conditional_action`, not ignored.
- If staking APY, unlock, fee, or liquidity data is missing, stakeable assets cannot be strong main recommendations.

## Strict Current Action Rules

For any recommendation that tells the user to buy, sell, DCA, set a limit order, reconnect a sold position, or choose an entry/exit price:

- Core market data must be newly fetched in the current run.
- Core market data means price, 24h stats, 7d/30d trend, volume, spread/depth, exchange pair availability, and source timestamp.
- If core market data is `degraded`, `disputed`, `stale`, or `missing`, the report must output `no_current_action_due_to_missing_latest_data`.
- Supplemental data gaps can be disclosed without blocking the whole report, but they cannot be replaced by old data and cannot be used as evidence for a current action.

## Recommendation Downgrade Rules

- `verified`: May output explicit DCA plan if risk model allows.
- `degraded`: For current buy/sell/DCA timing, block the action and request/perform a fresh retry. For non-timing long-term commentary, may output a clearly labeled watch-only note.
- `disputed`: Do not issue strong buy/sell; output `watch` or `hold_USDT_until_verified`.
- `stale`: Do not issue immediate action; request fresh data in the next run.
- `missing`: Block recommendation for that asset unless it is a pure hold-only note.

## Report Requirements

Every DCA report must include a concise data quality summary:

- A `Fresh Market Intelligence Panel` showing which markets, sources and sentiment inputs were refreshed during this manual dispatch.
- Sources used and timestamps.
- Which fields are verified/degraded/disputed/stale/missing.
- Any fallback source used.
- Any recommendation downgraded because of data quality.
- A `Missing Data / Downgrade Panel` for macro, micro, staking, unlock, chain/ecosystem, and liquidity gaps.
- A `Portfolio Evidence Gap Panel` for cost-basis, lcETH redemption, broker cash/buying power, crypto cash floor, open orders and import-template gaps.
- For each missing item, the impact must be one of `no impact / smaller size / conditional only / block`.
- If fresh refresh fails, set `market_intelligence_degraded=true` and block any new `execute_now`.

The report should compare only assets that matter to the current recommendation, holdings, watch/trim decision, or risk issue. It must not force a fixed token comparison table when those assets are not decision-relevant.

## Source To Decision Mapping

| Data source type | Changes |
|---|---|
| Macro regime | DCA pace only: `accelerate / normal / split_more / wait_for_pullback` |
| Fund flows | Category weight tilt, never a standalone strong buy |
| DeFiLlama/explorer/ecosystem | Long-term thesis score and micro matrix |
| Staking provider/protocol | Staking compounding model, size, and liquidity treatment |
| Order book/spread/volume | Immediate execution readiness and size |
| Official roadmap/tokenomics | Thesis confidence and supply/unlock risk |
| Crypto key-person social intel | Watch priority, risk alerts and DCA pace only; never standalone real-money execution |
