# Long-Term Price Scenario Policy

## Purpose

This policy turns long-term DCA discussion into explicit 5-year and 10-year price scenarios. It prevents reports from only saying "buy more SOL" or "avoid ETH" without showing whether the asset's plausible future price range can support the user's 5-year aggressive 10x target or 10-year fallback 10x target.

The scenario panel is not a promise, target guarantee, or automatic trade signal. It is a decision aid for sizing monthly DCA and comparing opportunity cost.

## Required Panel

Every formal long-term DCA report must include a `Long-Term Price Scenario Panel` when the report recommends, avoids, or meaningfully discusses a crypto asset.

The panel must include:

| Field | Meaning |
|---|---|
| current_price | Latest verified market price from API sources, not screenshot price. |
| current_market_cap | Current market cap when available. |
| current_fdv | Fully diluted valuation when available. |
| current_or_expected_supply_basis | Whether the estimate uses circulating supply, total supply, FDV, or a conservative future supply assumption. |
| 5y_survival_price_range | Weak case if the thesis survives but does not compound strongly. |
| 5y_base_price_range | Reasonable case if adoption and market cycle improve. |
| 5y_bull_price_range | Strong case if the thesis works well. |
| 10y_base_price_range | Longer compounding case, adjusted for future supply and maturity. |
| 10y_bull_price_range | Very strong but still explainable adoption case. |
| staking_adjusted_return_note | How APY, lock, liquidity, inflation and compounding change the required price multiple. |
| 10x_goal_fit | `strong_engine / satellite / tail_convexity / quality_hold / drag_for_new_dca`. |
| dca_implication | `increase / small_add / hold_only / watch_only / avoid_new_dca`. |
| confidence_band | `low / medium / high`, describing model reliability, not profit certainty. |
| downgrade_reasons | Missing supply, unlock, liquidity, chain usage, revenue, or cross-source price evidence. |

## Scenario Method

The panel must use ranges rather than single prices. The ranges should be derived from:

- Current price, market cap, FDV and supply.
- Plausible future market cap scenarios for the asset category.
- Network adoption, TVL, fees/revenue, users, developer/ecosystem evidence and project roadmap.
- Liquidity depth, spread, volume and venue availability.
- Token utility, value capture, emissions, unlocks and inflation.
- Staking APY and whether rewards are actually liquid or locked.
- Current portfolio concentration and role in the 10x path.

If market cap or supply data is missing, the panel must mark the asset `scenario_degraded` and limit the recommendation to `watch_only`, `small_add`, or `conditional_action`.

## Goal Fit Labels

| Label | Meaning |
|---|---|
| `strong_engine` | Can plausibly carry a meaningful part of the 5-10 year 10x path if the thesis works. |
| `satellite` | Can improve upside but should not dominate the portfolio. |
| `tail_convexity` | Very high upside, high failure/liquidity/supply risk; small sizing only. |
| `quality_hold` | Worth holding, but new DCA is less capital-efficient for the user's aggressive target. |
| `drag_for_new_dca` | Existing holding may be fine, but additional DCA likely slows the 10x path. |

## Dynamic Cross-Asset Baseline

There are no asset-name defaults. Current holdings, listed companies, regular
ETFs, BTC/ETH/SOL and other qualified crypto assets are baseline candidates
only. Every run reranks them with current fundamentals or network evidence,
value capture, balance-sheet or supply dilution, valuation, five- and ten-year
scenarios, survival/drawdown, correlation and next-dollar marginal
contribution. Cash is a valid winner in risk-off conditions. The final report
shows exactly one next-dollar winner and at most two runner-up rejection notes.

## Existing-Holding Concentration Rule

Any current holding can remain a quality hold while being a poor next-dollar
destination. Reports must distinguish hold quality from marginal allocation
using its current concentration, scenario multiple, liquidity, correlation and
the strongest cross-asset alternative. This rule applies symmetrically to
stocks, ETFs and crypto; it cannot encode an ETH-, SOL- or other symbol-specific
default.

## Output Rules

- Do not present scenario prices as certainty.
- Do not use a 10-year bull case to justify chasing a 24-hour rally.
- Do not compare assets only by APY; APY must be combined with required price multiple.
- Do not recommend adding a new asset unless it improves the selected 5-10 year
  goal better than the best existing holding, ETF, stock, crypto or cash alternative.
- If a user asks for a single number, still provide survival/base/bull ranges and explain which range is most decision-relevant.
