# Research Evidence Backlog Policy

This policy defines how a failed Research Committee quality audit becomes an actionable evidence-repair queue for the next manual report or active monitor handoff.

## Purpose

The Research Committee quality audit separates two different problems:

- `evidence_quality`: whether a research role used fresh, role-native, reliable sources.
- `action_readiness`: whether the portfolio can safely act after evidence is gathered.

When evidence quality fails, the system must not simply rerun the same report. It must produce a targeted evidence backlog that tells the next subagent run exactly which data to collect.

## Required Tool

Use:

```bash
python3 manual-investment-strategy-operator/scripts/research_evidence_backlog_builder.py \
  --quality-audit-json manual-investment-strategy-operator/experiments/{quality_audit}.json \
  --output manual-investment-strategy-operator/experiments/{run_id}-research-evidence-backlog.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-evidence-backlog.md
```

This tool is read-only. It does not fetch market data, change strategy weights, or authorize trades.

## Blocker Classification

After the backlog is generated, classify the remaining blockers before spawning
another research loop:

```bash
python3 manual-investment-strategy-operator/scripts/evidence_blocker_classifier.py \
  --backlog-json manual-investment-strategy-operator/experiments/{run_id}-research-evidence-backlog.json \
  --quality-json manual-investment-strategy-operator/experiments/{quality_audit}.json \
  --output-json manual-investment-strategy-operator/experiments/{run_id}-evidence-blocker-classification.json \
  --output-md manual-investment-strategy-operator/reports/{run_id}-evidence-blocker-classification.md
```

The classifier separates blockers into:

- public data that can be retried once inside the next timebox;
- paid/API entitlement gaps;
- user account, broker, wallet, or staking-export evidence gaps;
- time-gated learning outcomes such as paper trades and recommendation reviews;
- safety or scope caps that are not solvable by more data.

If most remaining blockers are paid/API, account-level, time-gated, or safety
scope items, the next action must be `stop_and_report_status` or a normal fresh
manual dispatch, not another open-ended evidence repair loop.

## Subagent Task Package

After generating the backlog, package it for real subagent dispatch:

```bash
python3 manual-investment-strategy-operator/scripts/research_subagent_task_packager.py \
  --evidence-backlog-json manual-investment-strategy-operator/experiments/{run_id}-research-evidence-backlog.json \
  --context-json /private/tmp/{run_id}-daily-context.json \
  --output manual-investment-strategy-operator/experiments/{run_id}-research-subagent-task-package.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-subagent-task-package.md
```

The task package must create:

- one task JSON per evidence-incomplete role;
- one prompt markdown per evidence-incomplete role;
- a shared external output target for collected subagent JSON;
- post-collection commands for quality audit and research panel generation;
- explicit live-order disabled and manual-arbitration-required fields.

The package still does not spawn agents by itself. It is an execution-ready set of instructions for the external subagent orchestrator.

## Subagent Output Collection

After real subagents write their per-role JSON files, collect them before running the quality audit:

```bash
python3 manual-investment-strategy-operator/scripts/research_subagent_output_collector.py \
  --task-package-json manual-investment-strategy-operator/experiments/{run_id}-research-subagent-task-package.json \
  --output /private/tmp/{run_id}-external-subagent-outputs.json \
  --manifest-output manual-investment-strategy-operator/experiments/{run_id}-research-subagent-output-collection.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-subagent-output-collection.md
```

The collector reads each task package `expected_output_json`, accepts optional explicit `--agent-output-json` files, deduplicates by `agent_id`, and validates the combined payload against the Research Committee runner. Missing roles, duplicate overwrites, stale or invalid files must remain visible in the collection manifest.

The collector does not fetch data, spawn agents, repair weak evidence, or authorize trades. If collection is incomplete, the next quality audit must remain degraded and the report must not output a new `execute_now`.

## Backlog Requirements

The backlog must include:

- current evidence-verified role count and target role count;
- one repair task per evidence-incomplete role;
- role-specific source categories and required fields;
- pass condition for each role;
- action blockers that must remain visible even after evidence repair;
- a subagent prompt template for the next run.

## Default Role Repairs

| Role | Minimum Repair Focus |
|---|---|
| `macro_regime_agent` | rate expectations, CPI/PCE, Treasury curve, DXY/VIX, risk appetite, crypto fund flows |
| `crypto_market_agent` | multi-source price, 1D/7D/30D trend, depth/spread/slippage, funding/OI, NIGHT float/unlock/liquidity |
| `onchain_defi_agent` | DeFiLlama TVL/fees, active users/developer proxy, staking APY/lock, supply/unlock, lcETH terms |
| `social_news_agent` | official/key-person sources, timestamps, identity verification, price/volume confirmation |
| `us_equity_alpha_agent` | dynamic market movers, intraday liquidity/spread proxy, catalyst validation, double-80 inputs |

## Action Limits

Evidence repair can remove `research_committee_degraded` only if the next quality audit passes. It does not by itself allow `execute_now`.

After evidence repair, `execute_now` still requires:

- strategy promotion evidence;
- true forecast probability `>=80%`;
- execution readiness `>=80`;
- no blocking missing data;
- live orders disabled and human confirmation required.

## Report Usage

If the quality audit fails, the manual report should include either:

- the latest evidence backlog summary; or
- a statement that no evidence backlog was generated and the run remains degraded.

The backlog is a research operating plan, not an investment recommendation.
The blocker classification is a runtime-control plan: it explains why some gaps
cannot be solved by repeated public web searches and what should trigger the
next useful run.
