# Sunday Crypto Realistic Paper Loop

## Purpose

This loop is the closest paper simulation of a future crypto high-frequency trading chain. It is used to test whether hourly active scanning can produce realizable paper ROI before any real-money API route is considered.

## Schedule

- Timezone: `Asia/Shanghai`
- Window: Sunday `10:00-18:00`
- Frequency: hourly
- Capital: existing `$500` paper portfolio ledger
- Scope: crypto spot paper only

## Hard Boundaries

- No live orders.
- No private trading API calls.
- No withdrawals.
- No margin, futures, perpetuals or leverage.
- No real fund transfer.
- No API keys in reports, logs, experiments or skill files.

## Standard Hourly Flow

1. Acquire run lock.
2. Load config, paper ledger and strategy state.
3. Fetch Binance spot book, depth and 24h stats; fetch CoinGecko price validation when available.
4. Fetch short-term information pressure: CoinGecko trending, Reddit 24h keyword mentions, futures funding/open interest and 24h volume/move.
5. Review every open paper position with realistic sell assumptions.
   - Hard exits: stop, take profit, max holding expiry.
   - Dynamic exits: trailing profit protection after meaningful unrealized gain, and information-decay exit for stale exploratory probes.
6. Rebuild the scan universe dynamically before the deep scan.
   - The configured symbol list is the baseline/fallback, not the final scan pool.
   - Use live Binance USDT spot 24h breadth, liquidity, 24h movement, trade participation, asset bucket bias, recovery queue symbols and latest social/key-person sentiment.
   - Preserve open paper symbols first so exits and current exposure remain monitored.
   - Preserve explicit user-provided symbols and recovery-queue symbols.
   - Shift the pool by market mood: defensive/risk-off prefers core liquidity and liquid rebound candidates; broad risk-on widens to high-beta momentum and large-cap leaders; selective rotation prioritizes top volume acceleration plus confirmed social narratives.
   - Adapt the effective scan width as well as the ordering: risk-on and selective rotation may expand beyond the configured baseline; risk-off and social risk-alert clusters contract toward cleaner liquid names. Reports must show the configured limit, effective limit, width multiplier and reason.
   - Risk-alert social clusters should penalize long priority and may make candidates `watch` only; they must not become standalone long paper triggers.
   - Follow `DYNAMIC_SCAN_POOL_POLICY.md` for the market state machine, sentiment overlay, width rules and bucket quota report.
7. Prefilter the dynamic deep-scan universe by information pressure and open positions.
8. Refresh Binance K-line cache for the filtered universe.
9. Scan current long-only spot signals.
10. Evolve strategy version inside paper only.
11. Open at most one new paper position when gates pass.
12. Record hourly report, handoff, experiment and paper-trade file.
13. Record equity snapshot and session summary.

## Realistic Execution Model

Paper entry and exit must use:

- bid/ask spread
- commission bps
- base slippage bps
- dynamic impact from 1% order-book depth
- quote volume and depth gates

The paper ledger must record gross PnL, net PnL, entry/exit commission, slippage, spread, max unrealized PnL and min unrealized PnL where available.

Every simulated entry and exit must also create a `paper_order` record that mimics an exchange API market order lifecycle:

- `paper_order_id`
- `side`
- `type=MARKET`
- `status=FILLED`
- average fill price
- executed quantity
- commission, spread, slippage and depth
- order reason
- `live_orders_enabled=false`

## Strategy Evolution

The loop may auto-switch strategy versions inside paper when a better current-signal candidate appears or when the prior strategy stops passing gates.

Each revision must record:

- `run_id`
- previous strategy version
- new strategy version
- reason
- selected symbol and interval
- train/OOS summary
- current signal count

Strategy evolution is paper-only. It must not create a live-trading candidate by itself.

## Opening Rules

- Max open paper positions: config `paper_portfolio.max_open_positions`, default `3`
- Do not duplicate an already-open symbol.
- New opens require `target_research_pass_current_signal` or `paper_forward_candidate_current_signal`.
- If no strict candidate exists, an information-driven exploratory probe may open from `paper_only` or `research_watch`.
- Exploratory probes require strong information pressure, enough OOS samples, acceptable drawdown and full liquidity/spread/depth validation.
- Exploratory probes use `$25` by default so the system can learn without pretending that weaker evidence deserves full size.
- The deep scan should prefilter to the strongest information/volatility candidates so hourly automation does not waste the window on low-signal assets.
- New opens require liquidity, spread, depth and cross-source validation gates.
- Default notional is `$75`; high-quality robust candidates may use up to `$150`.
- If data is degraded or disputed, produce report-only output.

## Session Summary

At every hourly run, include a session summary. The 18:00 run acts as the daily summary for the Sunday session and must include ROI, win rate, realized PnL, max drawdown, best/worst trade and open positions.
