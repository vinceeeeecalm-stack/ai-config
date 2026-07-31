# Dual Sample and Forward Validation Policy

## Scope

Active owns discovery, Paper execution and validation evidence. Manual remains
the final portfolio and execution authority.

Every Active candidate must be preserved as an observation even when it is
rejected, not triggered or superseded. Only a Paper fill with a reproducible
next-bar execution contract may become a trade sample.

## Output lanes

### ObservationSampleV1

Record:

- candidate and setup IDs;
- rail, request mode, strategy family, entry mode and strategy version;
- frozen evidence and market regime;
- accepted/rejected state and blocker;
- entry trigger state and review deadline;
- risk-adjusted path evidence when available.

This lane supports trigger rate, filter effectiveness, missed-opportunity review
and probability calibration. It does not contribute to trading win rate.

### TradeSampleV1

Record only after Paper entry:

- next executable bar and entry price;
- target, stop and time stop;
- fees, slippage and gap assumptions;
- exit, MFE, MAE, holding duration and path ambiguity;
- market regime and strategy version.

Crypto, US equity, Paper and live results must never be pooled. Active must
never write live capital results.

## Forward validation

Promotion evidence requires:

- at least 30 comparable, no-lookahead, closed trade samples;
- at least three non-overlapping forward windows;
- positive net return after fees and slippage;
- maximum drawdown no worse than 15%;
- paired ablation against the version without the proposed factor;
- effectiveness in at least two market regimes;
- no domination by a single outlier winner.

Sharpe, Sortino and Information Ratio remain descriptive and ranking evidence
until these gates pass. If a daily strategy return series is unavailable,
annualized ratios must be reported as unavailable rather than calculated from
unordered trade returns.

## Daily discipline

Before scanning new candidates, update every open Active observation and Paper
trade. Record entry, target, stop, time expiry, MFE, MAE, data gaps and the next
check time. Generate due review drafts automatically. New discovery must not
hide unfinished historical monitoring.

