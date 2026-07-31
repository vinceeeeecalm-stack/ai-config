# Risk-Adjusted Path Quality Decision Policy

## Decision Role

Use Active's `risk_adjusted_path` as a secondary description of return-path
quality. Do not let Sharpe replace fundamental value, confirmed catalyst,
historical conditioning, downside/capital risk, portfolio fit or cash
authority.

The factor begins as `research_only_paper_only`:

- it may reorder research and paper candidates;
- it cannot convert a candidate to `enter_now`;
- it cannot raise forecast probability or execution readiness;
- it cannot change `execution_decision`;
- zero confirmed cash still requires `no_deploy_cash` and amount zero.

## Manual Arbitration

For `trend_continuation`, accept Active's bounded `±7.5` research adjustment
only after data freshness, liquidity and catalyst checks. Treat
`20d Sharpe > 3` with `60d Sharpe < 1` as spike/chase risk, not a positive
gate.

For `value_repair`, require value discount, confirmed catalyst, incomplete
price transmission and reward/risk before accepting any bounded `±4`
adjustment. A negative recent Sharpe cannot reject an otherwise valid repair
setup by itself.

For `longterm_dca`, use path quality only to choose normal, smaller or delayed
tranche timing. Long-term quality and 5/10-year goal contribution remain based
on adoption, value capture, supply, liquidity, staking and portfolio
concentration.

## Console Contract

The main report must show:

- lane and 20/60-day Sharpe;
- 60-day information ratio and maximum drawdown;
- path-quality score, persistence label and ranking adjustment;
- base rank versus adjusted rank;
- data-quality and risk-free-rate provenance;
- explicit `live_gate_effect=none_until_promotion`.

The current action price and the closed-bar path price are separate evidence.
Manual must invalidate a candidate when a fresh quote has already crossed the
old target or fully transmitted the catalyst; it may not reuse the closed bar
as an entry price merely because the Sharpe calculation correctly excludes the
open bar.

Rejected candidates must state whether the rejection came from value,
catalyst, liquidity, downside, sample quality or path quality. Do not say “high
Sharpe therefore buy” or “negative Sharpe therefore undervalued.”

## Promotion

Require three non-overlapping reproductions, at least 20 resolved paper trades,
55% win rate, 5% net return, drawdown no worse than -15%, and paired ablation
with no more than two percentage points of drawdown deterioration. Until then,
the factor cannot affect real-money execution.
