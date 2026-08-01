# Objective Coverage Audit

`scripts/objective_coverage_audit.py` 审计单份报告是否覆盖目标；`scripts/goal_system_completion_audit.py` 审计整个系统是否已经足以证明长期目标完成。两者不能混用：报告通过不等于 5年/10年 10x 财务目标已经达成。

本文件定义“目标覆盖审计”。它用于确认每次报告是否真正覆盖用户原始目标，而不仅是格式完整。

## 审计对象

每次完整手动报告必须证明以下约束已经进入报告与账本：

- 当前持仓已经绑定到 5年/10年 10x 目标测算。
- 默认 `$1,000/月` crypto DCA 已进入 `goal_path_projection` 与 `asset_goal_contribution_panel`。
- Crypto DCA 给出最多两档行动，并把金额、入场、复盘日和失效条件写入 recommendation history。
- 战术资金池按月度 ROI `100%` 进攻目标追踪，只包含动态可部署战术仓和同通道已确认闲置资金，不把 `CRCL` 等长期保护仓或长期 DCA 本金算入。
- 美股战术行动必须动态识别资金来源，不 hardcode 某个标的。
- Research Committee 至少 6 个研究角色；若降级，必须阻止新的 `execute_now`。
- 推荐历史和概率校准必须阻止未校准的 `execute_now`。
- Crypto 与美股现金通道必须分离显示，跨通道资金不得默认可用。
- active-alpha paper ledger 必须作为策略证据输入；closed 样本不足或收益证据不足时，只能继续阻止新的 `execute_now`。
- recommendation outcome review / probability calibration 未形成样本时，必须保留 warning，不能把目标收益写成已验证能力。
- 每次报告必须生成 `recommendation_outcome_review_drafts`，列出 pending、due/reviewable、draft review 与 upcoming review 数量；默认不自动改账本，需人工确认后再写入 outcome review。
- 每次报告必须生成 `strategy_promotion_evidence_panel`，用 paper 样本、胜率、净收益、回撤和 recommendation calibration 决定短线策略最大动作；证据不足时必须阻止新的 `execute_now`，并在 `report_readiness.failed_gates` 写入 `strategy_promotion_evidence_gate_failed`。

## 脚本

使用：

```bash
python3 manual-investment-strategy-operator/scripts/objective_coverage_audit.py \
  --report-md REPORT.md \
  --context-json CONTEXT.json \
  --run-id RUN_ID \
  --paper-ledger-json active-alpha-paper-monitor/paper_trades/paper_portfolio_ledger.json \
  --expected-monthly-dca 1000 \
  --format json
```

`generate_manual_report.py` 默认会在 report integrity audit 后自动运行本审计，并把结果追加到报告底部。

## 解释

审计通过不代表收益目标一定达成；它只代表报告已经把目标、数据、风险门、执行计划、paper evidence 和复盘账本连起来。若审计失败，报告不能作为目标导向操作报告使用。
