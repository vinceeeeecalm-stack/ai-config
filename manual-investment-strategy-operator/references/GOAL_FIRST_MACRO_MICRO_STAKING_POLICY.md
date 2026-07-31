# Goal First Macro Micro Staking Policy

## Purpose

This policy makes the long-term goal measurable before any DCA recommendation. Reports must prove that a suggested allocation improves the probability of reaching 5-year or 10-year 10x, or explain why the action is only defensive, conditional, or watch-only.

## Goal Gap Panel

Every long-term or DCA report must calculate:

- Required CAGR for 10x in 5 years: approximately `58%`.
- Required CAGR for 10x in 10 years: approximately `26%`.
- Current portfolio value, monthly DCA assumption, and scenario base used for the path.
- Each major holding's contribution label:
  - `accelerator`: improves the 10x path through growth, convexity, or compounding.
  - `neutral`: useful for survival or liquidity but not enough for the target.
  - `drag`: too large, too slow, or low expected convexity versus target.
  - `tail_convexity`: high upside but high failure or liquidity risk.

The panel must state whether the current allocation is target-aligned, too defensive, too concentrated, or too speculative.

Automation support:

- Use `scripts/asset_goal_contribution.py` to generate the report-ready asset contribution and staking panel from the current portfolio snapshot and ledger.
- `scripts/build_daily_report_context.py` can auto-generate this panel, or accept a prebuilt JSON through `--asset-goal-contribution-json`.
- The panel is advisory and cannot authorize `execute_now` by itself; degraded staking/unlock/liquidity data must flow into Missing Data / Downgrade Panel.

## Macro Regime Panel

Macro data changes DCA cadence, not single-asset conviction by itself.

Required checks when data is available:

| Input | Preferred sources | Decision impact |
|---|---|---|
| Fed / rate expectations | CME FedWatch, FRED, Treasury | `accelerate / normal / split_more / wait_for_pullback` |
| 10Y / 2Y yields and curve | U.S. Treasury, FRED | risk appetite and duration pressure |
| DXY / liquidity proxy | Yahoo/FRED or market data | risk-on/risk-off filter |
| CPI / PCE / employment | BLS, BEA, FRED | inflation and policy pressure |
| Nasdaq / semis / VIX | Yahoo/public market data | cross-asset risk appetite |
| BTC/ETH ETF or digital asset fund flows | CoinShares or official ETF/fund sources | category rotation, not standalone buy signal |

If macro data is stale or missing, the report must downgrade immediate size rather than ignore the gap.

## Asset Micro Thesis Matrix

Only decision-relevant assets should be scored. Do not force a fixed token table.

Required dimensions:

| Dimension | Meaning |
|---|---|
| price_location | ATH drawdown, 30/90/200 day position, current rebound or breakdown |
| network_usage | active addresses, transactions, TVL, fees, revenue, or ecosystem usage |
| developer_ecosystem | roadmap, apps, developer growth, integrations, major releases |
| liquidity_depth | quote volume, spread, order book depth, exchange availability |
| supply_unlock | inflation, emissions, unlocks, redemptions, airdrop/thawing pressure |
| staking_compounding | APY, lock/unlock, provider fees, slash, compounding practicality |
| fund_flow_narrative | ETP/ETF/fund flow, category rotation, institutional or retail attention |
| risk_events | security, outages, regulatory, governance, delisting or liquidity events |

Each asset must receive a concise action implication: `increase_weight / hold_weight / reduce_new_dca / reduce_or_trim / watch_only`.

## Staking Compounding Model

For every stakeable relevant holding, report:

- APY used and source/provider.
- Whether rewards are delayed, locked, liquid, manually claimable, or automatically compounding.
- 5-year compounding multiplier: `(1 + APY)^5`.
- 10-year compounding multiplier: `(1 + APY)^10`.
- Price multiple still required to reach 10x after staking: `10 / compounding_multiplier`.
- Whether staking yield compensates for lower growth, liquidity, unlock, or provider risk.

If APY or unlock data is missing, the asset cannot be a strong main DCA recommendation. It may remain `conditional_action`, `smaller_size`, or `hold_only`.

## Long-Horizon Low-Value / Staking Timing Rule

The report must not treat “wait for a better price” as the default answer when the asset is in a long-term low-value zone. For stakeable assets, waiting has a measurable cost because capital is not earning staking rewards and is not exposed to long-term mean reversion.

Every DCA report must classify the timing decision:

| Timing decision | When to use |
|---|---|
| `accelerated_dca` | Long-term low-value zone, thesis intact, staking verified, current allocation under target, and expected pullback advantage is smaller than staking plus missed-upside cost |
| `near_price_entry` | Price is fair or attractive, but short-term risk suggests only the first DCA tranche now |
| `limit_order_wait` | Better pullback has high enough probability and clear trigger; thesis remains intact |
| `hold_stablecoin_until_trigger` | Data is disputed/stale, thesis has new risk, or liquidity/supply risk blocks new capital |

For SOL/ADA/ETH/lcETH and other stakeable assets, include:

- Estimated staking income lost by waiting.
- Time-cost risk: whether waiting for a small extra discount risks missing a larger long-term recovery or several weeks/months of compounding.
- Whether the reward delay, lock period, fees, or provider risk reduces the benefit of early staking.
- Whether early staking lowers the price multiple needed to reach the 10x path.
- Whether the current price is low enough that time-in-market is more valuable than trying to capture a small extra discount.

Plain note: `time-in-market` means having capital invested and compounding for longer; it is different from trying to perfectly time the lowest price.

Every report must also state whether idle stablecoin is helping or hurting the long-term objective. Stablecoin can be useful when a better entry is likely and clearly defined; otherwise, for verified low-value stakeable assets, excessive waiting can lower the chance of reaching the 5-year/10-year 10x path by delaying compounding.

For long-horizon DCA, the burden of proof is on waiting. If an underweight stakeable asset is in a verified long-term low-value zone and the thesis remains intact, the report should prefer at least a first near-price tranche unless the expected pullback is large enough, likely enough, and time-bounded enough to compensate for missed staking and missed recovery exposure.

Plain note: `time-cost risk` means the cost of staying in cash while a long-term asset may recover or compound. It is not only the dollar value of a few days of staking rewards; it also includes the risk that the market reprices before the limit order fills.

For aggressive 5-year/10-year targets, the report may recommend temporarily increasing the current month's DCA or front-loading a small portion of future DCA when the long-term low-value evidence is strong. This is not leverage and not automatic buying: it is a human-confirmed sizing adjustment that must preserve a stablecoin opportunity bucket and must show why early staking/time-in-market improves the goal path more than waiting.

## Long-Term Price Scenario Panel

Every long-term or DCA report must translate the thesis into 5-year and 10-year price ranges for decision-relevant assets. This is a scenario model, not a promise.

Required output:

- Current price, market cap, FDV, and supply basis.
- 5-year survival/base/bull price ranges.
- 10-year base/bull price ranges.
- Staking-adjusted return note and the remaining price multiple required after compounding.
- Goal-fit label: `strong_engine / satellite / tail_convexity / quality_hold / drag_for_new_dca`.
- DCA implication: `increase / maintain / small_dca_only / hold_only / no_new_dca / trim_review`.
- Confidence band and downgrade reasons.

ETH/lcETH can be a `quality_hold` and still be a `drag_for_new_dca` when the portfolio is already concentrated in ETH. In that case, reports should recommend holding and monitoring staking/liquidity terms, while directing new DCA toward assets with better marginal contribution to the 5-year/10-year 10x path.

## ADA Deep Value Rule

ADA is not limited to a low-weight satellite when all of the following are true:

- Price is in a deep historical drawdown or low valuation zone.
- Data quality is `verified` or only mildly `degraded`.
- Staking rewards are available and operationally realistic.
- Current ADA weight is below the target range.
- Micro thesis is not deteriorating in ecosystem, liquidity, or supply pressure.

When these conditions hold, ADA can target `15%-22%` of the crypto rail as a long-term staking satellite. It should remain below SOL as a growth engine if SOL has stronger ecosystem, liquidity, developer, or fund-flow evidence.

## Missing Data / Downgrade Panel

Every report must list missing or degraded data and the action impact:

| Impact | Meaning |
|---|---|
| `no impact` | Data gap does not affect the current decision |
| `smaller size` | Recommendation allowed only with reduced amount |
| `conditional only` | Action requires a trigger or next data confirmation |
| `block` | Asset-specific recommendation is blocked |

Missing macro, micro, staking, unlock, or liquidity data must be shown explicitly instead of hidden inside a generic data quality note.
