# Goal Evidence Closure Policy

## Purpose

本 policy 定义：当系统完成度审计显示 `ready_for_manual_reports=true` 但 `ready_for_tactical_execute_now=false` 或 `goal_complete=false` 时，必须把阻断项打包成一份可执行的证据闭环清单。

这一步不是投资建议，不改变持仓，也不授权真实交易。它只回答：为了让下一次手动报告更接近“可验证、可复盘、目标导向”的状态，还需要补齐哪些证据。

## Required Closure Areas

每次系统审计后，至少检查以下缺口：

- 持仓成本与 lot：lcETH、ADA、SOL 等是否有完整成本、成交时间、费用和来源文件。
- 质押/赎回条款：lcETH 的换算比例、赎回数量、解锁/unstake 时间和费用。
- 现金通道：crypto rail 与 US equity rail 是否分别有已确认现金；美股必须有券商 settled cash / buying power。
- 推荐校准：是否有足够 `hit / failed` 结果来校准真实预测概率。
- paper validation：短线策略是否有足够已关闭模拟交易、净收益、回撤和 walk-forward 证据。
- Research Committee：是否有足够外部 subagent 角色、来源和反证。

## Required Output

`scripts/goal_evidence_closure_packager.py` 必须输出：

- `closure_items`: 每个阻断项的优先级、影响、需要的证据、接受的数据来源和下一步命令。
- `readiness_summary`: 哪些能力已就绪，哪些仍只能 `paper_only / conditional_action / watch`。
- `user_action_checklist`: 用户需要导出的 broker/exchange/wallet 文件或截图字段。
- `automation_commands`: 只读复核命令；不得包含真实下单、转账、删除或自动修改账本命令。
- `plain_term_notes`: 对成本 lot、broker-verified cash、paper validation、calibration 等词做中文注释。

`scripts/goal_evidence_import_validator.py` 必须在任何人工导入前运行，检查用户填写的 CSV 模板是否满足字段、数值、时间戳和权限范围要求。它只输出验证结果和可关闭的候选缺口，不修改账本。

`scripts/goal_evidence_import_preview.py` 必须在验证通过后、任何人工导入前运行。它把有效 CSV 转换为 proposed ledger updates，例如拟追加的 crypto 成本 lot、拟补充的 lcETH 质押/赎回字段、拟更新的美股 cash rail。它仍然只读：

- 不修改 `portfolio_ledger.json`。
- 不写 recommendation history。
- 不关闭审计缺口。
- 不授权真实交易或转账。
- 只输出 `ready_for_human_review / empty_preview / blocked_invalid_validation`。

## Decision Rules

- 没有 broker-verified 美股现金，不得升级美股战术 sizing。
- 没有足够 recommendation outcome 校准，不得宣称真实 80% 预测概率。
- 没有足够 paper/walk-forward 样本，不得把短线策略升级为真实执行候选。
- 没有完整成本 lot，不得输出完整成本收益或税务级换仓判断。
- 证据闭环包只能降低不确定性，不能单独触发买入或卖出。
- 用户填写的 CSV 模板必须先通过 `goal_evidence_import_validator.py`；验证通过也只代表“可进入人工导入复核”，不代表自动关闭缺口。
- 验证通过后的 `goal_evidence_import_preview.py` 只代表“可以看到拟导入内容”，不代表已经导入、已经修复成本、或可以升级 `execute_now`。

## Plain Notes

- `cost lot`：一笔买入/卖出的成交记录，包括数量、价格、时间和手续费。
- `broker-verified cash`：券商账户里带时间戳的 settled cash / buying power，不是口述现金。
- `paper validation`：用模拟交易验证策略，不动真钱。
- `calibration`：把历史预测和实际结果对齐，检查所谓“80%概率”是否真的接近 80%。
- `import preview`：导入预览，只告诉你人工确认后可能补哪些账本字段，不自动写账本。
