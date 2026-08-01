# Risk-Adjusted Path Quality Policy

## Purpose

Sharpe, Sortino, information ratio and maximum drawdown measure the quality of
an observed return path. They do not measure fair value, confirm a catalyst or
produce a forecast probability. This factor is a bounded secondary ranking
input and remains `research_only_paper_only` until promotion evidence passes.

## Calculation Contract

- Use closed bars only.
- Use adjusted close for US equities and spot close for crypto.
- Keep the action price on a separate evidence path. A US-equity entry card
  uses the fresh screener `regularMarketPrice` (with its own source and
  timestamp), while Sharpe and every path statistic continue to use only
  closed adjusted bars. Never substitute the last closed bar for a current
  order price.
- Calculate daily 20/60/126-return windows. For crypto also calculate 42/126
  closed 4-hour-return windows for short-term context.
- Annualize US daily returns with `sqrt(252)`, crypto daily with `sqrt(365)`
  and crypto 4-hour returns with `sqrt(2190)`.
- Use the current 3-month US Treasury rate from the shared
  `EvidenceSnapshotV2`. If unavailable, use zero only with
  `degraded_risk_free_fallback_zero`.
- US benchmark defaults to SPY, with QQQ or an industry ETF when the scanner
  has a reproducible classification. Crypto benchmark is BTC.
- Record sample count, window start/end, benchmark, risk-free evidence ID,
  data quality and calculation version.

Required output:

```text
risk_adjusted_path:
  lane
  calculation_version
  benchmark
  risk_free_rate
  windows
  sharpe
  sortino
  information_ratio
  max_drawdown
  quality_score
  ranking_adjustment_points
  persistence_label
  flags
  data_quality
  evidence_ids
  promotion_status
  live_gate_effect
```

## Dual-Lane Ranking

### Trend continuation

Cross-sectional score weights:

- 20-day Sharpe: 25%
- 60-day Sharpe: 25%
- 60-day relative strength versus the selected benchmark: 20%
- 60-day Sortino: 15%
- 60-day maximum drawdown: 15%

The score may adjust the existing research ranking by at most `±7.5` points.
If 20-day Sharpe exceeds 3 but 60-day Sharpe is below 1, label
`short_spike_not_persistent`, cap path quality at 60 and do not treat the high
Sharpe as entry permission. Persistent strength requires 20-day Sharpe above
3, 60-day Sharpe at least 1.5 and 60-day information ratio at least 1.

### Value repair

Negative recent Sharpe does not delete a candidate. First require verified
value discount, confirmed catalyst, incomplete price transmission, liquidity
and reward/risk. After that, rank improvement in 20-day versus 60-day Sharpe
and information ratio, 20-day Sortino and drawdown compression. The adjustment
is capped at `±4` points. Before the value/catalyst gate passes, adjustment is
zero and the metrics are display-only.

## Long-Term DCA

Do not use Sharpe to score adoption, value capture, supply or long-term fair
value. It may only change a previously qualified DCA batch to normal, smaller
or delayed. Negative Sharpe is not “buy more”; high Sharpe is not better
long-term value.

## Failure and Promotion

Open bars, insufficient samples, zero volatility, missing benchmark, stale
data, corporate-action problems or missing risk-free evidence must be explicit.
Never map a path score to target probability or fair value.

If a fresh action price has already crossed a target or absorbed a catalyst,
invalidate the old entry card and rescan; a strong closed-bar Sharpe cannot
restore an expired price gap.

Before the factor can affect the real-money gate, require:

- three non-overlapping independent market windows;
- at least 20 resolved paper trades;
- win rate at least 55%, net return at least 5%, max drawdown no worse than
  -15%;
- paired ranking ablation that improves probability-weighted outcome without
  worsening maximum drawdown by more than two percentage points;
- Manual review and human confirmation.

Active remains unable to place live orders.
