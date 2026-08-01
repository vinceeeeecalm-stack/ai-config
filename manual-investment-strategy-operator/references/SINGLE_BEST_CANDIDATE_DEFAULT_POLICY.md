# Ranked Best Candidate Default Policy

## Purpose

Use this mode when the user asks for the best current US equity or crypto opportunity without requesting a full portfolio report. The filename remains for compatibility. Scan broadly, choose one clear primary recommendation, and expose up to two additional candidates only when they pass the same minimum historical-quality gate.

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
6. Expose exactly one rank-1 primary candidate and up to two qualified alternatives. Do not fill rank 2 or 3 with weak candidates merely to create choice.
7. Run point-in-time historical conditioning for every exposed candidate. The primary receives the complete deep dive; alternatives must still disclose the same-comparison sample size, out-of-sample win-rate interval, conservative EV, expected return, Profit Factor, drawdown, reward/risk and liquidity status.
8. Rank by conservative EV and target-compatible return after friction, with explicit penalties for drawdown, instability, poor liquidity and switching from a still-valid incumbent. Raw win rate alone cannot win the ranking.
9. Preserve the incumbent unless a challenger has a material, reproducible evidence advantage. The same snapshot, config and strategy version must reproduce the same ranking.

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

1. **One-line verdict**: rank-1 symbol, asset class and one of `可以买一小仓 / 等指定价格再买 / 只管理已有仓位 / 目前没有值得交易的标的`.
2. **Ranked choice table**: primary plus zero to two qualified alternatives, with win-rate interval, conservative EV, expected return, drawdown, reward/risk, validity window and one-line rank reason.
3. **What the primary is**: explain the business/protocol in two plain sentences.
4. **Why the primary ranks first now**: three to six decision-driving items labelled `事实 / 推导 / 判断`, each with source time.
5. **Primary entry plan**: current price/time, primary and optional secondary entry, exact trigger, allowed session, start/end and do-not-chase rule.
6. **Primary exit plan**: target 1/action/window, target 2/action/window, price stop, thesis invalidation, time stop and latest exit/review.
7. **Three primary scenarios**: bull/base/bear, weights totaling 100, price range, date window and drivers.
8. **Probability and history method**: point-in-time sample, walk-forward windows, untouched holdout, base rate, interval, adjustments, limitations and simulation/paper/live evidence labels.
9. **Why alternatives rank lower**: for each alternative, identify the exact disadvantage in probability, conservative EV, return space, drawdown, liquidity, catalyst quality or entry location.
10. **Funding**: current settled cash, legal funding source, position size and settlement constraint.
11. **What would change the decision**: maximum three observable conditions, including the challenger threshold required to replace the incumbent.

Before the one-line verdict, the internal candidate must set
`current_direct_decision=enter_now / small_entry_now / do_not_enter_now`.
The user-facing verdict must translate that current decision; a later price or
signal may only describe the next review and cannot be the primary entry
instruction. With zero settled cash, keep the theoretical current decision but
set the real execution amount to zero.

Put committee coverage and data-source failures in a compact appendix. Qualified alternatives are a user decision surface and must stay in the main report, not be hidden in the appendix.

## Qualified Alternative Gate

An alternative may appear only when all of the following hold:

- at least 30 no-lookahead observations with an untouched holdout;
- positive conservative EV after fees, spread and slippage;
- reward/risk at least 2 and Profit Factor above 1;
- current liquidity is verified and the decision window has not expired;
- the historical simulation, forward paper and any real outcome remain separately labelled;
- the report gives at least one material reason it ranks below the primary.

If only the primary passes, output one candidate. If none passes the formal action gate, still name the highest-ranked research candidate and its reopening trigger, but do not label it high-confidence or immediately executable.

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

The gate must confirm one primary candidate, zero to two qualified alternatives, three evidence types, fresh price/time, dated entry and exit, exactly three primary scenarios totaling 100, historical/probability provenance, deterministic ranking context and real funding constraints.
