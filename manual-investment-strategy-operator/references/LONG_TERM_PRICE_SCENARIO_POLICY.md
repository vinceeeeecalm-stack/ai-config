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

## Current Asset Defaults

These are default labels only; each report must refresh data before using them.

| Asset | Default role | Default implication |
|---|---|---|
| ETH/lcETH | `quality_hold` or `drag_for_new_dca` when overweight | Hold existing stake; do not add while ETH/lcETH concentration remains high. |
| SOL | `strong_engine` | Primary growth DCA candidate when data is verified and price is not overheated. |
| SUI | `satellite` with possible `strong_engine` upgrade | Growth satellite if liquidity, supply, TVL and ecosystem data remain verified. |
| ADA | `satellite` | Deep-value staking satellite; can increase when discount, APY and ecosystem evidence support it. |
| NIGHT | `tail_convexity` | Small tail only until DUST demand, float/unlock and liquidity evidence improve. |
| LINK | `satellite` or `quality_hold` | Infrastructure satellite; steadier than tail assets but usually lower 10x convexity. |
| TAO / RENDER | `tail_convexity` | AI-themed high-upside small positions only unless evidence improves. |
| BTC | `quality_hold` / liquidity anchor | Not default new DCA for this aggressive target unless extreme fear or liquidity-anchor need appears. |

## ETH-Specific Rule

ETH can remain a high-quality core asset while still being a poor use of new DCA capital for this specific aggressive goal.

Reports must distinguish:

- `hold_existing_eth`: long-term thesis intact; staking/settlement ecosystem remains valuable.
- `avoid_new_eth_dca`: ETH/lcETH portfolio concentration is too high or expected price multiple is too low versus SOL/SUI/tail alternatives.
- `resume_eth_dca`: only if ETH/lcETH concentration falls back into target range, price becomes extremely dislocated, staking/liquidity terms are verified, and the scenario panel shows improved 10x contribution.

## Output Rules

- Do not present scenario prices as certainty.
- Do not use a 10-year bull case to justify chasing a 24-hour rally.
- Do not compare assets only by APY; APY must be combined with required price multiple.
- Do not recommend adding a new asset unless it improves the current portfolio's 5-10 year goal fit better than adding to existing SOL/ADA/NIGHT/ETH roles.
- If a user asks for a single number, still provide survival/base/bull ranges and explain which range is most decision-relevant.

