# US Open Dynamic Scanner Policy

## Purpose

This policy covers the US equity open-window scanner. It discovers short-term candidates during the US market open and hands them to `manual-investment-strategy-operator`. It never places real orders.

## Cadence

- Default schedule: weekdays, `22:00-24:00 Asia/Shanghai`.
- The scan may run once or multiple times inside the window depending on the automation runner.
- If the computer, network, or data source is unavailable, output a degraded or missed-scan note on the next manual report.

## Candidate Sources

Candidates must come from dynamic data, not a fixed symbol list:

- market movers
- most active symbols
- unusual volume
- relative strength against QQQ/SOXX and the current tactical position when available
- confirmed news catalysts
- sector rotation
- active monitor handoff context

Low price, high volatility, high volume, and strong catalysts are preferences, not hard filters.

## Handoff Requirements

Each Top 1-3 candidate must include:

- `candidate_symbol`
- `entry_zone`
- `target_price`
- `target_time_window`
- `stop_loss`
- `latest_exit_date`
- `forecast_probability_pct`
- `execution_readiness_score`
- `why_better_than_current_tactical_position`
- `candidate_cash_relay_priority`
- `data_quality_status`
- `monitor_recommendation`
- `risk_adjusted_path` with base-versus-adjusted ranking audit

The scanner may output `watch`, `paper_only`, or `small_probe_review`; it must not output live order instructions.

## Safety Rules

- No options, 0DTE, short selling, margin, futures, inverse ETFs, or live orders.
- Unverified news can only produce `watch`.
- If the candidate cannot clearly beat the current tactical position, keep it as watch.
- If probability or readiness is missing, do not call it an 80% trade.
- Sharpe is a bounded secondary path-quality factor. `Sharpe > 3` is not an
  entry gate and cannot override catalyst, value, downside or Research
  Committee requirements.
