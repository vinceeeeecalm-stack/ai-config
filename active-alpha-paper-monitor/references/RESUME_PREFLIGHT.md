# Resume Preflight

V2.135 requires preflight to run the strict goal-completion auditor self-test.
If `scripts/phase_goal_readiness_auditor.py --self-test` fails, preflight safety
must block restoration or paper sampling. Passing this self-test proves only
that completion classification works; it does not make stale market evidence
fresh, rebuild Kline files, restore an automation, or authorize live trading.

V2.136 also requires `scripts/paper_testnet_risk_control_auditor.py --self-test`.
Failure blocks preflight because every paper-entry path must preserve daily-loss,
drawdown, losing-streak, duplicate-ID, exposure, and rollback controls. Passing
the self-test does not override missing automation, stale data, Phase 2/3 gates,
or human approval.

`resume_preflight.py` is the read-only check to run before resuming a paused active-alpha hourly paper loop.

It does not fetch market data, mutate the paper ledger, open or close paper trades, enable live orders, or call private trading APIs.

## Checks

- Automation status for `active-alpha-hourly-crypto-paper-loop`.
- Automation contract: cron hourly RRULE, local workspace cwd, paper-only/read-only prompt, and explicit bans on live orders, private trading APIs, withdrawal, margin/futures/perpetuals, and API key exposure.
- Safety invariant audit: local scan for live-order true flags, private Binance endpoints, signing/private API markers, and known API key literal leaks.
- Paper ledger readability and `live_orders_enabled=false`.
- Open paper position count, stale ledger age, expired positions, and near-expiry positions.
- Monthly compounding target state, including whether the current `monthly_goal_baselines[month_id]` record is explicit or will be persisted on the next ledger save.
- `sunday_crypto_realistic_paper_loop.py --self-test`.
- `recovery_watchlist_monitor.py --self-test` to protect Binance public host fallback, degradation context, and recovery watchlist data-source diagnostics before the monitor can feed future paper scouts.
- `paper_market_context_auditor.py --self-test` to ensure market-regime context coverage can be audited before Phase 2 evidence collection resumes.
- `binance_market_data_health_auditor.py --self-test` to ensure Binance public multi-host fallback, endpoint failure recording, and compact health payload behavior remain reliable before hourly resume.
- `binance_kline_cache_builder.py --self-test` to ensure the Kline cache builder's public multi-host fallback and all-host failure handling remain reliable before hourly resume.
- `weekly_goal_strategy_lab.py --self-test` to verify next-bar execution, conservative intrabar ordering, realistic friction, fractional capital use, timestamp-aligned benchmarks, and three-segment selection.
- `kline_research_reproducibility_auditor.py --self-test` to block research whose physical raw Kline cache or execution contract cannot be reproduced.
- `current_signal_probe.py --self-test` to ensure final holdout data is not used for strategy selection and legacy two-segment candidates cannot become paper entries.
- `strategy_recovery_optimizer.py --self-test` to ensure nonreproducible walk-forward evidence and legacy validation contracts cannot enter the recovery queue.
- `pipeline_freshness_auditor.py --self-test` to ensure downstream artifact synchronization cannot be mistaken for fresh market readiness when the source runner is stale.
- `automation_recovery_planner.py --self-test` to ensure missing, duplicate, unsafe, and safe-singleton automation states produce planning-only outcomes without mutating the scheduler.
- `paper_testnet_risk_control_auditor.py --self-test` to verify daily-loss and drawdown kill switches, losing-streak size reduction/pause, duplicate-ID blocking, and paper-only rollback evidence.
- `validation_sample_auditor.py --format json`.

## Decision States

- `not_ready`: safety errors exist. Do not resume.
- `exit_review_first`: safety checks pass, but open positions are stale, expired, or near expiry. Run paper exit review before any new scan.
- `ready_but_paused`: local checks pass and the automation is paused. User confirmation is still required before setting automation active or running the hourly loop.
- `ready_check_automation_state`: local checks pass, but automation state is not paused or is unknown. Review before changing anything.

## Common Command

```bash
python3 active-alpha-paper-monitor/scripts/resume_preflight.py --compact-output
```

Use `--no-write` when you want stdout only. The default writes a readable report and experiment:

```text
active-alpha-paper-monitor/reports/YYYY-MM-DD-resume-preflight-HHMM.md
active-alpha-paper-monitor/experiments/YYYYMMDD-HHMMSS-resume-preflight.json
```

Offline self-test:

```bash
python3 active-alpha-paper-monitor/scripts/resume_preflight.py --self-test
```

The self-test uses temporary files only and verifies the three main decisions:
`ready_but_paused`, `exit_review_first`, and `not_ready`. V2.32 also verifies that an unsafe automation contract blocks readiness.

## Safety

The preflight never overrides the pause state. A `ready_*` decision means the local state is coherent; it does not mean the automation should be resumed without explicit user confirmation.

If `automation_contract.status=blocked`, do not resume even if the ledger is otherwise healthy. Repair the automation prompt/schedule/workspace first.

If `safety_invariant_auditor.status=blocked`, do not resume. Inspect the finding file/line, remove the unsafe code/config/logged secret, and rerun preflight.

V2.35: the safety invariant auditor performs line-level matching and applies a per-file read timeout. A timed-out file is reported as `file_read_error` warning rather than blocking forever. True live-order flags, private Binance endpoints, signing markers, and API key literals still block readiness.

V2.88: preflight also runs `paper_market_context_auditor.py --self-test`. A failed market-context self-test blocks readiness because future Phase 2 evidence depends on reliably distinguishing real market-regime coverage from unknown or unstamped legacy paper trades.

V2.98: preflight also runs `recovery_watchlist_monitor.py --self-test`. A failed monitor self-test blocks readiness because the recovery watchlist is a source of future paper scout candidates and must preserve Binance public host fallback behavior, structured fetch failures, and dynamic-scan degradation context. Preflight treats child JSON payload statuses other than `ok/pass` as failed even when the child process exits with code 0.

V2.100: preflight also runs `binance_market_data_health_auditor.py --self-test`. A failed Binance data-layer health self-test blocks readiness because Phase 1 data evidence and future hourly scans depend on reliable public spot host fallback, endpoint source tracking, fallback failure counts, and read-only safety flags.

V2.104: preflight also runs `binance_kline_cache_builder.py --self-test`. A failed Kline cache builder self-test blocks readiness because the hourly runner, recovery shadow scan and current-signal refresh depend on reliable public Kline cache generation. This self-test is local/mocked, does not fetch live market data, does not mutate the paper ledger, and does not change paper entry rules.

V2.130: preflight also runs the weekly strategy lab, Kline research reproducibility, current-signal three-segment validation, and strategy recovery optimizer self-tests. Any failure blocks readiness. This prevents missing raw cache, final-holdout model selection, all-in backtest compounding, or legacy two-segment evidence from feeding a future paper entry after resume.

V2.131: preflight also runs `pipeline_freshness_auditor.py --self-test`. Artifact references may be perfectly synchronized while the originating market runner is stale; this self-test verifies those are reported separately and stale runner evidence remains non-actionable.

V2.133: preflight also runs `automation_recovery_planner.py --self-test` and records the current recovery-plan status. The planner never creates or edits an automation. Missing automation may only produce a user-approval-required proposal for one initially paused paper-only entry; actual scheduler changes must use the Codex automation tool after explicit approval.

V2.134: the Kline builder self-test also verifies the required `1m/5m/15m/1h/4h` operational contract, tiered request plan, and degradation for missing or short replay windows. Physical files alone are insufficient: preflight must continue to block current action when required interval coverage is incomplete, stale, or not reproducible.

V2.137: every durable Kline cache must include `kline-cache-manifest.json`. The manifest records each raw Kline file's relative path, byte size, SHA-256, row count, time window, and schema status. Missing manifests, hash/size mismatches, malformed OHLCV rows, unsafe paths, missing files, or untracked Kline files make the cache non-replayable. Historical experiment summaries and vanished `/private/tmp` paths cannot be reconstructed into synthetic evidence.

V2.138: scanners that append public Kline files must refresh the same manifest before the cache can remain replayable. Preflight accepts the newest reproducible walk-forward artifact while preserving older missing-cache artifacts as historical-only. Cache path resolution must be independent of child working directory. SHA-256 digest lines are permitted by safety scanning only under the explicit `sha256` key; the same literal under any credential-like assignment remains blocked.

V2.139: a persisted dry-run current-signal retest is evidence only. Preflight must not treat it as an open paper position, ledger mutation, automation authorization, or Phase 2 edge proof. Current-signal artifacts must expose closed-bar/freshness diagnostics; stale frames are non-actionable. Research-only cross-sectional proposals remain pending and cannot be auto-applied to the paper overlay.

V2.140: a no-blocker three-segment candidate is still non-actionable when its robustness audit is missing, stale, points to a different symbol/interval/source probe, shows the reference trigger has expired, lacks parameter-neighborhood support, has a wide small-sample Wilson interval, or fails friction stress. Preflight and capital allocation must treat such a candidate as dry-run only.

V2.141: robustness authorization is candidate-specific. A batch artifact must match symbol, interval, and complete strategy parameters; no global pass may authorize an unmatched candidate, and a failed first-ranked candidate must not erase a later independently passing candidate. Missing exact matches remain blocked.

V2.142: the adaptive holdout minimum exists only to make long-holding forward paper sampling feasible. It cannot reduce the absolute floor below six trades, bypass the neighborhood/Wilson/friction gates, increase the `$25` minimum scout size, or count as calibrated probability/live evidence.

V2.143: robustness alone cannot authorize a paper entry. The candidate must also pass a fresh public Binance spread, depth, 24h volume, and recent taker-flow check scaled to the proposed notional. Missing or failed microstructure data remains report-only.
