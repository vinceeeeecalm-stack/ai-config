# Subagent Orchestration Runbook

本 runbook 定义手动报告运行时如何真正调用 subagent，而不是只用本地 fallback 生成降级 `research_panel`。默认策略是 **全覆盖 + 强制 subagent**。

普通手动调度还必须遵守 **限时收口**：全覆盖是研究质量目标，不是无限等待许可。到达配置的等待时间后，主 agent 必须收集已完成输出，缺席角色按 `missing/degraded` 进入质量审计和报告，不得继续拖住本次 DCA 或美股战术判断。深度全量研究只有在用户明确要求时才另开。

普通调度的默认墙钟预算是：20 分钟内给出可读报告，30 分钟强制收口。超过 30 分钟还没有齐全的 subagent 或外部数据时，主 agent 必须生成 `stop_and_report_status`，而不是继续等待。

## 核心原则

- 当前 Codex 会话提供 `multi_agent_v1` 或等价 subagent 工具时，生成美股或 crypto 报告前必须优先并行调用真实 subagent。
- 本地 `scripts/research_panel_runner.py` 只负责校验、合并和降级，不负责冒充真实 subagent。
- 若无法获得至少 6 个成功、合法、非全降级的研究角色，报告必须标记 `research_committee_degraded=true`，且不得输出新的 `execute_now`。
- 主 agent 是 arbiter，只能在 subagent 输出、数据质量、目标函数和风险门之间仲裁，不能直接沿用旧结论。

## 限时收口

| 阶段 | 默认上限 | 行为 |
|---|---:|---|
| 初始等待 | `180s` | 等待并行 subagent 返回；期间主 agent 可做非重叠本地审计 |
| 收敛等待 | `90s` | 对未返回角色发送“只用已有 evidence cache 立即输出”的收敛指令 |
| 总外部等待 | `300s` | 到点后立即收集已有输出，关闭仍未返回角色 |
| 整体普通调度 | `30min` | 到点后强制生成状态报告；只允许另开深度研究继续补证据 |

收口后的报告必须显示：

- 已返回角色、缺席角色和缺席原因。
- 质量审计结果和最大允许动作。
- 哪些结论可用，哪些只能作为 `watch / conditional`。
- 下一次最小补证据任务，而不是继续在本轮无限扩展。

收口规则只影响“本次报告能否结束”，不降低强执行门槛。只要研究委员会未达标，`execute_now` 仍保持关闭。

### Social Intel Non-Blocking Rule

`social_news_agent` 负责 crypto 官方公告、关键人物、新闻和社交热度。它的缺席或超时必须这样处理：

- 标记 `social_news_agent=missing/degraded`。
- 本轮不能输出新的 `execute_now`。
- 本轮仍然可以生成可读的持仓、DCA、长期 thesis 和 watch/conditional 报告。
- 下一轮 evidence backlog 应给出具体社交/官方源补采任务。

原因：社交情报是发现风险和叙事拐点的增强层，不是价格、流动性、链上、宏观和持仓风控的替代品。

部分收口后必须运行只读审计：

```bash
python3 manual-investment-strategy-operator/scripts/timeboxed_committee_closeout_audit.py \
  --task-package-json manual-investment-strategy-operator/experiments/{run_id}-research-subagent-task-package.json \
  --collection-json manual-investment-strategy-operator/experiments/{run_id}-research-subagent-output-collection.json \
  --quality-audit-json manual-investment-strategy-operator/experiments/{run_id}-committee-quality.json \
  --output manual-investment-strategy-operator/experiments/{run_id}-timeboxed-closeout-audit.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-timeboxed-closeout-audit.md
```

该审计通过只表示“本次普通手动调度可以结束并进入学习闭环”；它不表示研究委员会通过，也不允许 `execute_now`。

## 默认角色

全量报告默认调用 8 个角色：

| agent_id | 必须回答的问题 |
|---|---|
| `prior_thesis_challenge_agent` | 上一轮结论有哪些反证？旧 thesis 是否还能沿用？ |
| `portfolio_state_agent` | 当前持仓、现金通道、质押、目标差距、超配/低配是否支持行动？ |
| `macro_regime_agent` | 利率、DXY、VIX、美债、CPI/PCE、风险偏好、资金流如何影响节奏？ |
| `crypto_market_agent` | crypto 价格、1D/7D/30D、成交量、盘口、流动性、funding/OI 是否支持 DCA 或战术？ |
| `onchain_defi_agent` | TVL、费用/收入、稳定币、链上活跃、供应/解锁是否支持长期 thesis？ |
| `social_news_agent` | 官方公告、关键人物、新闻、社交热度是否有确认或风险？ |
| `us_equity_alpha_agent` | 美股短中期候选是否明显优于当前战术仓？是否通过双 80？ |
| `backtest_validation_agent` | 历史样本、walk-forward、paper、base rate 和过拟合风险是否支持晋级？ |

即使用户只问 crypto，也优先保留 8 角色；不相关角色可以返回 `asset_scope=["not_applicable"]`，但仍需说明为什么不影响本次行动。

## Subagent 任务模板

给每个 subagent 的任务必须包含：

```text
你是 {agent_id}。请独立研究，不沿用上一轮结论，不能下真实交易指令。
只返回一个 JSON object，字段必须符合 MULTI_AGENT_RESEARCH_SCHEMA.md:
agent_id, asset_scope, sources_used, signals, confidence_pct, data_quality,
missing_data, failed_gates, recommended_max_action, what_would_change_my_mind.

本次 run_id: {run_id}
本次资产/问题: {asset_scope}
本次可读上下文: {context_paths}

要求：
- sources_used 必须列来源、时间戳、状态和覆盖范围。
- 区分已确认事实、推断、单源传闻和缺失数据。
- 任何社交/新闻单源信号最多 recommended_max_action=watch。
- 若数据不足，主动降级，不要凑 80%。
- 不要输出自然语言报告，只输出 JSON。
```

## 运行顺序

1. 主 agent 先本地读取持仓、配置、最新 handoff 和报告上下文，形成最小上下文文件。
2. 并行调用 8 个 subagent。不要让多个 subagent 写同一个文件；让它们只返回 JSON。普通手动调度按“限时收口”执行，不能无限等待。
3. 主 agent 收集结果，保存为：

```text
/private/tmp/{run_id}-external-subagent-outputs.json
```

4. 先运行质量审计：

```bash
python3 manual-investment-strategy-operator/scripts/research_committee_quality_auditor.py \
  --external-agent-outputs-json /private/tmp/{run_id}-external-subagent-outputs.json \
  --run-id {run_id}-committee-quality \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-committee-quality-audit.md \
  --normalized-output /private/tmp/{run_id}-external-subagent-outputs.normalized.json
```

质量审计必须检查：8 个角色是否齐全、schema 是否合规、至少 6 个角色是否 successful、至少 6 个角色是否 evidence-verified、至少 6 个角色是否有 `status=ok` 来源、是否存在 role-native evidence gaps、material missing data 和 failed gates。审计失败时可以继续生成报告，但报告必须保持 `research_committee_degraded=true`，不得输出新的 `execute_now`。

5. 如果质量审计失败，生成下一轮证据补采队列：

```bash
python3 manual-investment-strategy-operator/scripts/research_evidence_backlog_builder.py \
  --quality-audit-json manual-investment-strategy-operator/experiments/{run_id}-committee-quality.json \
  --output manual-investment-strategy-operator/experiments/{run_id}-research-evidence-backlog.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-evidence-backlog.md
```

该 backlog 只定义下一轮 subagent 应该补的数据、来源类别和 pass condition，不能升级本轮行动。

6. 将 backlog 打包成可分发的 subagent 任务：

```bash
python3 manual-investment-strategy-operator/scripts/research_subagent_task_packager.py \
  --evidence-backlog-json manual-investment-strategy-operator/experiments/{run_id}-research-evidence-backlog.json \
  --context-json /private/tmp/{run_id}-daily-context.json \
  --output manual-investment-strategy-operator/experiments/{run_id}-research-subagent-task-package.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-subagent-task-package.md
```

该任务包会生成每个 role 的 `.task.json` 和 `.prompt.md`，并给出 subagent 输出收集路径与后续质量审计命令。

7. subagent 完成后，先收集每个角色的输出文件：

```bash
python3 manual-investment-strategy-operator/scripts/research_subagent_output_collector.py \
  --task-package-json manual-investment-strategy-operator/experiments/{run_id}-research-subagent-task-package.json \
  --output /private/tmp/{run_id}-external-subagent-outputs.json \
  --manifest-output manual-investment-strategy-operator/experiments/{run_id}-research-subagent-output-collection.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-subagent-output-collection.md
```

该收集器会读取任务包中的 `expected_output_json`，也可以通过 `--agent-output-json` 追加外部汇总文件。它只负责收集、去重、列出缺席角色并预校验 schema；如果角色缺失或证据不足，后续质量审计仍必须保持降级。

8. 重新运行质量审计，并使用收集后的 `/private/tmp/{run_id}-external-subagent-outputs.json` 作为输入。若是部分输出，质量审计必须失败或降级，但本次报告仍应完成，最大动作不高于 `watch / conditional_action`。

9. 运行：

```bash
python3 manual-investment-strategy-operator/scripts/generate_manual_report.py \
  --run-id {run_id} \
  --external-agent-outputs-json /private/tmp/{run_id}-external-subagent-outputs.normalized.json
```

10. `generate_manual_report.py` 会调用 `scripts/research_panel_runner.py` 校验外部 JSON，并把合法输出写入 `research_panel`。
11. 若 subagent 少于 6 个成功返回、schema 失败、evidence-verified role 少于 6 个、ok source 覆盖少于 6 个、全部 degraded，或关键角色缺失，报告必须保留降级状态。

## 仲裁要求

主 agent 在报告中必须显式输出：

- 成功和缺失的研究角色。
- 外部 subagent 质量审计摘要和 repair queue。
- `bull_case / base_case / bear_case`。
- 旧结论是否 confirmed / weakened / invalidated / insufficient_data。
- 所有反证和 missing_data_summary。
- `arbiter_decision` 与 `max_allowed_action`。
- 每条行动的 “what_would_change_my_mind”。

仲裁优先级：

1. 长期 5年/10年 10x 目标。
2. 当前组合状态和现金通道。
3. 数据质量与风险门。
4. 历史验证 / paper / recommendation calibration。
5. 短期价格和消息催化。

## 降级规则

- subagent 工具不可用：`research_committee_degraded=true`。
- 少于 6 个成功角色：`research_committee_degraded=true`。
- 全部角色都是 `degraded`：`research_committee_degraded=true`。
- 任一关键角色输出 `block/no_deploy/risk_alert`：对应资产不得新增。
- 社交/新闻不能单独触发真实交易。
- 美股/crypto 战术 `execute_now` 仍必须通过真实概率 >=80% 和执行准备度 >=80。

## 与本地 fallback 的关系

没有外部 subagent JSON 时，`research_panel_runner.py` 会用本地 evidence role 生成降级 panel。这只用于保持报告结构完整，不能视为完成强制 subagent 流程，也不能允许新的 `execute_now`。
