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

The live discovery phase fetches screener sources concurrently, selects a
bounded liquid universe from quote-level fields, and obtains its 1D/5D return
vector through small bounded public batches. A failed batch degrades only its
symbols; successful batches remain eligible, and a time-bounded chart fallback
may repair missing vectors before the fifteen-second deadline. Discovery owns
shorter source timeouts than validation: screener and batch-vector calls must
leave explicit headroom inside the fifteen-second budget, and missing-vector
fallback is limited to one bounded concurrent wave rather than waiting for the
whole universe. It must not fetch a one-year
chart, benchmark path, risk-free rate, account state, Research Committee, SEC
filing, earnings expectation, or news for every discovered symbol. Only the
probability-free Top3 may receive those validation/deep-research requests.

## Handoff Requirements

The scanner first emits a probability-free `DiscoveryCandidateV1` Top3. Only
those three names receive path-quality and news validation; full probability,
filing, earnings-expectation and downside underwriting belongs to Manual Top1.

Each Top 1-3 candidate must include:

- `candidate_symbol`
- `entry_zone`
- `target_price`
- `target_time_window`
- `stop_loss`
- `latest_exit_date`
- `discovery_score`
- `setup_quality_score`
- `reward_risk_ratio`
- `why_better_than_current_tactical_position`
- `candidate_cash_relay_priority`
- `data_quality_status`
- `monitor_recommendation`
- `risk_adjusted_path` with base-versus-adjusted ranking audit

The scanner may output `watch`, `paper_only`, or `small_probe_review`; it must not output live order instructions.

Even when the committee, SEC/IR, earnings expectation, derivatives, historical
analog or current-session evidence is missing, a validated Top1 must finish a
`top1_decision_card` with explicit blockers and the fixed decision-first
fields. Missing evidence may force `do_not_enter_now`; it must not leave the
card absent or pending. A closed market must be labeled
`closed_or_stale_last_session` with the actual price cutoff.

## Safety Rules

- No options, 0DTE, short selling, margin, futures, inverse ETFs, or live orders.
- Unverified news can only produce `watch`.
- If the candidate cannot clearly beat the current tactical position, keep it as watch.
- The scanner never labels a candidate with a target-achievement probability;
  missing Manual probability evidence caps the later action without changing discovery rank.
- Scanner scores are not forecast probability, and no universal 80% gate is used.
- One failed screener source preserves candidates from other successful sources;
  only total core-source failure is `degraded_no_actionable_trade`.
- `intraday_scalp` uses quotes no older than 60 seconds, a five-minute default
  validity window and a mandatory same-session exit. Leveraged ETFs require a
  confirmed underlying signal.
- Sharpe is a bounded secondary path-quality factor. `Sharpe > 3` is not an
  entry gate and cannot override catalyst, value, downside or Research
  Committee requirements.
