# Paper Position Exit Monitor

## Purpose

This is the fast review loop for existing crypto paper positions. It exists because entry scans can run every few hours, but exits need faster checks to simulate a future API trading route responsibly.

The monitor is paper-only. It never opens new positions, never places real orders, never calls private trading APIs, and never uses margin, futures, perpetuals, or withdrawals.

## Cadence

- Timezone: `Asia/Shanghai`
- Suggested automation: hourly
- Scope: existing open crypto spot paper positions only
- Ledger: existing `$500` paper portfolio ledger

## Standard Flow

1. Acquire the shared paper ledger lock.
2. Load config and paper ledger.
3. Backfill legacy paper entry orders if needed.
4. Fetch Binance spot book/depth/24h stats for open symbols only.
5. Fetch information pressure for open symbols when available.
   - Futures/funding/OI context is optional for this exit-only loop and should default to skipped/degraded if it is slow or unavailable; spot bid/ask/depth and existing paper rules are the exit decision baseline.
6. Review hard stop, hard take profit, max holding expiry, trailing profit protection, and information-decay exit.
7. If an exit triggers, write a simulated `SELL MARKET` paper order with fill price, quantity, commission, spread, slippage, depth, and reason.
8. If an exit does not trigger, persist current mark price, unrealized PnL, profit-protection armed state, highest unrealized PnL, trailing floor, and latest information pressure for every open position.
9. Update ledger cash, open value, equity, monthly-double progress, report, handoff, experiment, and paper-trade record.
10. If an external subagent JSON is supplied through `--external-agent-outputs-json`, validate and embed the `research_panel`; otherwise write `research_panel_missing=true` with a clear reason.

## Output

```text
reports/YYYY-MM-DD-paper-exit-monitor-HHMM.md
handoffs/YYYY-MM-DD-paper-exit-monitor-HHMM-handoff.json
experiments/YYYYMMDD-HHMM-paper-position-exit-monitor.json
paper_trades/YYYY-MM-DD-paper-exit-monitor-HHMM.json
paper_trades/paper_portfolio_ledger.json
```

## Safety

- `live_orders_enabled=false` in every output.
- No private API endpoints or keys.
- No new paper entries.
- If market data is missing or disputed, mark the position degraded and do not simulate a sell unless a valid execution quote exists.
- Optional public data fetches must have hard timeouts and degrade the report instead of blocking scheduled exit review.
- Even with a valid external `research_panel`, this monitor caps `max_allowed_action` at paper-only simulated exit review; it never authorizes real trading.
