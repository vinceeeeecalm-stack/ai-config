# Passive Dispatch Runtime Policy

## Purpose

This policy prevents the manual investment skill from drifting into an always-on trading bot or an endless research loop.

The manual skill is a user-triggered decision support tool. Each run may actively fetch market data, sentiment, macro, on-chain, social/news, portfolio, paper-trading, and recommendation-history evidence, but it only does so inside the current user-triggered dispatch.

## Plain-Language Boundary

- `被动调度`：用户问一次，系统跑一次完整取数、分析、报告和复盘队列。
- `主动取数`：在这一次手动调度里尽量全面拿数据，不代表后台自动盯盘。
- `自动交易`：系统自己下真实单、转账或调整持仓。本 skill 永远不做这件事。
- `学习闭环`：记录判断、到期复盘、归因错误、提出下一轮改进建议。它不等于保证收益。

## Runtime Rules

1. Ordinary manual dispatches must be finite.
   - Generate a report.
   - Write recommendation records or mark degradation.
   - Run report audits.
   - Run learning review calendar and next-dispatch readiness.
   - Stop and report status when no due-now review remains.
   - Target ordinary run time is 20 minutes; 30 minutes is the hard wall-clock stop unless the user explicitly asks for a separate deep-research run.

2. Deep research must not block the manual report.
   - If subagents, APIs, social intel, or market-data sources are slow or incomplete, mark the relevant panel as `degraded`.
   - Continue with a readable report.
   - Keep `execute_now_allowed=false` unless all positive readiness gates pass.
   - `social_news_agent` is useful for official/key-person intelligence, but a timeout or missing social result only blocks `execute_now`; it must not block a readable DCA or portfolio report.

3. Long-running simulation is a separate active-monitor concern.
   - Manual dispatch may read active-alpha handoffs and paper results.
   - Manual dispatch may request or package next research tasks.
   - Manual dispatch should not run long loops such as broad paper-trading sweeps unless the user explicitly asks for that separate workflow.

4. A stop is not completion.
   - `stop_and_report_status` means the current turn has reached a useful checkpoint.
   - It does not mean the 5-year/10-year 10x goal has been achieved.
   - It does mean the next useful progress depends on a new market refresh, a due paper/recommendation review, or user-provided evidence.

## When To Continue In The Same Turn

Continue only if at least one is true:

- The user explicitly asks for a fresh report, fresh market scan, or deep research now.
- A paper or recommendation review is due now and can be closed without waiting for future market time.
- A required mechanism is broken and can be fixed locally in a bounded edit/test cycle.
- A report/audit just failed because of a concrete bug that can be repaired immediately.

## When To Stop And Report

Stop the current turn and report status when all are true:

- The manual mechanism can run.
- The latest readiness check says `ready_for_next_manual_dispatch=true`.
- There is no paper/recommendation review due now.
- `execute_now` is still blocked by evidence, sample-size, data-quality, or human-confirmation gates.
- Further progress would only mean waiting for market time, accumulating more samples, or running open-ended research.

Also stop and report status when the ordinary-dispatch hard stop is reached. The report must say what finished, what timed out, which panels are degraded, and what exact evidence task should be run next.

## Required Report Note

When the current turn stops for this reason, the user-facing summary must say:

- The system is passive/on-demand, not automatic trading.
- Which artifacts were generated.
- Why `execute_now` is or is not available.
- What the next useful trigger is: fresh manual report, due review, new screenshot/ledger evidence, or explicit deep research request.
