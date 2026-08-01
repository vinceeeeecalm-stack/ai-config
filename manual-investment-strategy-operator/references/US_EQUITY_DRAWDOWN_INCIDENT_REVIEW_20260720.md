# US Equity Drawdown Incident Review - 2026-07-20

## Scope

This review covers the APLD position entered around the user-stated `$46` cost
and the prior SOXL tactical recommendations. It separates facts observable at
the time from hindsight. The purpose is to repair the process, not to pretend
that every drawdown was predictable.

## APLD Finding

At a current reference price near `$28.2`, a `$46` entry is down about 39%.
The June 5 screenshot implies a higher approximate average cost near `$51.1` for
54 shares, so the exact broker cost remains an evidence gap.

The structured recommendation record is missing, although the archived session
contains the original recommendation: roughly `$44-$46` entry, `$53-$56` target,
`$40.5` stop, about 62% target probability and 78 execution-readiness points.
Presenting this sub-80 setup as a first-priority buy was an action-language
failure. The missing structured record is a second control failure because the
system could not automatically enforce or review the original invalidation.

### Risks observable before or during the drawdown

- The stock had approached a 52-week high after a very fast run. Entry needed an
  extension and crowding penalty, not only a catalyst score.
- Fiscal Q3 2026 reported strong revenue growth but also a `$100.9m` attributable
  net loss, `$2.7b` debt, sharply higher SG&A and stock compensation, and a share
  count increase from 224.9m to 285.4m year over year.
- June filings added a `$350m` revolver, `$1.59b` of 7% secured notes and increased
  the Series G preferred commitment to `$2.0b`. These actions funded growth, but
  they also raised leverage, interest and dilution risk.
- The stock's failure to hold gains after verified project-delivery news was a
  negative price-reaction signal. Positive news without durable relative strength
  should have blocked averaging down.
- The July 27 earnings date created a known gap-risk window.

Primary sources:

- [APLD fiscal Q3 2026 results](https://ir.applieddigital.com/news-events/press-releases/detail/148/applied-digital-reports-fiscal-third-quarter-2026-results)
- [June 8 financing 8-K](https://www.sec.gov/Archives/edgar/data/1144879/000149315226027857/form8-k.htm)
- [June 16 secured-notes 8-K](https://www.sec.gov/Archives/edgar/data/1144879/000149315226028899/form8-k.htm)
- [June 26 preferred commitment 8-K](https://www.sec.gov/Archives/edgar/data/1144879/000149315226030333/form8-k.htm)
- [APLD fiscal Q4/FY2026 earnings date](https://ir.applieddigital.com/news-events/press-releases/detail/158/applied-digital-sets-fiscal-fourth-quarter-and-full-year)

### Missed control points

1. Pre-entry financing/dilution underwriting was not mandatory.
2. The report did not estimate target-before-stop versus stop-before-target.
3. No 5/10/20-day maximum-adverse-excursion or event-gap scenario was shown.
4. Cost drawdown and relative-underperformance alerts were not converted into a
   timely user-facing repair/trim decision.
5. The real position was never backfilled into recommendation history.
6. After the original `$40.5` invalidation was breached, later discussion moved
   the effective exit area down toward `$37.5-$38`. This was stop-loss drift: the
   market invalidated the old plan, but the system loosened it instead of forcing
   a new approval.

## SOXL Finding

SOXL is not a current holding and should not be described as a realized-loss
case. The best available ledger and user reports indicate 16 shares at about
`$155`, followed by sales of eight shares near `$225` and eight near `$250`.
That implies an approximate `$1,320` gain, or 53.2%, before fees and tax. The old
records also contained a conditional entry, a stop near `$188`, a maximum
ten-trading-day window, probability around 62% and readiness around 78. The
remaining failure was process quality: conditional language and daily-reset risk
were not strong enough, and degraded scanner states still produced concrete
entry instructions.

SOXL targets 300% of the semiconductor index's **daily** return. Direxion warns
that multi-day performance can diverge materially because of daily rebalancing
and compounding. Historical analysis supplied by the research committee found
that in comparable deep-downtrend states, the next-month positive-close rate was
about 56.5%, while the median interim adverse move was about 20.5%.

Primary source:

- [Direxion SOXL product and risk disclosure](https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs)

### Missed control points

1. The daily-reset and volatility-decay warning was not the first conclusion.
2. The plan did not quantify gap-through-stop loss or common adverse paths.
3. Entry and invalidation zones could overlap, making the plan internally weak.
4. The underlying SOX/SOXX trend, breadth, rates and earnings cluster were not a
   mandatory daily recertification package.
5. Repeated equivalent recommendations were superseded rather than resolved,
   so the probability record did not learn from the market outcome.

## Root-Cause Classification

| Failure | APLD | SOXL |
|---|---|---|
| Data collection | Missing complete broker cost and original record | Product data existed but was not foregrounded |
| Fundamental underwriting | Financing/dilution/capital gap underweighted | Not the primary issue |
| Regime/crowding | High-rate, crowded AI setup underweighted | Semiconductor crowding and rate sensitivity underweighted |
| Path risk | MAE and gap risk absent | Daily-reset, MAE and gap risk insufficient |
| Execution | No enforced recertification or repair deadline | Stop/time limit existed but was not reliably closed out |
| Learning loop | Real trade absent from structured history | Profitable real exit and duplicate recommendations were not resolved into a clean outcome |

## Required Repairs

- Run `Pre-Entry Downside and Capital Risk Gate` before every US tactical main
  recommendation.
- Backfill user-confirmed real trades that lack a recommendation record.
- Reuse one active recommendation ID when evidence is unchanged; do not create
  repeated pseudo-samples.
- Force an outcome review when the time window or stop is reached.
- Treat a material financing filing or positive-news failure as an immediate
  thesis challenge.
- For leveraged ETFs, use 1-3 day default review, daily recertification after
  day five, and an absolute ten-day maximum.

## Current Consequence

APLD is a red repair/trim-review holding and must not be averaged down before its
earnings and capital-risk review. SOXL is `paper_only/watch` for a new cycle until
the underlying semiconductor trend and volatility regime improve. Its previous
cycle was profitable, but that does not validate the weaker controls. Neither
can be promoted by a single rebound session.
