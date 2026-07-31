# Stage Acceptance And Compounding Policy

This policy separates the current build milestone from the final financial goal.

## Two Different Completion Meanings

| Term | Meaning | Can Be True Now? |
|---|---|---|
| `stage_system_complete` | The manual strategy system can generate audited reports, record recommendations, review paper trades, and keep learning. | Yes |
| `goal_complete` | The real portfolio has achieved the long-term financial target. | No, only future account value can prove it |

The current stage can be accepted when the system is complete enough to run the loop and historical/paper evidence shows at least a 60% learning-floor win rate. This does not mean the 5-year/10-year 10x target is already achieved.

## Stage Acceptance Criteria

The stage can be marked `temporarily_complete` when all of the following are true:

1. The latest formal report has `report_integrity_audit.status = ok`.
2. The latest formal report has `objective_coverage_audit.status = ok`.
3. The system can write structured recommendation records for DCA, tactical watch/conditional actions, holds, and no-add decisions.
4. The paper ledger has at least `10` closed paper trades.
5. The closed paper-trade win rate is at least `60%`.
6. The system exposes a learning loop that records pending recommendations, future review dates, sample gaps, and next evidence tasks.
7. Live trading and automatic portfolio mutation are disabled.

## Compounding Edge Model

The 10x target should be treated as a compounding path:

- 5-year 10x pure compounding requires about `3.91%` average monthly growth or `12.20%` average quarterly growth.
- 10-year 10x pure compounding requires about `1.94%` average monthly growth or `5.93%` average quarterly growth.
- These figures are the minimum compounding floor, not the desired ceiling. Recommendations should prefer opportunities with expected return above the relevant floor, while still protecting against drawdowns large enough to break the compounding path.

Monthly DCA changes the exact account path, but the discipline is the same: each dispatch should look for positive expected-value actions, protect against large drawdowns, record the decision, and use outcomes to improve the next dispatch.

## Promotion Path

| Stage | Evidence Floor | Allowed Meaning |
|---|---|---|
| learning floor | paper/history win rate `>=60%` | The system is worth continuing and can produce watch / paper / conditional decisions |
| calibrated path | more resolved outcomes across different market regimes | The system can refine probability estimates and sizing |
| validated action | true target probability `>=80%` and readiness `>=80` | A candidate may become an `execute_now` candidate, still requiring human confirmation |

The system must not use the 60% learning floor to claim high-confidence live trading. It can only say the current build stage is complete enough to keep iterating.

## Report Requirement

Each status report should state:

- whether `stage_system_complete` is true;
- whether `goal_complete` is false;
- current paper closed sample count and win rate;
- current action ceiling;
- what evidence is needed to move from 60% learning-floor quality toward 80% validated quality.
