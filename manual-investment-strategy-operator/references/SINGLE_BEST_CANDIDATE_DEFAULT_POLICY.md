# Single Best Candidate Default Policy

## Purpose

Use this mode when the user asks for the best current US equity or crypto opportunity without requesting a full portfolio report. Scan broadly, decide narrowly, and return one decision-ready candidate.

## Trigger And Scope

Trigger on requests equivalent to:

- 现在最值得买什么？
- 美股和 crypto 里推荐一个。
- 找最近表现好、未来一段时间还有空间的标的。
- 给我明确的入场、出场和概率。

Do not trigger when the user explicitly requests a complete portfolio report, multiple sectors, a watchlist, weekly/monthly review, or a single named-asset deep dive.

## Internal Selection

1. Refresh current price, timestamp, spread/liquidity, 1D/5D/20D/60D path and volume.
2. Scan both US equities and crypto unless the user restricts the asset class.
3. Verify near-term information catalysts from primary sources where possible.
4. Check macro/sector or BTC/ETH regime, financing/dilution or unlock/regulatory risk, and portfolio/funding fit.
5. Rank candidates on catalyst quality, trend persistence, entry quality, liquidity, downside asymmetry and evidence quality. Ranking score is not a probability.
6. Expose exactly one primary candidate. Keep at most two runners-up in one sentence explaining why they lost.

Recent strong performance is a discovery signal, not an automatic buy signal. Reject an extended candidate when the remaining upside no longer compensates for the stop or event gap.

## Probability Provenance Gate

Keep these probability concepts separate:

| Type | Meaning | Required support |
|---|---|---|
| `historical_path` | Target reached before stop within the stated horizon | Reproducible rules, dates, sample size and target/stop counts |
| `event_estimate` | Probability that a defined event outcome occurs | Consensus/guide comparison, event analogs and explicit adjustments |
| `scenario_weight` | Bull/base/bear planning weight | Must be labelled judgment, even when evidence-informed |

Rules:

- Do not transform subagent confidence, execution readiness, social heat or ranking score into a price probability.
- Show the base rate first, then every upward/downward adjustment. Cap any qualitative adjustment at 10 percentage points unless a documented calibrated model says otherwise.
- With fewer than 20 reasonably independent samples, do not publish a precise single-point success probability. Use a range at least 15 percentage points wide and label it low-confidence.
- If no reproducible sample exists, set measured probability to `unavailable` and provide only bull/base/bear judgment ranges.
- Bull/base/bear weights must total 100 and state the price path, date window and observable drivers for each case.
- Never imply that scenario weights guarantee returns.

## User-Facing Output

Keep the main report in this order:

1. **One-line verdict**: symbol, asset class and one of `可以买一小仓 / 等指定价格再买 / 只管理已有仓位 / 目前没有值得交易的标的`.
2. **What it is**: explain the business/protocol in two plain sentences.
3. **Why this one now**: three to six decision-driving items labelled `事实 / 推导 / 判断`, each with source time.
4. **Entry plan**: current price/time, primary and optional secondary entry, exact trigger, allowed session, start/end and do-not-chase rule.
5. **Exit plan**: target 1/action/window, target 2/action/window, price stop, thesis invalidation, time stop and latest exit/review.
6. **Three scenarios**: bull/base/bear, weights totaling 100, price range, date window and drivers.
7. **Probability method**: sample, base rate, adjustments, limitations and whether the result is measured or judgment-only.
8. **Funding**: current settled cash, legal funding source, position size and settlement constraint.
9. **What would change the decision**: maximum three observable conditions.

Before the one-line verdict, the internal candidate must set
`current_direct_decision=enter_now / small_entry_now / do_not_enter_now`.
The user-facing verdict must translate that current decision; a later price or
signal may only describe the next review and cannot be the primary entry
instruction. With zero settled cash, keep the theoretical current decision but
set the real execution amount to zero.

Put committee coverage, data-source failures and runners-up in a compact appendix. Do not make the user reconstruct the conclusion from the appendix.

## Action Semantics

- `可以买一小仓`: real cash exists, entry trigger is active, reward/risk and downside gate pass, and the size is derived from loss tolerance.
- `等指定价格再买`: the candidate is best-in-universe but the price/confirmation trigger is not active.
- `只管理已有仓位`: the best opportunity is already owned and adding would worsen risk.
- `目前没有值得交易的标的`: no candidate has positive enough asymmetry. Still state the exact reopening trigger.

For a reproducible 60%-79% target-achievement estimate, a small-probe plan may be shown when reward/risk is at least 2, target-first is above stop-first, no hard capital-risk block exists, and fresh settled cash is confirmed. Call it “可以小仓试，不是高胜率重仓”. Do not use research confidence or evidence quality as the 60%-79% number, and do not hide the plan behind `NO_DEPLOY` jargon.

Cash equal to zero does not erase the candidate analysis. It sets deployable amount to zero and requires a new settled-cash source before execution. Never invent buying power or an asset sale.

## Validation

Before publishing, serialize the candidate card and run:

```bash
python3 scripts/historical_cycle_event_conditioning_gate.py --input candidate.json
python3 scripts/single_candidate_report_gate.py --input candidate.json
```

The gate must confirm one candidate, three evidence types, fresh price/time, dated entry and exit, exactly three scenarios totaling 100, probability provenance and real funding constraints.
