# Polymarket High-Confidence Alpha V1

Independent, paper-only Polymarket research loop. It never signs or submits an
order. Missing probability, rule, source, calibration, or order-book evidence
is a hard `PASS`.

The highest business priority is the `polymarket_daily_priority.py` path. Each
fresh full-market snapshot is reconciled against authoritative Gamma details,
then every genuinely expiring 1–24 hour binary market receives both token books
and its current fee contract. Sampling/Gamma expiry conflicts, missing rules,
thin books and unavailable models are excluded per market. At most two candidates
may reach the main paper ledger; the 1–30 day inventory remains research/watch
only so it cannot consume the two daily-priority slots. The daily report also
builds an event-family model-coverage queue: applicable failed models are frozen
from reuse against inspected holdouts, while uncovered families receive an exact
data/OOS requirement instead of an invented probability.

## Implemented core

- Gamma-format market normalization and domain routing.
- Official public Gamma/CLOB discovery, order-book, CLOB fee-contract and batch
  price-history snapshots with SHA-256 manifests.
- External, versioned probability-estimate contract. The scanner never invents
  a probability from the market price.
- Conservative confidence/uncertainty discount, spread, fee, slippage, depth,
  and price-impact-aware net EV gate.
- Maximum two open paper positions and a maximum 20% equity risk pool.
- Symmetric YES/NO evaluation: the model contract remains a traceable YES
  probability, while execution can buy either outcome token when its transformed
  probability, confidence interval, book and net EV pass every gate.
- Append-only simulated order and position-observation records, settlement,
  loss/early-exit back cases, and post-exit counterfactual tracking to resolution.
- Evidence-gated paper strategy evolution: three similar reviewed failures, or
  one explicit data/rule error, before a conservative overlay can activate.
- Stable strategy/change IDs on candidates, positions, and paper orders, plus
  automatic rollback after five degraded forward samples.
- Calibration, Brier score, log loss, CLV, drawdown, net ROI, and same-window
  Binance comparison audit.
- Resolved-only forecast scoring: an early exit is excluded from Brier/Log Loss
  until its counterfactual resolution is proven.
- A 60-minute minimum CLV protocol tied to the first eligible immutable book
  observation; early exits continue collecting CLV evidence until resolution.
- Strict Back Case completeness joined by paper trade ID; an early-exit review
  cannot complete before final resolution and exit-versus-hold value are known.
- Read-only export from the canonical Binance paper ledger into exact 30-day,
  same-capital, full-friction comparison windows.
- Offline self-test and fixtures so safety behavior is testable without network.

## Commands

```bash
python3 polymarket-alpha/scripts/polymarket_daily_priority.py

python3 polymarket-alpha/scripts/polymarket_daily_sports_report.py
python3 polymarket-alpha/scripts/polymarket_daily_sports_acceptance.py

python3 polymarket-alpha/scripts/polymarket_lolesports_gpr_lab.py

python3 polymarket-alpha/scripts/polymarket_alpha.py self-test

python3 polymarket-alpha/scripts/polymarket_public_data.py discover-live \
  --max-markets 100 --aux-limit 100 --include-books \
  --output-dir polymarket-alpha/cache/live

python3 polymarket-alpha/scripts/polymarket_public_data.py discover-history \
  --max-markets 500 --include-price-history \
  --output-dir polymarket-alpha/cache/history-500

python3 polymarket-alpha/scripts/polymarket_historical_replay.py run \
  --snapshot-dir polymarket-alpha/cache/history-500 \
  --forecast-horizon-hours 24 \
  --output polymarket-alpha/experiments/historical-replay.json

python3 polymarket-alpha/scripts/polymarket_probability_lab.py run \
  --historical-replay-json polymarket-alpha/experiments/historical-replay.json \
  --output polymarket-alpha/experiments/probability-calibration-lab.json

python3 polymarket-alpha/scripts/polymarket_runner.py \
  --max-markets 0 \
  --book-limit 50 \
  --estimates-json estimates.json

python3 polymarket-alpha/scripts/polymarket_strategy_evolver.py --dry-run

python3 polymarket-alpha/scripts/binance_window_exporter.py

python3 polymarket-alpha/scripts/polymarket_window_comparator.py

python3 polymarket-alpha/scripts/polymarket_domain_inventory.py \
  --snapshot-dir polymarket-alpha/cache/forward_cycle_002_20260711

python3 polymarket-alpha/scripts/polymarket_crypto_barrier_lab.py \
  discover-history-sliced --start-month 2024-06 --end-month 2026-06

python3 polymarket-alpha/scripts/polymarket_crypto_barrier_lab.py fetch-binance-klines
python3 polymarket-alpha/scripts/polymarket_crypto_barrier_lab.py fetch-polymarket-histories
python3 polymarket-alpha/scripts/polymarket_crypto_barrier_lab.py walk-forward

python3 polymarket-alpha/scripts/polymarket_shadow_forecaster.py

python3 polymarket-alpha/scripts/polymarket_weather_lab.py discover-history
python3 polymarket-alpha/scripts/polymarket_weather_lab.py fetch-forecast-archive
python3 polymarket-alpha/scripts/polymarket_weather_lab.py fetch-market-trades
python3 polymarket-alpha/scripts/polymarket_weather_lab.py walk-forward
python3 polymarket-alpha/scripts/polymarket_weather_shadow.py

python3 polymarket-alpha/scripts/polymarket_weather_nyc_lab.py discover-history
python3 polymarket-alpha/scripts/polymarket_weather_nyc_lab.py fetch-forecast-archive
python3 polymarket-alpha/scripts/polymarket_weather_nyc_lab.py fetch-market-prices
python3 polymarket-alpha/scripts/polymarket_weather_nyc_lab.py walk-forward

python3 polymarket-alpha/scripts/polymarket_football_lab.py discover
python3 polymarket-alpha/scripts/polymarket_football_lab.py fetch-odds
python3 polymarket-alpha/scripts/polymarket_football_lab.py fetch-market-prices
python3 polymarket-alpha/scripts/polymarket_football_lab.py walk-forward
python3 polymarket-alpha/scripts/polymarket_football_shadow.py

python3 polymarket-alpha/scripts/polymarket_social_count_lab.py discover

python3 polymarket-alpha/scripts/polymarket_stock_weekly_lab.py discover-history
python3 polymarket-alpha/scripts/polymarket_stock_weekly_lab.py fetch-market-prices
python3 polymarket-alpha/scripts/polymarket_stock_weekly_lab.py fetch-ohlc
python3 polymarket-alpha/scripts/polymarket_stock_weekly_lab.py walk-forward
python3 polymarket-alpha/scripts/polymarket_baseball_lab.py discover-history
python3 polymarket-alpha/scripts/polymarket_baseball_lab.py fetch-cutoff-prices
python3 polymarket-alpha/scripts/polymarket_baseball_lab.py fetch-mlb-schedule
python3 polymarket-alpha/scripts/polymarket_baseball_lab.py walk-forward
python3 polymarket-alpha/scripts/polymarket_baseball_lab.py report
python3 polymarket-alpha/scripts/polymarket_esports_lab.py discover-history --title all
python3 polymarket-alpha/scripts/polymarket_esports_lab.py audit
python3 polymarket-alpha/scripts/polymarket_esports_lab.py fetch-cutoff-prices --title lol
python3 polymarket-alpha/scripts/polymarket_geopolitics_lab.py
python3 polymarket-alpha/scripts/polymarket_elections_lab.py
python3 polymarket-alpha/scripts/polymarket_elections_lab.py --discover-history
python3 polymarket-alpha/scripts/polymarket_macro_lab.py --discover-history
python3 polymarket-alpha/scripts/polymarket_family_identity_enrichment.py
python3 polymarket-alpha/scripts/polymarket_mentions_lab.py --discover-history
python3 polymarket-alpha/scripts/polymarket_fdv_lab.py --discover-history
python3 polymarket-alpha/scripts/polymarket_social_count_lab.py fetch-history
python3 polymarket-alpha/scripts/polymarket_social_count_lab.py fetch-market-prices
python3 polymarket-alpha/scripts/polymarket_social_count_lab.py walk-forward
python3 polymarket-alpha/scripts/polymarket_social_count_shadow.py

python3 polymarket-alpha/scripts/polymarket_model_registry.py

python3 polymarket-alpha/scripts/polymarket_validation_cycle.py \
  --runner-timeout-seconds 300 --shadow-timeout-seconds 240 \
  --weather-shadow-timeout-seconds 180 \
  --football-shadow-timeout-seconds 180 \
  --social-count-shadow-timeout-seconds 180

python3 polymarket-alpha/scripts/polymarket_alpha.py scan \
  --markets-json markets.json \
  --books-json books.json \
  --estimates-json estimates.json \
  --output scan.json

python3 polymarket-alpha/scripts/polymarket_alpha.py enter \
  --scan-json scan.json \
  --ledger polymarket-alpha/data/paper_ledger.json

python3 polymarket-alpha/scripts/polymarket_alpha.py settle \
  --ledger polymarket-alpha/data/paper_ledger.json \
  --resolutions-json resolutions.json

python3 polymarket-alpha/scripts/polymarket_alpha.py monitor \
  --ledger polymarket-alpha/data/paper_ledger.json \
  --books-json books.json \
  --estimates-json updated_estimates.json \
  --events-json event_state.json

python3 polymarket-alpha/scripts/polymarket_alpha.py audit \
  --ledger polymarket-alpha/data/paper_ledger.json \
  --binance-metrics-json binance_metrics.json \
  --validation-metrics-json validation_metrics.json
```

Estimate input is keyed by market ID. Required fields include `probability`,
`confidence_low`, `confidence_high`, `model_version`, `calibration_samples`,
`sources`, `rules_review`, and `failure_paths`. A source has `kind` equal to
`official` or `independent`; rule review must explicitly be `clear`.

Back cases remain `review_required` by default. The learner only reads records
marked `reviewed`, `completed`, or `complete` with a `failure_category` and a
structured `proposed_change`. Allowed changes can only tighten paper gates,
block a model version, or cap a domain/model probability. Loosening gates,
live-order flags, private APIs, and real-money parameters are not allowlisted.
The persistent overlay is `config/paper_strategy_overlay.json`; `--dry-run`
never writes it or the ledger.

Order books are keyed by token ID with `bids` and `asks` arrays containing
`price` and `size`. Prices are dollars per share; sizes are shares.

## Current boundary

This is the deterministic execution/risk/evidence kernel. The official
500-market catalog and market-price baseline replay now exist; domain-specific
probability models, richer live event feeds, and the 100-trade forward
experiment remain evidence work. Until calibrated model plugins provide valid
estimates, the correct output is an empty candidate list.

The strategy learner is operational but has zero reviewed back cases and zero
forward samples, so the current overlay is intentionally empty. Its existence
is an auditable adaptation mechanism, not evidence of Alpha.

The first independent domain model family is restricted to Binance spot
first-passage price barriers. It rejects point-in-time closes, price ranges,
Volmex indexes, announcements, and token launches. Threshold ladders sharing a
symbol and expiry stay in one chronological split group. The current V2 model
failed the paired event-group OOS gate and its reused holdout is diagnostic
only, so it emits no paper estimates.

The failed V2 empirical model is frozen in a separate research-only shadow
forecaster. It records fresh 1-hour-to-30-day BTC/ETH/XRP barrier scores,
three-source spot evidence, the contemporaneous Polymarket benchmark, rules
hashes and snapshot provenance in `data/research_forecast_ledger.json`. It
cannot emit paper estimates or mutate `data/paper_ledger.json`. Resolved scores
are evaluated by independent event group; even positive shadow evidence only
produces a manual-research-review state and never promotes itself.

The second independent research domain is the Dallas daily high-temperature
ladder. It uses complete Gamma series history, Open-Meteo GFS/ECMWF fixed
24-hour-lead archives and the last official public Data API trade before a
fixed local-day-start cutoff. The Data API's documented `end` filter was
empirically observed returning later trades, so the implementation pages each
condition newest-first and applies the cutoff locally. Weather V1 lost to the
market in both chronological OOS segments and remains frozen as a failed,
research-only shadow model. Events before its freeze timestamp are never
backfilled as forward forecasts.

A prospective Weather V2 feasibility gate tested a materially different
market-temperature calibration plus weather log-opinion pool. Its grid and fit
were selected strictly on the original 60% development segment; the already
inspected validation/final segments were diagnostic only. The best development
candidate still had negative 95% Brier and log-loss lower bounds, and both old
diagnostics worsened. The family was rejected before protocol freeze, so no V2
shadow or paper estimate exists.

The third independent research domain is FIFA World Cup 90-minute 1X2. It maps
official ESPN fixtures to Gamma main events, excludes advance/qualification and
prop markets, de-vigs archived DraftKings odds, and benchmarks them against
Polymarket prices one hour before kickoff. V1 failed sustained chronological
OOS improvement and is frozen after its final holdout inspection. Its separate
football shadow ledger can record only genuinely post-freeze forecasts during
the fixed one-hour pre-kickoff window; it cannot emit main paper estimates.

The fourth independent research domain is X social post-count ladders. It uses
official XTracker tracking periods, hourly counts and final totals, exact Gamma
event links, and entry-time CLOB prices. Truth Social is explicitly excluded
because its current tracker totals disagree with multiple Gamma resolutions.
The fixed trailing count-distribution V1 failed sustained OOS improvement and
is frozen into a separate one-hour-cutoff shadow ledger with no paper estimates.

Forward counts and domain counts are always derived from the Polymarket ledger;
an external validation JSON cannot increase them. The market-score gate needs
a versioned comparison artifact whose model and market sample counts exactly
match the resolved ledger forecasts. A legacy Binance summary never satisfies
the same-window gates.

Zero-trade Polymarket windows remain valid because `PASS` is a required action
when no positive net EV exists. Such a cash window counts only when the ledger
proves continuous observation from the exact window start through its end and
there is no unmarked boundary position.

New paper entries additionally require `--max-markets 0` to reach the terminal
Gamma keyset cursor with an intact snapshot. Finite probes and budget-exhausted
full scans are observation-only and hard-block entry. A cash window requires
successful full-market observation events with no gap greater than six hours;
`updated_at` alone is not accepted as coverage evidence.

`polymarket_validation_cycle.py` is the single operational entrypoint. It uses
an exclusive stale-safe lock, fixed current snapshot/report paths, per-step
timeouts, and runs Binance-window export → model-registry refresh → deadline-sensitive weather/football/social-count/stock-weekly shadows → full Polymarket runner → market-family audit → isolated Crypto shadow → forward-protocol audit → same-window comparison → goal audit. A local Codex
automation invokes it every hour; the prompt explicitly forbids private
APIs, real orders, wallets, leverage and model/risk-parameter changes.

After each complete live snapshot, `polymarket_market_family_audit.py` groups
markets by event ID and ranks domain families for research feasibility only.
Repeated thresholds inside one event never count as independent samples, and
limited book coverage is reported as unknown rather than illiquid. The current
audit first selected baseball and froze a research-only protocol for pregame
full-game moneylines at T-24h and T-60m. Baseball Elo V1 subsequently failed
both validation and final OOS comparison against the market, so it is blocked.
The router previously selected esports, with each title isolated and only pregame
full-match winners in scope. LoL has a complete point-in-time market benchmark.
The public server-rendered official LoL Esports page now supplies timestamped
GPR/Elo history for 58 teams, allowing a 457-match T-24h research replay without
an API key. The frozen V1 direct-match model underperformed the market in both
validation and final holdout, while bulk pagination authorization and historical
revision provenance also remain unresolved. It is registered as failed and emits
no paper estimate. Tournament outrights and tournament-stat props are separate
families and cannot inherit this direct-match model.

The evidence-aware family router now consumes
`experiments/family-research-status.json`. A family that fails a data, rule or
OOS gate is removed from the unresearched queue without deleting its reusable
cache. Geopolitics currently has no homogeneous live subtype that passes the
strict ambiguity gate. Elections isolated US governor general markets, but
terminal Elections-tag history contains only 17 strict settled governor races
and only two unambiguous-rule races versus the frozen 150-event split gate.
Neither family emits a model probability or paper-entry permission. Global
family counts now require a real Gamma event ID or public CLOB negative-risk
group; condition IDs are never used as an independence fallback. With that
correction, the next research family is macro indicators (176 contracts, 42
proven event clusters), under a taxonomy-first protocol with no selected model.
Its terminal tag history contains 170 settled events, but the largest strict
homogeneous group is only 12 US GDP advance-estimate events versus 150 required,
so the family is blocked before model fitting.

The separate family-identity enrichment artifact resolves Gamma event IDs for
otherwise ungrouped CLOB conditions without mutating the immutable snapshot.
The current run found 1,076/1,077 missing identities. Mentions then reduced 535
live contracts to 30 events and terminally scanned 937 settled events. Its
largest strict homogeneous group is 128 Trump political appearances, below the
150-event frozen split gate, so no mentions model is fitted.

FDV currently contains 439 live threshold contracts across 56 launch events,
but every contract leaves the price venue as the unspecified "most liquid"
source. Terminal history contains 93 settled FDV events and zero strict events,
so FDV is blocked. When no unresearched family has 30 live events, the router
may now choose a lower-inventory family for history feasibility only; this
fallback cannot select a model or authorize paper entry.

That fallback next selected finance daily direction. The official Finance
Updown history reached terminal cursor with 1,242 strict Pyth equity
symbol-date events over 74 settlement dates. Same-date symbols remain in one
chronological segment and confidence bounds are clustered by date. Public CLOB
history supplied 1,208 complete T-24h and 1,241 complete T-60m benchmarks;
split-adjusted daily OHLC supplied features for 1,233 events, with SPCX
excluded for insufficient history. The frozen L2-logistic V1 underperformed
the market in validation and the already-inspected historical diagnostic
segment, so it is blocked and emits no paper estimates. Historical trades are
not execution evidence because they do not recover spread, depth, VWAP or
impact; any future materially different model also requires fresh post-freeze
OOS dates.

After recording that failure in the evidence router, the next history-only
fallback is basketball. Its current 157-contract inventory reduces to 20
proven event groups and has no event inside 30 days; visible contracts are
dominated by WNBA/NBA season futures, awards, statistical leaders and roster
outcomes rather than a homogeneous near-term game market. Basketball therefore
was separated into terminal NBA, WNBA and Summer League single-game histories.
NBA supplied 1,392 strict moneylines and WNBA 452; independent ESPN monthly
scoreboards mapped 1,310 NBA and 446 WNBA games with zero winner disagreement,
and official CLOB history supplied both fixed cutoffs. Frozen development-only
Elo grids for NBA and WNBA both underperformed the market in validation; WNBA
also had only 25 complete validation dates versus 30 required. Summer League
had only 19 games. Basketball is therefore blocked with no paper estimates.

The router then audited markets outside its original 14 families. Public Gamma
condition enrichment resolved all 2,676 uncovered contracts into 882 events
and added technology, corporate events, macro policy, entertainment, token
launch and non-crypto finance barriers as explicit research families.
Technology ranked first with 824 contracts and 215 events, but strict Phase-0
decomposition by event type, resolution authority and named subject found no
homogeneous group of 30 events. A misleading catch-all mixed company KPIs,
FDA decisions, personnel changes, cloud incidents and product expansion; it
was split and prohibited from promotion. Technology is blocked before history
or model fitting. Corporate Events then isolated a standardized earnings-beat
contract whose rules embed the market-creation-time sell-side EPS consensus.
Terminal Earnings-tag history contains 740 comparable resolved events across
366 tickers and 127 release dates. A frozen online Beta-Binomial/shrinkage grid
used only earlier completed release dates, but underperformed Polymarket at
both T-24h and T-60m in validation and final diagnostics. IPO, private
valuation and M&A subtypes did not independently pass their rule/sample gates.
Corporate Events is therefore blocked and Macro Policy becomes the next
Phase-0 target. Macro Policy then separated individual central-bank meetings,
annual cut counts, binary hikes/cuts, recession, personnel and fiscal/trade
policy by institution, source and horizon. No homogeneous group reached 30
independent events—the largest explicit US Fed meeting group contained five—
so it was blocked before history fitting. Entertainment becomes the next
Phase-0 family. Entertainment separated reality shows, awards, box office,
music charts, releases and celebrity events by named property and resolution
authority. Emmys contained 20 events, Big Brother candidate ladders only five,
and no property/source group reached 30, so it was blocked before fitting.
Token Launch then scanned the official Token Launches and Pre-Market tags to
their terminal cursors. Only nine settled events met the strict public,
transferable, tradable and announcement-does-not-qualify definition, versus
150 required for a frozen split, and no versioned point-in-time project
milestone source is ready. The family is blocked without probability output.
Finance Barrier finally reduced 197 live contracts to 21 symbol-period events:
16 Pyth equity/ETF monthly events, four Pyth commodity monthly events and one
TradingView cash-index annual event. No asset-class/source/session/horizon
group reached 30 independent events. It is blocked at Phase 0. Every currently
routed family now has an evidence-backed blocked or failed status, so the next
family protocol is intentionally `no_eligible_family_pending_more_identity_or_history_evidence`.

Current official API contracts used by the data layer:

- `GET https://clob.polymarket.com/sampling-markets` until terminal cursor
  `LTE=` for complete live tradable discovery.
- `GET https://gamma-api.polymarket.com/markets`
- Batched Gamma `condition_ids` lookups for rules, event metadata and numeric
  market IDs only for book/model candidates.
- `GET https://clob.polymarket.com/book?token_id=...`
- `GET https://clob.polymarket.com/clob-markets/{condition_id}`
- `POST https://clob.polymarket.com/batch-prices-history`

No authentication headers, wallet methods, orders, cancellations, balances,
bridges, or withdrawals exist in the data script.

Live full discovery uses the compact official CLOB sampling cursor; Gamma
market keyset remains the historical/fallback path. Page and wall-clock budgets
plus terminal-cursor proof are hard safety contracts: incomplete pagination
writes a degraded snapshot and blocks all new paper entries.
Sampling-cursor page overlap is classified separately from corruption: repeated
rows with the same condition identity, token mapping, tradability state and
expiry are deterministically deduplicated and counted in the manifest. Missing
condition IDs or conflicting duplicate identity contracts remain fatal and
degrade the snapshot.
The validation cycle also runs a stock-weekly research shadow. It records the
frozen failed V1 model only during the 24 hours before Monday 09:29 ET, never
backfills a missed window, and never emits main-ledger paper estimates. Its
market benchmark is the official two-sided CLOB midpoint; each row also stores
a $75 simulated buy VWAP, spread, impact, depth, book hash and server timestamp.
Promotion evidence is scored separately on contracts that pass the executable
spread/impact gate, so wide or thin markets cannot manufacture apparent Alpha.
Every validation cycle also runs `polymarket_forward_protocol_audit.py`. It
checks all five research shadow ledgers against their allowed capture windows.
A newly observed post-window or missed cutoff degrades that cycle and can never
be backfilled. The unique miss remains permanently visible in historical
evidence, but does not falsely degrade every later on-time cycle again.
`polymarket_capture_backcase.py` converts each unique, irrecoverably missed
window into an append-only operational Back Case. It reconstructs only genuine
in-window attempts, records data/root-cause evidence and the correct pass action,
and forbids automatic model changes. Corrections use a new algorithm version
and `supersedes_backcase_id`; prior records are retained rather than overwritten.

`polymarket_deadline_shadow_cycle.py` is the lightweight fixed-window path. It
runs weather, football, social-count and stock-weekly shadows plus the forward
protocol audit under the same lock as the hourly full-market cycle, records the
next unique window and active-but-unrecorded windows, and proves by SHA-256 that
the main paper ledger and main estimate artifact are unchanged. A separate
active local schedule runs this lightweight path every 30 minutes; lock
collisions are intentionally skipped because the full cycle runs the same
deadline shadows first.

`polymarket_automation_contract_audit.py` is the read-only scheduling gate for
both orchestrators. It verifies that both tasks exist, remain active, keep their
frozen cadence and script, run locally in the intended project/CWD, retain all
paper-only safety language and contain no live/private enablement flags. Both
orchestrators run this gate before network work; a missing, paused or drifted
task blocks the cycle instead of silently weakening forward coverage.

`polymarket_automation_runtime_audit.py` separately checks the age of the last
full-validation and deadline-shadow artifacts. The configuration gate remains
strict, while runtime staleness is diagnostic so a delayed full cycle cannot
suppress the lightweight deadline monitor that may restore coverage.

`polymarket_binary_pair_arbitrage.py` adds a model-independent structural
screen after each fresh hourly snapshot. It batches official CLOB `SELL`
best asks and `BUY` best bids for every YES and NO token, with both side
semantics empirically cross-checked against complete books. It then verifies
the lowest equal-share purchase costs and highest paired sale proceeds against
full depth. A purchase research candidate must remain below the $1
binary payout after taker fees, extra slippage, depth/impact and a settlement
risk reserve. The stricter main gate still requires at least 4 cents per pair
and forward leg-risk evidence; the scanner itself never opens either leg. Each
reverse complete-set mint-and-sell calculation includes sale fees, slippage,
depth/impact and an operation reserve. General split/merge semantics are pinned
to a versioned contract sourced from current official Polymarket CTF docs;
paper promotion remains hard-blocked until each candidate has read-only proof
of its prepared condition, correct standard/Neg-Risk adapter route and forward
leg-risk behavior. Prepared binary conditions are checked only after an
economic candidate appears, using `getOutcomeSlotCount(conditionId)` against
two allowlisted Polygon public RPCs; chain, contract, selector, slot count and
source quorum are frozen in the semantic contract.
Each run appends a deduplicated observation to
`data/binary_pair_research_ledger.json`, including the snapshot hash, lowest
verified pair cost and candidate counts, while proving the main ledger and
formal estimate artifact hashes are unchanged.
