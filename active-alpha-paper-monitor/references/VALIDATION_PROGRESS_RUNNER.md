# Validation Progress Runner

V2.135 adds a strict completion layer on top of the runner evidence. Run
`scripts/phase_goal_readiness_auditor.py` after evidence-producing steps to
refresh `reports/ACTIVE_ALPHA_GOAL_COMPLETION_AUDIT.md` and
`experiments/active-alpha-goal-completion-audit.json`. Only explicit
`proven/pass/ok` evidence counts as complete; partial, stale, blocked-by-design,
or manual-review-required rows remain open work. The completion auditor is
offline/read-only and must not fetch market data, mutate the ledger, restore an
automation, or authorize live trading.

`validation_progress_runner.py` is the active skill's evidence orchestration layer. It does not discover a new trading thesis by itself; it runs the existing paper exit, fast entry, optional daily deep scan, and validation audit tools in a fixed order so the system can see whether evidence is actually improving.

## Purpose

- Reduce one-off script drift by making every validation cycle comparable.
- Show whether paper samples, closed trades, equity, drawdown, calibration gaps, and walk-forward gaps improved.
- Keep live trading disabled while still testing simulated API order lifecycle.
- Produce a single report that manual skill can cite when deciding whether strategy promotion remains blocked.

## Default Order

1. `validation_sample_auditor.py --format json`
2. `paper_position_exit_monitor.py --compact-output`
3. `fast_crypto_paper_auto_trader.py --compact-output --allow-outside-window`
4. Optional: `daily_crypto_paper_auto_trader.py --compact-output --allow-outside-window`
5. Optional: `binance_kline_cache_builder.py` builds a fresh public Binance kline cache for high-liquidity/high-movement USDT pairs
6. Optional: `weekly_goal_strategy_lab.py` persisted as fresh walk-forward evidence
7. Optional: `current_signal_probe.py` persisted as current-signal evidence
8. Optional: `current_signal_near_miss_sampler.py` opens tiny paper samples for near-miss current signals or strict top current-signal quality scouts
9. Auto when paused, or optional: `strategy_recovery_optimizer.py` builds a research-only recovery retest queue
10. `validation_sample_auditor.py --format json`

The daily deep scan is opt-in through `--include-daily` because it is heavier and may need a larger network/time budget.

## Dynamic Scan Pool

When `--dynamic-scan-pool` is enabled, the runner must not treat the configured symbols as the final universe. It rebuilds the scan pool from Binance public 24h breadth, BTC/ETH direction, liquidity, trade participation, recent movers, open paper symbols, explicit user symbols, strategy recovery queue symbols and the latest social/key-person handoff. The policy is defined in `DYNAMIC_SCAN_POOL_POLICY.md`.

Reports must show market regime, market atmosphere, sentiment state, selected symbols, width multiplier, bucket quotas and top ranked candidates. If market data or social data is unavailable, the runner may fall back to a static/core list, but it must label the fallback and avoid treating it as fresh market discovery.

Dynamic pool output is still paper-only. It can reprioritize scan candidates, but it cannot bypass current-signal, K-line freshness, liquidity, friction, capacity, recovery, recent-loss or learning gates.

Walk-forward and current-signal refreshes must read a kline cache that actually contains JSON bars. Use
`--walkforward-cache-dirs` to pass a comma-separated cache list when known. If omitted, the runner now prefers
the latest `/private/tmp/binance_klines_cache_dynamic_*` directories and populated known caches such as
`/private/tmp/binance_klines_cache_validation_20260530` before falling back to legacy defaults, so refreshes do not silently produce `frames_loaded=0` and leave
`walkforward_no_target_research_pass` untested.

When the cache is stale or too narrow, use `--include-dynamic-kline-cache`. It fetches public Binance spot klines first, persists a cache-builder summary, and then lets the walk-forward/current-signal steps consume that fresh cache. This remains market-data only; no account, order, or private endpoints are used.

V2.15 adds a default stale-research guard for paused-capacity runs. If the pre-audit capacity state pauses new paper samples or switches the runner to exit-only mode, the runner still reviews open positions and skips fast/daily new entries. It then checks the latest `*current-signal-probe.json` artifact. If that research evidence is missing or older than `--current-signal-stale-hours` (default `6`), the runner refreshes `current_signal_probe.py` once using existing caches and compact output. If the evidence is still fresh, the runner records a skipped child with `skipped_fresh_evidence` instead of running a heavy scan. This keeps the system learning while avoiding unnecessary hourly research churn.

The V2.15 auto refresh is disabled by `--disable-auto-current-signal-refresh`. It never builds a dynamic Binance cache by itself and never authorizes new paper exposure; it only refreshes research evidence while the capacity gate is already blocking new samples.

V2.19 adds a default strategy-recovery optimizer for paused-capacity runs. If the pre-audit capacity state blocks new samples, the runner also calls `strategy_recovery_optimizer.py` once unless `--disable-auto-strategy-recovery-optimizer` is set. This optimizer reads latest walk-forward evidence plus `validation_recovery_plan`, filters out retired/cooldown entry modes, strategy families, intervals and symbols, and writes an `eligible_retest_queue`. The queue is research-only; it does not open paper positions and cannot authorize live trading.

## Outputs

```text
active-alpha-paper-monitor/reports/YYYY-MM-DD-validation-progress-runner-YYYYMMDD-HHMMSS-ffffff.md
active-alpha-paper-monitor/experiments/YYYYMMDD-HHMMSS-ffffff-validation-progress-runner.json
```

The runner may be called repeatedly inside the same minute while tuning paper validation. Artifact names must therefore include seconds plus a unique suffix so one run never overwrites another evidence record.

The output must include:

- pre/post validation status
- child run status
- equity and net return delta
- monthly-double recovery status and target gap
- strategy quality triage from `validation_recovery_plan`
- strategy recovery optimizer status, eligible retest queue, and blocked candidates
- open/closed paper trade delta
- paper order count delta
- failed gate delta
- sample gap delta
- open paper review calendar: nearest expiry, expiring 24h/72h counts, the next reviewable paper positions, and active profit-protection status/floor
- safety errors, especially any `live_orders_enabled=true`
- Binance public market-data health: ticker/book/depth/recent-trades/Kline endpoint coverage, spread/depth, taker-buy ratio and blocked/warning symbol counts
- Paper signal contract completeness: direction, entry, stop loss, take profit, time window, confidence, failure conditions and unsafe flag counts
- next validation queue

## Safety Rules

- The runner never enables live orders.
- Any child process that reports `live_orders_enabled=true` makes the runner `blocked_safety_error`.
- The runner recursively scans child stdout and referenced JSON artifacts for `live_orders_enabled=true`, `private_api_used=true`, `private_api_keys_used=true`, or `allow_real_orders=true`.
- Child JSON parse failures or non-zero return codes make the runner `degraded_child_failure`.
- `--dry-run` passes dry-run to child scripts and skips runner artifact writes.
- `--offline-fixture` is allowed for CI/syntax checks, but its results do not count as live-market evidence.
- The runner cannot promote a strategy above `paper_only`; manual skill must still re-check research committee, strategy promotion, double-80, data quality, and human confirmation.
- The near-miss sampler only opens minimum-size paper positions with approved paper entry modes such as `current_signal_near_miss_probe` or `current_signal_quality_scout_probe`. It is for forward evidence collection when a signal looks strong but needs live-market paper proof; it is never a real-money recommendation.
- The strategy recovery optimizer never opens paper positions. It only identifies future retest directions that must still pass current signal, liquidity, friction, capacity and recovery gates before any paper entry.

## Common Commands

Fast validation loop:

```bash
python3 active-alpha-paper-monitor/scripts/validation_progress_runner.py --cycles 1 --no-lock --compact-output
```

Paused-capacity loop with built-in stale current-signal check:

```bash
python3 active-alpha-paper-monitor/scripts/validation_progress_runner.py \
  --cycles 1 \
  --current-signal-stale-hours 6 \
  --no-lock \
  --compact-output
```

Deep validation loop:

```bash
python3 active-alpha-paper-monitor/scripts/validation_progress_runner.py --cycles 1 --include-daily --no-lock --compact-output
```

Walk-forward refresh loop:

```bash
python3 active-alpha-paper-monitor/scripts/validation_progress_runner.py \
  --cycles 1 \
  --include-dynamic-kline-cache \
  --include-walkforward-refresh \
  --include-current-signal-probe \
  --include-near-miss-sampler \
  --no-lock \
  --compact-output
```

Offline dry run:

```bash
python3 active-alpha-paper-monitor/scripts/validation_progress_runner.py --cycles 1 --dry-run --offline-fixture --no-lock --compact-output
```

With externally orchestrated research committee outputs:

```bash
python3 active-alpha-paper-monitor/scripts/validation_progress_runner.py \
  --cycles 1 \
  --external-agent-outputs-json /private/tmp/run-external-subagent-outputs.json \
  --no-lock \
  --compact-output
```

## Manual Skill Consumption

Manual reports should treat the runner as evidence hygiene. If the runner still shows insufficient paper samples, poor closed net return, unresolved probability calibration, or missing walk-forward target pass, the maximum tactical action remains `paper_only/watch` even if one child scan found a strong candidate.

V2.16 adds `validation_recovery_plan` to the runner's post-audit view. This plan turns failed paper evidence into concrete paper-only quality actions:

- `retire_from_new_samples`: do not use this entry mode or strategy family for new paper samples until a separate offline retest passes.
- `cooldown_until_retested`: keep it research/watch only until fresh evidence improves.
- `eligible_small_paper_only`: if capacity allows sampling again, this group can receive small paper-only probes, never live action.

The runner report must show monthly target gap, required return from current equity, recovery actions, and strategy quality triage so the system does not keep sampling modes that already caused the paper ledger to fall behind the monthly-double benchmark.

V2.17 carries this recovery evidence into the shared paper scanner. The validation runner itself still only orchestrates exits, scans, research refresh, and audits, but child fast/daily runs now receive the latest `validation_recovery_plan` through their capacity audit and enforce it before opening new paper samples. If a future run shows `validation_recovery_plan_block`, that is evidence of quality control working, not a live trade action.

V2.18 closes the near-miss sampler bypass. When `--include-near-miss-sampler` is used, `current_signal_near_miss_sampler.py` must read the same audit payload and apply `validation_recovery_plan_gate` before entry mode learning, cash, or open-position checks. A retired `current_signal_near_miss_probe` or `current_signal_validation_probe` must be skipped with `validation_recovery_plan_block`, even if the runner explicitly requested near-miss sampling.

V2.19 adds `strategy_recovery_optimizer.py` as the next-step planner after recovery gating. Once old modes are retired/cooldown, the optimizer prevents the system from becoming passive by ranking alternative walk-forward candidates that are not blocked by the recovery plan. These candidates are emitted as `paper_retest_watch`, not orders.

V2.20 feeds the optimizer output back into the shared scanner safely. `sunday_crypto_realistic_paper_loop.py` may add recovery queue symbols to the scan universe and may give matching candidates a `strategy_recovery_queue_bonus_points` ranking boost. This feed is still research-only: it cannot create a paper position unless the candidate independently passes current signal, K-line freshness, liquidity, friction, validation capacity, validation recovery, recent-loss, and entry-mode learning gates.

V2.21 adds a recovery shadow scan when validation capacity pauses new samples. In that state the runner still records the normal fast scan as `skipped_capacity_gate`, then runs `fast_crypto_recovery_shadow_scan` with `--dry-run --ignore-validation-capacity-gate`. This shadow child is observation-only: it can inspect live/current candidates and recovery-queue ranking pressure, but it cannot write the ledger, open paper positions, or authorize live trading. The report and compact JSON must show the shadow scan summary and top candidate attribution so the next active run can see whether recovery candidates are becoming actionable again.

V2.22 adds recovery-queue Kline prefetch before the shadow scan. The runner reads the latest `*strategy-recovery-optimizer.json`, extracts queued symbols and intervals, and invokes `binance_kline_cache_builder.py` against the fast scanner cache directory. The following shadow scan then focuses on those recovery symbols and passes the same cache dir explicitly. This avoids stale/missing `4h/1d` rows hiding promising recovery candidates. The prefetch is public market-data only and must never log API keys, use account endpoints, or open paper/live orders.

V2.34 clarifies dry-run recovery shadow reporting. The child scanner may still return `new_paper_trades` as hypothetical execution artifacts, but the runner report must label them `Hypothetical paper trades`, compact JSON must also expose `hypothetical_paper_trades`, and operators must treat the ledger as the only source of truth for actual open paper positions.

V2.35 hardens the resume safety audit used before runner resume. `safety_invariant_auditor.py` now scans line by line and applies a per-file read timeout so local file coordination issues cannot block the active loop forever. File read timeouts are warnings; live-order/private endpoint/signing/API-key findings remain blocking.

V2.36 hardens child process execution. `validation_progress_runner.py` must launch child scripts in their own process group and terminate the whole group when a child timeout is reached. This prevents a child script or its network/file-coordination descendants from holding stdout/stderr pipes open after timeout. Timed-out children are reported as structured `timeout` child runs, not as parent tracebacks, and the runner remains paper-only.

V2.50 adds a separate `--current-signal-timeout-seconds` budget. Current-signal refresh can be much heavier than the hourly fast paper loop, especially after a fresh dynamic K-line cache build. High-frequency runs should keep this budget short enough to return a degraded report instead of blocking the paper execution cycle; deeper refreshes can explicitly raise the timeout or run as lower-frequency research jobs. Timeout handling must terminate the current-signal process group and record the timeout in the runner report.

V2.51 makes downstream runner hints dynamic-pool aware. `validation_sample_auditor.py` must not suggest a plain static-symbol runner after capacity or sample-gap review; any suggested validation runner command should include `--dynamic-scan-pool --dynamic-max-symbols 36` unless the user is explicitly debugging an offline fixture. This keeps manual resume actions aligned with the same market-atmosphere and sentiment-adjusted pool used by the hourly automation.

V2.52 makes Phase 1 readiness distinguish a broken Binance market-data path from a minor non-critical symbol/source degradation. If the fast loop reviewed open positions, verified K-line freshness, scanned strategies, produced deduplicated candidates, and kept paper/live safety flags false, a single candidate-symbol 24h timeout may be recorded as `minor_symbol_degradation_only` while still proving the public ticker/order-book/depth route is operational. Broader market errors, stale K-lines, missing candidates, or incomplete execution quotes remain partial or blocking evidence.

V2.53 makes the dynamic scan pool the default runner path. Unless `--disable-dynamic-scan-pool` or `--offline-fixture` is supplied, `validation_progress_runner.py` must rebuild the scan universe from Binance market breadth, 1h/4h anchor atmosphere, liquidity, participation, open symbols, recovery queue and fresh social/key-person sentiment. The old `--dynamic-scan-pool` flag remains accepted for compatibility, but it is no longer required for live market paper runs. Runner reports must continue to show market regime, sentiment overlay, width policy, bucket quotas and top dynamic candidates so operators can see why the pool changed.

V2.54 separates the Phase 2 proof gate from the forward-sample recovery gate. Phase 2 still requires the full portfolio-level paper proof: enough closed trades, win rate above the configured target, net return above the configured target, controlled drawdown, and calibration evidence. But `validation_sample_auditor.py` may classify an entry mode or strategy family as `eligible_small_paper_only` when it has at least 3 resolved paper trades, meets the win-rate gate, and has realized net return of at least +2% even if it has not reached the full +5% promotion return gate. This keeps the system collecting controlled forward samples from positive early evidence without claiming the strategy is proven or live-ready. Weak symbols, retired modes, negative-return groups, liquidity gates, current-signal gates, capacity gates and paper-only safety still apply.

V2.55 makes market mood and sentiment an explicit scan-pool contract, not just a ranking preference. Every live-market runner pass must expose the dynamic pool state machine in report and JSON output: market regime, market atmosphere, short-term anchor state, sentiment state, width policy, bucket quota policy, selected symbols and top dynamic candidates. Risk-off or fading anchors contract speculative symbols; risk-on or accelerating anchors widen high-beta/momentum coverage; selective rotation prioritizes volume acceleration, recovery-queue visibility and confirmed narratives; fresh positive catalysts may reserve social-catalyst watch slots; fresh risk-alert clusters contract high-beta/meme slots. Stale or missing social handoff data falls back to `neutral_or_missing_social` and must not expand the pool. This only changes paper scan coverage and candidate ordering; all current-signal, K-line freshness, liquidity, recovery, capacity, learning and paper-only safety gates remain in force.

V2.56 aligns fast-scan data budgets with the default dynamic pool. When risk-on or positive-catalyst conditions expand the effective universe above the configured baseline, the fast scanner needs enough market-fetch and information-fetch budget to complete Binance primary data plus cross-source checks. The default fast market budget is raised from 90s to 150s and info budget from 60s to 90s. If those budgets are still exhausted, the report must keep `data_status=degraded`, show which items were skipped, and avoid treating incomplete cross-source data as fully verified.

V2.57 adds the mood/sentiment adaptive scan-pool contract. The runner should treat static configured symbols as a baseline/fallback only; every live-market pass must expose how market atmosphere and fresh sentiment changed the effective scan universe before child scanners run. Risk-off or fading anchors contract speculative coverage, risk-on or accelerating anchors expand high-beta/liquid momentum/new-mover coverage, selective rotation prioritizes volume acceleration and recovery-queue visibility, and fresh social catalyst or risk-alert clusters adjust bucket quotas. Missing or stale social data must fall back to `neutral_or_missing_social`; missing Binance breadth must be marked as `fallback_static`. This still affects only paper scan coverage and ranking, not live trading permission or entry-gate thresholds.

V2.58 aligns the validation sample auditor with the Phase 2 objective. `validation_sample_auditor.py` now uses `min_closed_paper_trades=30` as the lower bound for positive-expectancy proof, matching the user's 30-50 closed paper trade requirement and the phase readiness auditor. The system can continue collecting samples below 30, but reports must treat fewer than 30 closed trades as insufficient Phase 2 evidence even if some smaller subgroup is temporarily profitable.

V2.72 adds `recovery_watchlist_paper_sampler.py` after `recovery_watchlist_monitor.py` in the hourly evidence chain. The watchlist monitor remains conditional and read-only; the sampler is the only bridge that may write one tiny recovery scout into the main paper ledger. It must require a fresh watchlist, `paper_scout_allowed_if_trigger_confirms`, confirmed trigger price, spread/depth/microstructure gates, no duplicate open symbol, open-position capacity, paper cash, and paper-only safety flags. If any gate fails, it writes blocked decisions to report/experiment and leaves the ledger unchanged. The hourly automation should run the sampler before dashboard, HTML, and push assets so the operator can see both watchlist readiness and actual paper action/blocked reasons in the main board.

V2.82 adds `binance_market_data_health_auditor.py` as the explicit public Binance data-layer health check. Each scheduled pass should run it before paper entry scanning and surface its result in Markdown, HTML and WeChat outputs. The audit verifies public spot ticker, bookTicker, depth, aggTrades and 1m/5m/15m/1h/4h Klines for core symbols, then records spread, 1% depth, recent taker-buy ratio, endpoint coverage and safety flags. If this audit is `blocked`, the active loop should degrade to report-only and avoid new paper entries until public market data recovers. The audit is read-only and must never call private/account/order/withdraw/margin/futures/perpetual endpoints.

V2.83 adds `paper_signal_contract_auditor.py` as the explicit signal-layer completeness check. It reads the latest recovery watchlist, blocked-candidate retest, sampler decisions and open paper positions, then verifies that executable paper/watch signals include symbol, direction, entry trigger, stop loss, take profit, time window, confidence/probability and failure conditions. Incomplete executable contracts must be shown in Markdown, HTML and WeChat outputs and should not be treated as actionable paper entries until repaired. The audit is read-only and does not mutate the ledger or strategy config.

V2.84 makes Phase 1 readiness consume the explicit audit evidence introduced in V2.81-V2.83. `phase_goal_readiness_auditor.py` must read the latest `*binance-market-data-health.json`, `*paper-signal-contract-audit.json`, and `*paper-ledger-integrity-audit.json`, then include them as formal Phase 1 checks. A fully closed Phase 1 loop now requires public Binance endpoint health, complete signal contracts, clean ledger integrity, paper-only flags, dynamic scan evidence, paper buy/sell lifecycle, exit samples and reports. The main Markdown dashboard must show `phase1_gap_count` plus top Phase 1 gaps so operators can see whether the loop is actually blocked by data, signal, ledger, execution, or reporting evidence.

V2.85 makes Phase 2 readiness stricter and less vulnerable to lucky outliers. `phase_goal_readiness_auditor.py` must derive closed-trade quality from the paper ledger: evidence span in days, positive strategy-family count, positive entry-mode count, explicit market-regime coverage, largest winning trade share of total positive PnL, symbol diversity and realized PnL. Phase 2 cannot pass merely because total PnL improves; it also needs 30+ closed trades, positive net return after realistic costs, 55%+ win rate, controlled drawdown, at least two positive strategy families/entry modes, explicit regime evidence and no single winning trade dominating the proof. The Markdown dashboard should show enough Phase 2 blockers for operators to see these quality gaps without opening the raw experiment.

V2.86 makes future paper trades carry market context at entry. Shared fast/daily/sunday entries must stamp top-level `market_regime`, `market_atmosphere`, `short_term_state`, `sentiment_state`, plus `market_context_at_entry` from the current dynamic scan pool. Minimal sampler paths that do not yet receive a full dynamic pool must still stamp an explicit unknown marker, such as `unknown_or_not_attached`, so the Phase 2 auditor can distinguish missing context from real regime coverage. `phase_goal_readiness_auditor.py` must ignore unknown/manual markers when counting `explicit_regime_count`.

V2.87 separates explicit data-layer health from stale child-run artifacts. Phase 1's public Binance ticker/order-book/depth check may be proven by a clean `binance_market_data_health_auditor.py` result even if the latest fast-loop artifact is older, skipped or degraded for a non-current reason. The dedicated health audit remains read-only and must show no blocked endpoints, no live/private API use and no ledger mutation.

V2.88 adds market-context self-test coverage to resume preflight. `resume_preflight.py` must run `paper_market_context_auditor.py --self-test`, expose `market_context_self_test_status` in compact output and the main dashboard, and block readiness if that self-test fails. This protects Phase 2 regime-diversity evidence from silently breaking after code changes.

V2.89 adds a sample-growth action board to the human-facing dashboards. When the runner scans live market data but records `no_new_entry`, Markdown, HTML and WeChat outputs must translate the block reasons into next paper-only evidence steps: closed-trade gap, capital policy, max deployable paper cash, per-trade notional, top blocked symbols, current block reason and required retest/trigger/microstructure condition. This board is visibility only; it must not weaken recovery gates, mutate strategy config, open paper trades, or authorize live orders.

V2.90 hardens `top_blocked_retest_quality_scout_sampler.py` network failure recovery. Binance public ticker/depth DNS, timeout or fetch failures must become structured blocked decisions such as `binance_public_fetch_failed:ticker_24hr` rather than uncaught tracebacks. The sampler must still write report/experiment output, keep `ledger_mutated=false`, and preserve `live_orders_enabled=false` / `private_api_used=false`. Network failure can block a paper scout, but it must not make the hourly automation disappear without a readable reason.

V2.91 adds a `Quality Scout Trigger Watch` view to the operator dashboards. When the top-blocked retest lab finds candidates that pass research retest but the quality-scout sampler blocks them because current price, spread, depth or trigger confirmation is not ready, Markdown, HTML and WeChat outputs must show symbol, current price, trigger/breakout level, distance to trigger, spread, depth, OOS win rate/net return and top block reasons. This is a waiting-condition panel only; it must not create an order, mutate the ledger, or loosen sampler gates.

V2.113 adds `phase2_quality_gate_enforcement_auditor.py` as a read-only enforcement check between the Phase 2 quality recovery board and the latest runner evidence. It compares blocked entry modes and strategy families against the latest `validation_recovery_plan` retired/cooldown sets, then checks `no_entry_summary.top_blocked_candidates` for current `validation_recovery_plan_block` hits. The dashboard must show checked, enforced, currently blocking and missing counts so operators can see whether poor Phase 2 modes are actually gated instead of only documented. This audit must not mutate the paper ledger, strategy config, automation state, or live accounts.

V2.114 makes `binance_kline_cache_builder.py` persist an experiment summary by default at `experiments/YYYYMMDD-HHMMSS-binance-kline-cache-builder.json`. The summary records selected symbols, intervals, cache dir, file count, failures, fallback hosts and safety flags, while still writing K-line files to the requested cache directory. This turns refreshed public K-line caches into auditable data-layer evidence for Phase readiness and dashboards; it remains public market-data only and must not call account/order/withdraw/margin/futures/perpetual endpoints.

V2.115 makes Phase readiness choose latest experiment evidence by filesystem modification time instead of lexicographic filename order. This prevents UTC/local timestamp naming differences, such as `101737` versus `1634`, from causing the dashboard to cite stale Binance K-line cache evidence after a fresher builder run. The change only affects evidence selection and must not mutate the paper ledger, strategy config, or trading permissions.

V2.116 makes Binance K-line cache evidence visible across operator surfaces. The Markdown status board, local HTML dashboard and WeChat push assets must show the latest `binance-kline-cache-builder` run ID, selected symbol count, intervals, file count, failure count, fallback count and paper-only safety flags. This is display-only evidence for data-layer freshness; it must not fetch new data, mutate the paper ledger, change strategy config or authorize live trading.

V2.117 adds `pipeline_freshness_repair_runner.py` as the automatic repair bridge for stale downstream evidence. After recovery watchlist/retest lab and their samplers run, the hourly automation should call this runner before signal-contract, capital, phase and dashboard steps. It reads the latest freshness audit state, reruns only stale or missing downstream paper-only artifacts with explicit latest source paths, then writes a repair report and final freshness state. It may cause a downstream sampler to open at most the same tiny paper scout that the sampler itself would have opened, but it cannot bypass trigger, liquidity, capacity, recovery or safety gates; it must keep `live_orders_enabled=false`, `private_api_used=false`, and it must not call private APIs or authorize live trading.

V2.121 makes Top Blocked Retest source selection candidate-aware. `top_blocked_candidate_retest_lab.py` must not blindly consume the newest validation runner when that runner is only a maintenance, learning-sync, skip-fast or other no-candidate pass. By default it scans recent runner artifacts and selects the latest paper-safe runner with non-empty `no_entry_summary.top_blocked_candidates`, then records `source_selection.status`, selected artifact, candidate count and latest-any runner. `pipeline_freshness_auditor.py` must use the same candidate-bearing runner as the expected source for the top-blocked retest lab, while still using the latest runner for dynamic market context surfaces. This keeps the paper-only learning/retest loop from being erased by a later housekeeping runner; it does not open paper positions, change strategy config, loosen sampler gates or authorize live trading.

V2.118 makes pipeline freshness repair visible across operator surfaces. The local HTML dashboard, WeChat summary/card and compact visual JSON should show the latest repair status, initial/final freshness, child-run count, ledger mutation flag and paper-only safety flags. This is display-only visibility for the repair bridge; it must not run fresh market scans by itself, mutate strategy config, loosen sampler gates or authorize live trading.

V2.122 makes signal-contract auditing actionability-aware. `paper_signal_contract_auditor.py` should only mark a missing direction/entry/stop/target/window/confidence/failure-condition set as an incomplete executable contract when the row is actually actionable or intended for paper execution. Recovery watchlist rows that are explicitly `watch`, `no_deploy`, `blocked` or `risk_alert`, and are already blocked by missing/stale/disputed data or a concrete block reason, should be reported as `blocked_non_executable` diagnostics. They remain visible to the operator and samplers, but they must not downgrade Phase 1 executable-contract readiness. This is reporting hygiene only; samplers still block missing price, missing trigger, invalid stop/take, thin depth and all live-order safety violations.

V2.123 preserves microstructure evidence in Recovery Watchlist sampler decisions. `recovery_watchlist_paper_sampler.py` should copy `book_depth_min_usd_20`, `recent_taker_buy_quote_ratio`, `order_book_imbalance_20`, `quote_volume_24h_usd` and `data_quality_status` from each watchlist item into its blocked/opened decision rows, and render depth/buy-ratio/imbalance in the Markdown report. This makes ZEC/STO-style blocked candidates auditable without re-opening the watchlist JSON. It is visibility only and must not change paper entry gates, ledger mutation rules, live-order flags or strategy promotion.

V2.73 adds recovery sampler self-test coverage to resume preflight. `recovery_watchlist_paper_sampler.py --self-test` must use temporary files only and verify an actionable fixture opens exactly one tiny paper scout, duplicate symbol reruns are blocked, and unsafe ledger safety flags block mutation. `resume_preflight.py` must run this self-test unless `--skip-self-test` is supplied and expose `recovery_sampler_self_test_status` in compact output. A failed sampler self-test is a safety block before hourly automation resume.

V2.74 adds top-blocked-candidate visibility. Runner JSON already emits `no_entry_summary.top_blocked_candidates`; dashboards and push assets must surface this list with symbol, interval, family, entry mode, OOS metrics and primary block reason. This is paper-only diagnosis for strategy iteration: it explains why apparently strong scanned candidates such as high-beta movers were blocked by recovery/learning gates, and must not be treated as an order, watchlist approval, or live-trading permission.

V2.47 adds stale shared-lock recovery. Before starting, the runner checks `/private/tmp/active_alpha_sunday_crypto_realistic_paper.lock`. A fresh lock still makes the runner skip ledger-mutating children when its PID is alive. A lock older than 55 minutes, or a lock whose PID no longer exists, is treated as stale, removed, and recorded in `shared_paper_lock_startup` plus `stale_lock_cleanup` so the paper loop can recover after an interrupted run. This only removes a local lock file; it does not bypass ledger safety, live-order checks, capacity gates, or paper-only constraints.

V2.48 adds budgeted degradation for the shared fast/daily market and information-source phases. The hourly automation should pass or inherit explicit budgets for market fetch, K-line fetch and info fetch. If a budget is exhausted, child scanners must stop additional slow source calls, mark the affected symbols/sources as degraded, continue writing report/experiment/handoff output, and avoid opening a new paper position from incomplete data. Dynamic scan pools should still be rebuilt from whatever fresh market atmosphere is available, but stale or missing social/news context must fall back to `neutral_or_missing_social`.

V2.49 adds a top-level `No New Entry Diagnosis` panel to runner reports and compact JSON. When a scan produces candidates but opens no new paper position, the runner must summarize whether the block came from validation recovery, weak intervals, cooldown entry modes, quality floors, duplicate open symbols, recent-loss penalties, or lack of a current entry trigger. This keeps the loop from looking idle when it is actually rejecting weak setups, and it makes each hourly report useful for strategy iteration without digging through child JSON.

V2.23 adds profit-protection visibility to paused-capacity and normal validation reports. The open paper review calendar must show whether each open position has armed trailing profit protection, the highest unrealized PnL seen so far, the active trailing floor, and the distance from current PnL to that floor. This is reporting/evidence only; it does not loosen exit rules or authorize live trading.

V2.24 adds a separate current-signal quality scout path. If a refreshed `current_signal_probe.py` finds a top signal with no promotion blockers and strict train/OOS quality, `current_signal_near_miss_sampler.py` may open one minimum-size paper sample with `paper_entry_mode=current_signal_quality_scout_probe`. This avoids mixing strong top current signals with the retired `current_signal_validation_probe` mode. The scout may retest a weak interval, but it must not bypass weak symbols, retired entry modes, retired strategy families, liquidity, cash, open-position, or live-trading safety gates.

V2.25 wires that quality scout path into the normal runner. When the pre-audit capacity gate pauses ordinary new sampling, `validation_progress_runner.py` still defaults to a `current_signal_quality_scout_sampler` child that invokes `current_signal_near_miss_sampler.py --quality-scout-only`. This quality-only child skips the legacy near-miss queue and the legacy validation probe path; it can only produce minimum-size `current_signal_quality_scout_probe` paper entries, and duplicate sample keys must produce `sample_already_exists` instead of a second paper position. The runner report and compact JSON must expose `auto_quality_scout_sampler_enabled` plus the child row so this behavior is auditable.

V2.26 adds a pause/resume ledger guard. If there are open paper positions and the paper ledger is stale beyond the configured threshold, or if any open paper position has already passed `expires_at`, the runner must skip new sampling for that pass and prioritize exit review. This makes a resumed hourly automation start by refreshing open-position evidence instead of opening fresh paper risk from stale state. The report and compact JSON must show `pause_resume_guard.triggered`, the ledger age, expired/near-expiry counts, and the reason. This is paper-only risk hygiene and does not authorize live trading.

V2.27 changes the monthly target accounting from fixed seed doubling to monthly compounding. Runner reports must consume `validation_sample_auditor.py`'s `monthly_target` object and show `target_model=monthly_compounding_double`, the current Asia/Shanghai `month_id`, the `baseline_source`, `month_start_equity_usd`, `lifetime_initial_capital_usd`, `target_equity_usd`, `progress_pct`, and required return. This keeps the goal aligned with the user's requirement: a month that starts at $1000 should target $2000, not the original $1000 target from a $500 seed.

V2.28 persists the monthly compounding baseline in the shared paper ledger. Any runner child that writes the paper ledger through the shared loop helpers must stamp a missing current-month `monthly_goal_baselines[month_id]` record before saving, and must never overwrite an existing month record. The validation report should therefore prefer `baseline_source=ledger_monthly_goal_baselines`; event inference is now only a backward-compatible fallback for older ledgers.

V2.29 adds offline self-test coverage for that baseline persistence. Before resuming a paused hourly loop after code changes, `sunday_crypto_realistic_paper_loop.py --self-test` should pass and include `monthly_baseline_persistence`. This test uses temporary ledgers only; it is not a market scan and does not mutate the real paper ledger.

V2.30 adds `resume_preflight.py` as the read-only check before the runner is resumed after a pause. Preflight can say `exit_review_first` when open positions are stale or near expiry, but it does not run exit review, scan markets, mutate the ledger, or change automation state. Once the user explicitly resumes, the validation runner still owns the real review/scan/audit cycle.

V2.31 adds `resume_preflight.py --self-test`. This must pass before treating preflight as reliable after code changes. It uses temporary files to verify `ready_but_paused`, `exit_review_first`, and `not_ready`; it does not read market data or mutate the real paper ledger.

V2.32 makes preflight validate the hourly automation contract. The automation must be a local cron with `FREQ=HOURLY;INTERVAL=1`, must point at the investing workspace, and its prompt must preserve paper-only/read-only/live-order-ban/API-key-ban language. A contract failure is a safety block before the validation runner can be resumed.

V2.33 adds `safety_invariant_auditor.py` to the preflight chain. The auditor is offline and scans active-alpha files plus the hourly automation for live-order true flags, Binance private order/account/withdrawal endpoints, signing/private API markers, and known API key literals. A blocked auditor result blocks runner resume before any market scan.

V2.92 tightens Phase 2 evidence visibility. `phase_goal_readiness_auditor.py` must consume the latest `paper-market-context-audit` artifact, include market-context coverage fields in the `Market regime evidence recorded` blocker, and add a `next_best_actions` item that explicitly says future paper trades need `market_context_at_entry` before they count toward Phase 2 regime proof. The same action list should also remind the loop to collect independent quality-scout samples across different strategy families and entry modes, so the closed-trade gap is not filled by one repeated weak pattern. This remains read-only reporting; it must not mutate the ledger, loosen sampling gates, or authorize live orders.

V2.93 makes market-context stamping a paper ledger integrity invariant for open positions. `paper_ledger_integrity_auditor.py` should warn when an open paper position lacks `market_regime`, `market_atmosphere`, `short_term_state`, `sentiment_state`, or `market_context_at_entry`; its self-test must prove that a complete open position passes and a missing `market_context_at_entry` open position warns. This protects future Phase 2 samples from silently losing regime evidence, while still leaving old closed trades to the separate read-only market-context audit.

V2.94 preserves dynamic market context through the top-blocked quality-scout bridge. `validation_progress_runner.py` must attach the current dynamic scan `market_regime`, `market_atmosphere`, `short_term_state`, `sentiment_state`, pool policies and a `market_context` object to each `no_entry_summary.top_blocked_candidates` row. `top_blocked_candidate_retest_lab.py` must preserve that context in each retest result, and `top_blocked_retest_quality_scout_sampler.py` must write it into `market_context_at_entry` if a future tiny paper scout is opened. Self-tests should prove context is preserved. This is evidence plumbing only; it must not loosen recovery, trigger, liquidity, capacity or live-order gates.

V2.95 preserves dynamic market context through the current-signal near-miss / quality-scout sampler. `validation_progress_runner.py` must pass the current dynamic scan `market_regime`, `market_atmosphere`, `short_term_state` and `sentiment_state` into `current_signal_near_miss_sampler.py`; the sampler must use those values as fallback context when current-signal candidates do not already carry their own `market_context`. Candidate-provided context must win over runner fallback context. This only improves Phase 2 regime evidence quality; it must not change candidate floors, recovery gates, capacity gates, order sizing, ledger safety flags, or live-trading permissions.

V2.96 preserves dynamic market context through the Recovery Watchlist bridge. `recovery_watchlist_monitor.py` must read the latest validation runner dynamic scan pool, attach `market_regime`, `market_atmosphere`, `short_term_state`, `sentiment_state`, pool policy and selected-symbol sample to each conditional recovery watchlist item, and record the source runner artifact. `recovery_watchlist_paper_sampler.py` must prefer the item-level `market_context` when opening a future tiny `recovery_watchlist_paper_scout`, writing it into `market_context_at_entry`. Self-tests should prove this context survives from watchlist item to open paper position. This is evidence plumbing only; it must not loosen trigger, liquidity, microstructure, cash, capacity, duplicate-symbol, ledger-safety or live-order gates.

V2.97 makes Recovery Watchlist degradation evidence explicit. When the latest validation runner dynamic scan pool falls back to static symbols because Binance breadth, ticker, social or other upstream evidence is degraded, `recovery_watchlist_monitor.py` must preserve `dynamic_scan_status`, `dynamic_scan_reason`, `ticker_meta_status`, `social_handoff_status`, `social_handoff_age_hours` and `data_layer_degraded` in `market_context`. These degradation fields may follow a future paper scout into `market_context_at_entry`, but unknown/degraded market-regime fields must not be treated as Phase 2 regime coverage. This improves operator visibility without creating orders, loosening gates or claiming positive-expectancy proof.

V2.98 makes Recovery Watchlist monitor reliability part of resume preflight. `resume_preflight.py` must run `recovery_watchlist_monitor.py --self-test` before declaring the hourly paper loop locally ready, and it must block readiness if the monitor self-test fails or if the child JSON payload reports a non-`ok/pass` status even with a zero process exit code. This protects Binance public host fallback, structured fetch-failure evidence and dynamic-scan degradation context before recovery watchlist candidates can feed future paper scouts.

V2.99 makes Binance data-layer health multi-host aware. `binance_market_data_health_auditor.py` should try multiple Binance public spot hosts for ticker, bookTicker, depth, aggTrades and 1m/5m/15m/1h/4h Klines, then record `base_urls`, endpoint source host, attempt count and fallback failure count in the experiment and dashboards. This strengthens Phase 1 data-layer evidence without calling private endpoints, mutating the ledger, opening paper positions, loosening entry gates or authorizing live trading.

V2.100 wires Binance data-layer health into resume preflight. `resume_preflight.py` must run `binance_market_data_health_auditor.py --self-test`, expose `binance_market_data_health_self_test_status` in compact output and the main dashboard, and block readiness when that self-test fails or reports a non-`ok/pass` JSON status. This makes data-layer reliability part of the one-entry resume safety gate without fetching live market data in preflight, mutating the ledger, or changing paper entry rules.

V2.101 makes the validation runner dynamic scan pool use the same Binance public multi-host posture as the health auditor. `validation_progress_runner.py` should try `api.binance.com`, `data-api.binance.vision`, `api1.binance.com`, `api2.binance.com`, and `api3.binance.com` for public ticker/Kline calls, and record the selected `base_url`, attempt count and fallback failure count in `ticker_meta` and anchor metadata. A single public host timeout should no longer force the scan pool into `fallback_static` when another public Binance host is healthy. This only improves data-source resilience and dynamic symbol discovery; it must not mutate the ledger, open paper trades by itself, loosen recovery gates or authorize live orders.

V2.102 extends the same Binance public spot multi-host fallback into the shared fast/daily/sunday paper loop. `sunday_crypto_realistic_paper_loop.py` should route spot ticker, bookTicker, depth, Kline and all-24h breadth requests through a shared read-only `binance_spot_get()` helper with the five public spot hosts. `fast_crypto_paper_auto_trader.py` inherits this behavior because it wraps the shared loop. Futures sentiment endpoints may remain separate sentiment/risk references, but spot paper entries must not depend on a single Binance public host. This change only improves data availability and degradation evidence; it must not call private endpoints, mutate strategy config, bypass validation recovery gates, or authorize live orders.

V2.103 makes `binance_kline_cache_builder.py` multi-host aware as well. The cache builder should use the same five Binance public spot hosts for exchangeInfo, 24h ticker discovery and Kline downloads, record fallback events in its JSON summary, and expose `--self-test` coverage for one-host-fails and all-hosts-fail cases. This reduces long hourly delays when the primary public host is unreachable while preserving the rule that Kline cache generation is research-only, public-market-data-only, and cannot open paper or live positions.

V2.104 wires the Kline cache builder self-test into `resume_preflight.py`. Preflight must run `binance_kline_cache_builder.py --self-test`, expose `binance_kline_cache_builder_self_test_status` in compact output and the main dashboard, and block readiness when that self-test fails or reports a non-`ok/pass` JSON status. This protects the data cache path used by runner warmups, shadow scans and current-signal refresh before an hourly resume, without fetching live market data in preflight, mutating the ledger, or loosening any paper/live trading gates.

V2.105 adds `pipeline_freshness_auditor.py` as a read-only operator visibility check. The auditor compares the latest validation runner with downstream Recovery Watchlist, Recovery sampler, Top Blocked Retest Lab and Top Blocked Retest sampler artifacts, then marks stale-source or upstream-stale reports when they still depend on an older runner. The main dashboard must show pipeline freshness status, stale/missing counts and rerun hints. This prevents stale watchlist or sampler panels from being mistaken for current market-state evidence; it does not fetch market data, mutate the ledger, open paper positions, change sizing, or authorize live orders.

V2.106 adds `strategy_proposal_decision_auditor.py` as the missing decision layer between proposed changes and paper-only validation. It reads the strategy iteration backlog, paper ledger and latest attribution evidence, ranks each active proposal by loss-at-risk, affected sample count, persistence and protective value, and writes `reports/STRATEGY_PROPOSAL_DECISION_BOARD.md` plus `experiments/strategy-proposal-decision-board.json`. The main dashboard must show the top proposal decisions, priority score, validation sample target and whether the decision is an entry-filter, exit-rule, retirement/cooldown or capital-posture candidate. This board does not approve changes, mutate config, write the ledger, open paper trades, increase notional or authorize live orders; it only focuses the next paper-only strategy iteration.

V2.107 adds `strategy_proposal_enforcement_auditor.py` as the follow-through check after the decision board. It reads the latest proposal decisions, validation recovery plan, no-entry blockers and paper capital allocation audit, then classifies each decision as enforced by recovery gates, enforced by conservative capital policy, pending paper-only A/B validation, awaiting the next open-position exit-rule test, manual review only, or blocking. The main dashboard must show decision count, enforced count, pending test count, blocking count and top enforcement rows so the operator can see whether strategy lessons are actually reflected in current paper gates. This audit is read-only: it does not mutate strategy config, write the paper ledger, open paper positions, increase notional, bypass recovery/capacity gates or authorize live orders.

V2.108 extends `phase_goal_readiness_auditor.py` to match the four-phase objective instead of stopping at testnet/tiny-live readiness. The audit must now include `phase4_real_auto_trading_candidate`, explicitly blocked until Phase 2 positive expectancy, Phase 3 risk/testnet controls, least-privilege API policy, human approval and rollback/audit controls are proven. It must also output a `layer_status` matrix for Binance data, signal scanning, paper execution, review/attribution, strategy iteration, capital allocation and real-trading-candidate layers. The main dashboard must surface this matrix so operators can distinguish a proven paper engineering loop from an unproven trading edge or live-candidate route. This remains read-only and cannot authorize real orders.

V2.109 corrects the phase mapping to the current user objective: Phase 3 is the `$500` tactical paper monthly-double pressure test, not testnet/tiny-live readiness. `phase_goal_readiness_auditor.py` must evaluate Phase 3 with the monthly compounding target, isolated paper pool, current target equity, closed-trade quality, drawdown/duplicate-symbol risk controls and outlier concentration. Binance Spot testnet/dry-run, least-privilege API policy, kill switch, rollback/audit controls and manual approval now belong to Phase 4 real-auto-trading-candidate readiness. The dashboard must show `phase3_monthly_double_pressure_test` and `phase4_real_auto_trading_candidate` separately so monthly paper pressure evidence cannot be confused with live-route permission.

V2.110 adds `phase3_pressure_action_board.py` as the fixed human action board for the monthly-double pressure test. It reads the paper ledger, latest validation progress, capital allocation audit and phase readiness audit, then writes `reports/PHASE3_PRESSURE_ACTION_BOARD.md` plus `experiments/phase3-pressure-action-board.json` with target gap, required return, sample-quality blockers, current posture and the maximum paper-only scout action currently allowed. The main Markdown/HTML/operator artifact index must surface this board so the user can see whether Phase 3 permits no deploy, one tiny scout, or broader pressure testing. This board is read-only with respect to trading state: it must not mutate the paper ledger, alter strategy config, open positions, increase capital limits, use private APIs or authorize live orders.

V2.111 adds an explicit `objective_coverage` matrix to `phase_goal_readiness_auditor.py`. The matrix maps the user objective to concrete evidence rows: Binance ticker, 1m/5m/15m/1h/4h Klines, order book depth/spread, recent trades, dynamic scan pool, executable signal contract, paper order lifecycle, stop/profit/expiry exits, attribution learning, strategy backlog/enforcement, capital allocation and phase promotion gates. `LATEST_ACTIVE_ALPHA_STATUS.md` must show the top objective coverage gaps so the operator can distinguish engineering coverage from strategy-quality blockers. This is read-only visibility only; it must not mutate the ledger, change strategy config, open paper/live positions, use private APIs or relax Phase 2/3/4 promotion gates.

V2.112 adds `phase2_quality_recovery_action_board.py` as the Phase 2 strategy-quality recovery board. It reads the paper ledger, latest Phase readiness audit, attribution report, strategy backlog and capital allocation audit, then writes `reports/PHASE2_QUALITY_RECOVERY_ACTION_BOARD.md` plus `experiments/phase2-quality-recovery-action-board.json`. The board must show the current win-rate math, the required future wins to recover to 55%/60%, the average PnL needed to break even, cooldown/retire candidates by entry mode and strategy family, and the paper-only recovery actions. The main dashboard must surface this board so the operator can see why larger sizing remains blocked. This remains read-only: no ledger mutation, no config mutation, no paper/live opening, no private API, and no relaxation of recovery/capacity/liquidity gates.

V2.134 defines a tiered durable Kline cache contract. The main cache builder and validation runner default to `1m/5m/15m/1h/4h`, with `1d` retained as optional long-horizon research data. The builder records per-interval target bars, roles, planned/used public request budget, symbol trimming, missing pairs and short-history pairs. A cache with files but incomplete required-interval coverage is not replayable. Targeted recovery prefetch may use `requested_only` coverage, but it must say so explicitly and cannot substitute for the complete operational cache. This remains public-market-data-only and does not open paper or live positions.

V2.137 makes raw Kline durability cryptographically auditable. The builder writes `kline-cache-manifest.json` after all public-data files are complete. Reproducibility and preflight checks require every Kline data file to be tracked, schema-valid, byte-size matched, and SHA-256 matched; a missing, altered, truncated, untracked, or unsafe-path file blocks replay. Old summary artifacts whose `/private/tmp` data has disappeared remain historical summaries only and must never be expanded into synthetic OHLCV.

V2.138 closes the runtime durability loop. Relative walk-forward cache paths are resolved against the active skill root before child execution; shared fast/daily/Sunday and recovery-shadow Kline prefetch refreshes the manifest atomically after adding files; the safety auditor recognizes only an explicit 64-hex `sha256` field as a digest while still blocking identical values in key/secret fields. The reproducibility gate evaluates the newest research artifact for current promotion and keeps older missing-cache artifacts historical-only. A fresh dynamic recovery-shadow scan may prove Phase 1 scan execution when it loads frames and scans strategies without mutating the ledger; it does not count as a paper trade or Phase 2 edge proof.

V2.139 makes current-signal evidence closed-bar and freshness aware. Open bars are removed before evaluation, stale or future frames are excluded, and reports record discovered/fresh/stale frames plus removed open-bar rows. A no-blocker authoritative three-segment candidate may create a `$25` `three_segment_current_signal_retest` paper ticket. When deployment authorization is absent, the sampler may persist a dry-run experiment/report but must leave the ledger unchanged. Small holdout win rates remain historical sample statistics, not calibrated forecast probabilities. Cross-sectional rotation stays research-only until train, validation, and final holdout are all positive after friction across independent windows.

V2.140 adds a mandatory current-signal robustness gate before a three-segment ticket may mutate the paper ledger. The reference signal must still be active on the newest closed bar; at least five nearby current-signal parameter variants are required, at least 30% must remain no-blocker, the final holdout needs at least 20 trades with a Wilson 95% win-rate lower bound of 45%, and train/validation/final holdout must remain positive under 0.46%, 0.8%, and 1.2% round-trip friction. Failure produces a persisted diagnostic dry-run and `current_signal_robustness_gate_block`, not a position.

V2.141 replaces the single-candidate robustness artifact with a batch artifact. The runner audits up to twelve no-blocker current candidates before any current-signal sampler. Sampler matching requires symbol, interval, and the complete strategy parameter dictionary; one failed top candidate cannot globally block or authorize a different candidate. Every candidate keeps its own failed gates and paper retest decision.

V2.142 prevents a fixed-sample deadlock for long-holding paper strategies. The minimum final-holdout trade count becomes `min(20,max(6,floor(holdout_window_bars/median_holding_bars*0.4)))`, while the neighborhood still needs at least five active variants, eight no-blocker variants, and a 15% no-blocker share. The Wilson 95% lower bound remains 45% and all three friction stresses must stay positive. Passing this adaptive gate authorizes only a minimum `$25` forward paper sample; it is not an 80% probability claim or a live promotion.

V2.143 adds a notional-scaled public order-book gate after robustness and before paper mutation. Spread must be at most 15 bps; 1% bid/ask depth must each satisfy `max($2,500,100x notional)`; top-20 ask depth must satisfy `max($200,8x notional)`; 24h quote volume must exceed $1M; and recent taker-buy quote ratio must be at least 0.55. A passing paper fill uses public best ask plus configured slippage. No private endpoint or live order is used.
