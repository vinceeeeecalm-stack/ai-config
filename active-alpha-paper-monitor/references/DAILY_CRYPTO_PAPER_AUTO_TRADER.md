# Daily Crypto Paper Auto Trader

## Purpose

This is the daily paper simulation of a future crypto API trading route. It tests whether automated market scans can review, open, and close virtual spot positions fast enough to make the monthly-double target measurable.

The current version is paper-only. It does not place real orders, call private trading APIs, withdraw funds, use margin, futures, perpetuals, or leverage.

V2.136 requires every entry-capable daily/fast/Sunday or auxiliary sampler run
to evaluate `paper_testnet_risk_controls` before selecting or sizing a new paper
position. A `block_new_entries` decision skips entry scanning or records a
blocked candidate; `reduce_new_entry_size` applies the risk multiplier and must
not be raised back up by target-pressure or scale-in logic. Exit review continues
even when new entries are blocked.

## Cadence

- Timezone: `Asia/Shanghai`
- Suggested automation: every 4 hours by default
- Paired fast exit automation: `paper_position_exit_monitor.py`, hourly by default
- Paired fast entry automation: `fast_crypto_paper_auto_trader.py`, hourly by default
- Manual stress-run: allowed with `--allow-outside-window`
- Ledger: existing `$500` paper portfolio ledger
- Scope: crypto spot long paper only

## Standard Flow

1. Acquire lock.
2. Load config, strategy state, and paper ledger.
3. Fetch Binance spot ticker, book, depth, 24h stats, and K-line history.
4. Cross-check CoinGecko prices when available.
5. Fetch information pressure: CoinGecko trending, Reddit 24h keyword search, funding, and open interest.
   - Public exchange market-data calls should retry transient failures before marking a position degraded.
   - The configured `symbols` list is only a baseline and fallback. Before each fast/daily scan, rebuild the effective scan pool from live Binance USDT spot breadth, liquidity, 24h movement, asset bucket bias, recovery queue and latest social/key-person sentiment.
   - Market mood controls the pool shape:
     - `risk_off_rebound_watch`: preserve open/core symbols first; prefer BTC/ETH/BNB/SOL/TRX and liquid rebound candidates; reduce high-beta/meme priority.
     - `risk_on_momentum`: widen to liquid high-beta leaders, AI/L1/meme momentum and large-cap beta.
     - `selective_high_beta_rotation`: prioritize top volume acceleration plus confirmed social narratives.
     - `mixed_selective`: keep liquid leaders and only add high-beta names with enough liquidity or catalysts.
   - Social/news sentiment is not a direct entry trigger. Positive confirmed catalysts can raise scan priority; risk-alert clusters reduce long-score weight, increase monitoring priority, and should not create standalone long paper entries.
6. Review open paper positions using realistic sell assumptions.
   - Apply dynamic exits before waiting for expiry: hard stop, take profit, trailing profit protection, and stale information-decay exits.
   - `15m` positions use faster trailing profit protection and shorter information-decay windows than longer-horizon probes.
   - Every simulated buy/sell must create a `paper_order` record with side, type, status, fill price, fee, spread, slippage, depth, reason, and `live_orders_enabled=false`.
7. Scan current long-only signals with walk-forward train/OOS summaries.
   - Daily mode uses a bounded, cross-family fast scan by default so scheduled runs stay timely.
   - Daily mode includes `15m` short-horizon strategies for faster opportunity detection; these use shorter lookbacks, shorter holding windows, and a shorter historical cache window than `1h/4h/1d`.
   - Candidate output must be diversified across symbols so one already-open symbol cannot crowd out the whole watchlist.
   - Daily mode may widen the info prefilter and reduce per-symbol candidate caps when paper cash is idle, so the scan can find alternative symbols instead of repeatedly rejecting already-open names.
   - Sunday/manual stress mode can keep the full deep scan for broader research coverage.
8. Evolve strategy version inside paper only.
9. Open one or more new paper positions if gates pass and open-position limits allow.
   - Daily mode can open up to the configured `max_new_positions_per_run` distinct symbols in one run.
   - Multi-open is only for paper simulation; it must still reserve cash/exposure for earlier selected candidates before sizing later ones.
   - Use dynamic paper sizing: `$25` minimum probe, `$50` mid probe, `$75` accelerated probe, or up to `$150` strict robust signal.
   - Sizing requires evidence: OOS win rate/final capital/drawdown, information pressure, idle cash, exposure capacity, and valid liquidity/depth.
   - Daily exploratory probes may start from lower information pressure than Sunday stress tests, but weak evidence remains minimum-size paper only.
   - Sizing is paper-only and must never be interpreted as live trade authorization.
   - If a current signal is classified as `paper_only` and passes `validation_probe_*` thresholds, the system may open a minimum-size `validation_probe` paper position even when social/info pressure is low. This exists only to collect forward samples for the validation gate; it cannot become `small_probe_review`, `conditional_action`, or real-money advice without later proof.
10. Write report, handoff, experiment, paper-trade record, and updated ledger.

The daily trader is responsible for candidate discovery and possible paper entries. Faster exit checks should be handled by `paper_position_exit_monitor.py` so open positions are not forced to wait for the next full strategy scan.

For timely entries, use `fast_crypto_paper_auto_trader.py` as the hourly execution layer. It scans only `15m/1h` with smaller strategy limits, writes the same report/handoff/experiment structure, and keeps all safety gates and paper-only restrictions. The full daily trader can remain deeper and slower because it is no longer the only entry path.

Fast and daily modes may run a controlled `winner_scale_in_probe` when cash is idle and the only passing signal is already open. This is paper-only pyramiding, limited by existing profit/profit-protection, info score, per-symbol exposure cap, order-book liquidity, spread/slippage, and cross-source validation.

V2.42 makes the scan pool itself dynamic. The shared fast/daily engine now records `dynamic_scan_pool` in every report, handoff and experiment. Operators must read `market_atmosphere`, `pool_bias`, `sentiment_state`, selected symbols and top dynamic candidates before judging whether the loop is too conservative or too aggressive. A static pool is allowed only when Binance all-market 24h data is unavailable, offline fixtures are being used, or the dynamic pool is explicitly disabled.

V2.43 makes the dynamic pool shape itself depend on market mood and social sentiment. The scanner no longer fills every remaining slot by raw score alone. It first preserves open, explicit, recovery and core symbols, then applies auditable bucket quotas:

- `risk_off_rebound_watch`: more core/large liquid names and rebound watches; cap high-beta and meme exposure in the scan pool.
- `risk_on_momentum`: reserve more slots for high-beta leaders while keeping enough core liquidity anchors.
- `selective_high_beta_rotation`: prioritize volume acceleration plus confirmed social catalysts, but still cap crowded meme/high-beta exposure.
- `mixed_selective`: balance liquid leaders, infra/large-cap names and confirmed catalysts.
- `risk_alert_cluster`: keep risk names visible for monitoring, but cap their ability to crowd out cleaner long candidates.

Every report must show `pool_shape_policy`: reason, min slots, max slots and selected counts. These quotas only shape scanning and paper evidence collection; they do not loosen current-signal, liquidity, recovery, validation-capacity, profit-protection or live-trading bans.

V2.44 makes the scan pool width itself adaptive. The configured symbol limit is now a baseline: `risk_on_momentum` and `selective_high_beta_rotation` can expand the effective scan width to catch broader high-beta or volume-acceleration rotations; `risk_off_rebound_watch` and `risk_alert_cluster` contract the width toward liquid/core/rebound names. Positive confirmed social catalysts can reserve catalyst slots and modestly expand width, while risk-alert social clusters contract width and cap meme/high-beta/social-catalyst crowding. Every report must show `pool_width_policy` with configured limit, effective limit, multiplier and reason. This only changes what gets scanned and ranked; all paper/live safety gates remain unchanged.

V2.7 adds `target_sprint_scale_in` for the monthly-double paper stress test. When cash ratio is high, monthly progress is still far behind, and an existing winner remains protected by profit/protection gates plus current information pressure, the paper system may allow a third same-symbol probe and raise the scale-in notional to `$50`. This is deliberately limited to paper records and must be tagged in `scale_in_evidence`; it is not a real-money instruction. Target-sprint scale-ins must use a tighter dedicated loss cut than ordinary exploratory probes so capital is recycled quickly when the sprint entry is wrong.

V2.9 adds `validation_probe` sampling. The prior gate rejected many `paper_only` current-signal candidates because they lacked enough information pressure, which slowed sample collection and left the strategy promotion gate stuck. A `validation_probe` keeps the trade at minimum paper size, still checks OOS trades/win rate/final capital/net return/drawdown, requires the train window not to show an obvious loss-making fit, and still requires liquidity/friction gates. It exists only to produce measurable forward evidence.

V2.10 adds recent-loss attribution rotation. Recent failed paper trades now affect candidate ranking at two levels: symbol-level losses and strategy-level losses. Strategy-level attribution matches `strategy_family + interval + paper_entry_mode`, with smaller penalties for partial family/interval/mode matches. This prevents idle cash from immediately rotating into the same losing setup on a different token while keeping all hard gates unchanged. The mechanism only changes paper candidate ordering; it does not force entries, loosen liquidity checks, or authorize real trades.

V2.10 also adds K-line cache freshness auditing. Each run records the latest available data time, lag, and `open_bar_count` for every scanned `symbol + interval` pair. If a Binance K-line is still the currently open bar, the audit caps its available timestamp at `now` instead of reporting a future close time. Stale or missing K-line cache data marks the run degraded instead of allowing old bars to masquerade as current signals. Reports and handoffs include a `Kline Cache Freshness` panel plus `kline_cache_audit` JSON summary.

V2.11 adds paper-only crash-rebound coverage for selloff regimes. The strategy lab now includes `crash_rebound` and `liquidation_wick_reversal` families, which require recent drawdown plus confirmation from reclaim candles, lower-wick recovery, prior oversold state, and volume. These families are designed to create reviewable minimum-size paper samples during high-volatility drawdowns; they do not loosen liquidity, K-line freshness, friction, recent-loss, or real-trading gates.

V2.12 adds validation-capacity gating to the hourly orchestration path. Before a new hourly paper cycle expands exposure, `validation_progress_runner.py` reads `validation_sample_auditor.py`. If the auditor returns `pause_new_samples`, `hold_new_samples_temporarily`, `exit_only_until_slots_free`, `exit_first_then_reassess`, or `exit_monitor_only`, the runner must still review open positions but must mark fast/daily/near-miss sampling as `skipped_capacity_gate`. This prevents a weak paper ledger from opening more trades only because idle cash is high. It is a paper risk-control gate, not a live-trading permission.

V2.13 moves the same validation-capacity gate into the shared `sunday_crypto_realistic_paper_loop.py` engine used by `fast_crypto_paper_auto_trader.py` and `daily_crypto_paper_auto_trader.py`. Direct fast/daily runs now call the auditor before K-line strategy scanning. If the capacity gate says to pause new samples, the direct wrapper still reviews open positions and writes a report, but it sets scan status to `skipped_capacity_gate` and opens no new paper positions. `--ignore-validation-capacity-gate` exists only for explicit debug/offline experiments and must not be used in scheduled automation.

V2.14 improves the research-only current-signal queue. `current_signal_probe.py` now computes `promotion_blockers`, `robust_score`, `recommended_max_action`, and `why_not_live_ready` for every active signal. The ranking penalizes train/OOS mismatch, weak train windows, deep drawdowns, small OOS samples, and OOS-only luck. This keeps the system learning while new paper sampling is paused, without letting overfit candidates become the next paper-entry priority.

V2.15 moves the stale current-signal refresh rule from automation prompt text into `validation_progress_runner.py` itself. When the validation capacity gate pauses new samples, the runner reviews exits, skips fast/daily entry scans, and checks whether the latest current-signal probe evidence is older than 6 hours or missing. Stale or missing evidence triggers one research-only `current_signal_probe.py` refresh using existing caches; fresh evidence is recorded as `skipped_fresh_evidence`. This keeps the paused system learning without hourly heavy scans or new exposure.

V2.16 adds a validation recovery plan. `validation_sample_auditor.py` now groups resolved paper trades by entry mode, strategy family, interval, and symbol, then marks each group as `retire_from_new_samples`, `cooldown_until_retested`, `single_sample_watch`, or `eligible_small_paper_only`. The daily/fast paper route should treat retired/cooldown groups as quality warnings when deciding future candidate ranking. The recovery plan also reports the monthly target gap and required return from current equity, keeping strategy iteration tied to the monthly-double benchmark instead of merely accumulating samples.

V2.17 makes the recovery plan enforceable in the shared fast/daily/sunday engine. After a candidate receives a proposed `paper_entry_mode`, `sunday_crypto_realistic_paper_loop.py` runs `validation_recovery_plan_gate`. By default it blocks new paper samples whose entry mode, strategy family, interval, or symbol is marked `retire_from_new_samples` or `cooldown_until_retested`. Blocked candidates must be recorded as `validation_recovery_plan_block` in rejections and must not reach sizing, execution quote, or paper open. Existing open positions are still reviewed normally.

V2.18 applies the same rule to `current_signal_near_miss_sampler.py`. This matters because the sampler can be invoked independently by the validation runner. Near-miss sampling must no longer bypass the recovery plan; retired or cooldown modes are report-only until a separate retest changes the audit result.

V2.24 separates strong top current signals from the retired `current_signal_validation_probe` mode. A top signal with no promotion blockers and strict train/OOS quality may use `current_signal_quality_scout_probe` at minimum paper size. This quality scout can retest a weak interval, but it still cannot bypass weak symbols, retired entry modes, retired strategy families, liquidity, cash, open-position, or live-order safety gates.

V2.19 adds `strategy_recovery_optimizer.py` to the paused-capacity learning path. When validation gates retire old entry modes or strategy families, the optimizer ranks alternative walk-forward candidates that are not blocked by recovery plan. Daily/fast scanners may use this queue as research context or future sorting input only; every actual paper entry still needs current signal, liquidity, friction, capacity, and recovery gates.

V2.20 makes that sorting input explicit in the shared scanner. Recovery queue symbols may be added to the Binance K-line scan universe, and candidates matching queue symbol/interval/family can receive a visible ranking bonus. The bonus must appear in candidate attribution and never bypass the hard gates that decide whether a paper trade can be opened.

V2.5 adds target-directed paper capacity. When paper cash is still high while monthly-double progress is low, the system may expand paper slots and scan more candidates instead of stopping at a small fixed position count. This does not relax live-trading restrictions: every added paper position still needs current signal, liquidity, friction, cross-source validation, sizing, and experiment logging. High-information existing winners may use a lower OOS sample gate for paper-only scale-in, and that sample-risk must remain visible in the report/experiment.

The daily and fast scanners also ingest the latest `social_key_person_intel` handoff into `info_pressure_score`. Social intel can raise scan priority or paper sizing only after price/volume and liquidity gates pass. Risk-alert intel increases monitoring priority but is not a standalone long signal.

## Monthly-Double Tracking

The monthly double target is a benchmark, not a promise. V2.27 uses `monthly_compounding_double`: the active target is 2x the current Asia/Shanghai month-start paper equity, not always 2x the original `$500` seed. V2.28 persists that month-start value in `monthly_goal_baselines` the first time the shared paper ledger is loaded/saved for a new Asia/Shanghai month, and never overwrites an existing month baseline. Every run must preserve enough evidence to evaluate whether the target is realistic:

- current equity vs current month-start paper equity
- lifetime initial capital, current `month_id`, baseline source, and target equity
- net return percent
- realized and unrealized PnL
- max drawdown
- open-position count
- strategy version and candidate history
- rejected candidates and reasons
- simulated API order lifecycle for every paper entry and exit
- dynamic exit reason when a paper position is closed before hard stop/take/expiry
- dynamic sizing tier, reason, planned notional, available capacity, cash ratio and evidence

If paper equity does not trend toward the monthly target after realistic costs, the system should keep scanning and recording evidence rather than loosening gates without a documented reason.

V2.36 adds K-line fetch wall-clock budgeting and dataless artifact protection. Fast/daily scans still use Binance public spot K-lines, but the K-line refresh phase must stop once `kline_fetch_wall_clock_seconds` or `ACTIVE_ALPHA_KLINE_FETCH_WALL_CLOCK_SECONDS` is exhausted, mark missing pairs as degraded, and continue to a report instead of blocking the automation slot. Non-critical JSON artifacts such as strategy state or old optimizer reports may be skipped when macOS marks them `dataless`; the paper ledger is critical and must not silently fall back to a new empty ledger if it cannot be read safely.

V2.48 adds wall-clock budgets for the market snapshot and information-pressure phases as well. `market_fetch_wall_clock_seconds` covers spot ticker/book/depth plus CoinGecko price validation; `info_fetch_wall_clock_seconds` covers trending/social/futures-context reads. When either budget is exhausted, the scan must mark the missing symbols or sources as `budget_exhausted` / `skipped_budget`, continue to a readable degraded report, and refuse to force a new paper entry from stale or missing data. This preserves hourly responsiveness while keeping the scan pool dynamic: market mood and sentiment should be used when fresh, and downgraded to market-breadth-only or report-only when unavailable.

V2.49 aligns the shared fast/daily recovery gate with the near-miss sampler: `current_signal_quality_scout_probe` may retest a weak interval at minimum paper size when `allow_quality_scout_weak_interval_retest=true`. This exception does not apply to weak symbols, retired entry modes, retired strategy families, ordinary `validation_probe`, or `info_exploratory_probe`, and it does not loosen liquidity, capacity, current-signal, paper-only, or live-trading bans. Its only purpose is to collect narrowly scoped forward evidence when the system needs more Phase 2 samples without reviving a broadly weak mode.

V2.50 makes the dynamic scan-pool contract explicit. Fast, daily and Sunday paper routes must rebuild the effective scan pool from current market atmosphere and fresh sentiment whenever market data is available; the configured symbol list is only a fallback. When a quality scout candidate is blocked by both a weak interval and a weak symbol/retired group, the weak symbol or retired group wins and the candidate remains blocked. This keeps the loop adaptive without letting a broad-risk exception become a hidden exposure leak.

V2.37 adds a two-layer target-path refresh for compounding research. `target_path_candidate_refresh.py` may first use Binance public 24h stats to discover high-liquidity/high-movement USDT spot symbols, then load K-lines, score symbol/interval frames with a quick prefilter, and only run full strategy simulation on the top frames. The script must skip dataless K-line cache files instead of blocking on local file coordination, reserve wall-clock budget for simulation, and write a localized experiment copy. It also emits `pressure_retest_queue`: paper-only forward retest candidates from pressure upper-bound rows. These candidates remain research/watch material until they pass current-signal, liquidity, validation capacity, recovery and paper execution gates.

V2.38 adds `pressure_retest_queue_sampler.py` as the forward bridge for those pressure rows. It reads the latest queue, recomputes current Binance K-line signals, checks realistic execution quotes, and applies validation capacity plus recovery plan gates before any `$25` paper-only `pressure_retest_probe`. If capacity is paused, the interval/symbol/family is retired or cooldown, current signal is false, or liquidity is weak, it must write the explicit block reason and open nothing. Pressure replay remains an upper-bound research observation, not target proof or live-trading permission.

V2.39 adds durable local shadow artifacts under `/private/tmp/active-alpha-paper-monitor-shadow`. Scripts that write critical paper evidence should write both the primary workspace file and a shadow copy for ledgers, reports, experiments, compounding replays, and pressure sampler outputs. When a primary file is marked `dataless`, auditors may read the shadow copy as fallback evidence and must keep the fallback status visible. Shadow evidence only fixes local file readability; it does not loosen strategy gates, change ledger truth, prove the target path, or authorize live trading.

V2.40 hardens target-path refresh budgeting. `target_path_candidate_refresh.py` now supports `--max-loaded-frames` and uses a conservative load cutoff that accounts for both simulation reserve and HTTP timeout. In scheduled or manual budgeted runs, prefer a smaller fully simulated universe over a broad scan that exhausts its budget before strategy simulation. Reports must expose `max_loaded_frames`, `load_cutoff_seconds`, `simulation_started_elapsed_seconds`, and `strategies_scanned`; `strategies_scanned=0` should be treated as weak evidence and rerun with fewer frames.

## Execution Model

Paper orders must model:

- bid/ask spread
- base slippage
- dynamic impact from 1% order-book depth
- commission
- 24h quote volume
- cross-source price validation
- trailing/profit-protection exit checks
- information-decay exit checks for stale exploratory probes

Low-liquidity, wide-spread, thin-depth, stale, or disputed symbols must be report-only.

## Safety

- `live_orders_enabled=false` in all outputs.
- API keys must not be written to reports, logs, config, handoffs, or experiments.
- No private API endpoints, order endpoints, withdrawal endpoints, margin endpoints, futures order endpoints, or perpetual order endpoints.
- Any future real API route requires a separate approval, dry-run/testnet phase, audit log, kill switch, rollback plan, and manual authorization gate.
