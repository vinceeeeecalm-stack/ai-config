# Subagent Orchestration Runbook

本 runbook 定义 active monitor 如何接入真实 subagent 输出。active monitor 仍然只做发现、paper、风险提示和 handoff，不授权真实交易。

## 核心原则

- 当前 Codex 会话提供 `multi_agent_v1` 或等价 subagent 工具时，生成 handoff 前应并行调用真实 subagent。
- active 脚本本身不直接 spawn Codex subagent；外部编排器或主 Codex agent 负责收集 JSON，再通过 `--external-agent-outputs-json` 传入。
- 如果无法传入至少 6 个成功、合法、evidence-verified 的角色，或无法满足至少 6 个 ok source 角色，handoff 必须写入 `research_panel_missing=true`、`research_committee_degraded=true` 和原因。
- active 侧永远不输出真实 `execute_now`；外部 subagent 若返回 `execute_now`，active 会降级成 `conditional_action` 后交给 manual 二次审查。

## 默认角色

全量 handoff 默认覆盖：

- `prior_thesis_challenge_agent`
- `portfolio_state_agent`
- `macro_regime_agent`
- `crypto_market_agent`
- `onchain_defi_agent`
- `social_news_agent`
- `us_equity_alpha_agent`
- `backtest_validation_agent`

crypto-only 或 US-open-only 扫描也尽量保留 8 角色；不相关角色必须解释 `not_applicable`，不能静默缺失。

## Research Subagent Task Package

`research_subagent_task_package` 是外部 subagent 编排的上游 artifact。它告诉外部编排器本轮要运行哪些角色、每个角色要补哪些证据、使用哪些只读上下文，以及最终应把外部 JSON 收集到哪里。

建议保存路径：

```text
/private/tmp/{monitor_run_id}-research-subagent-task-package.json
```

它应由 manual skill 的 `scripts/research_subagent_task_packager.py` 生成，或由等价外部编排器生成。active monitor 只消费最终的 `--external-agent-outputs-json`，不把任务包当成已完成研究，也不把任务包写成交易许可。

subagent 完成后，应先用 manual skill 的 `scripts/research_subagent_output_collector.py` 收集任务包中的 `expected_output_json` 文件，生成 `/private/tmp/{monitor_run_id}-external-subagent-outputs.json` 和 collection manifest。collector 只做收集、去重、缺席角色标记和预校验；如果角色不足或证据不足，active monitor 必须继续降级。

## 外部 JSON 输入

外部 subagent 结果必须是数组，或包含 `agent_outputs` 的对象。每个对象必须符合 `MULTI_AGENT_RESEARCH_SCHEMA.md`。

建议保存路径：

```text
/private/tmp/{monitor_run_id}-external-subagent-outputs.json
```

可传入这些脚本：

```bash
python3 active-alpha-paper-monitor/scripts/us_open_dynamic_scanner.py \
  --external-agent-outputs-json /private/tmp/{monitor_run_id}-external-subagent-outputs.json

python3 active-alpha-paper-monitor/scripts/social_key_person_monitor.py \
  --external-agent-outputs-json /private/tmp/{monitor_run_id}-external-subagent-outputs.json

python3 active-alpha-paper-monitor/scripts/daily_crypto_paper_auto_trader.py \
  --external-agent-outputs-json /private/tmp/{monitor_run_id}-external-subagent-outputs.json

python3 active-alpha-paper-monitor/scripts/fast_crypto_paper_auto_trader.py \
  --external-agent-outputs-json /private/tmp/{monitor_run_id}-external-subagent-outputs.json

python3 active-alpha-paper-monitor/scripts/sunday_crypto_realistic_paper_loop.py \
  --external-agent-outputs-json /private/tmp/{monitor_run_id}-external-subagent-outputs.json

python3 active-alpha-paper-monitor/scripts/paper_position_exit_monitor.py \
  --external-agent-outputs-json /private/tmp/{monitor_run_id}-external-subagent-outputs.json
```

## Subagent 输出要求

每个 subagent 只返回 JSON，不写自然语言报告。字段：

- `agent_id`
- `asset_scope`
- `sources_used`
- `signals`
- `confidence_pct`
- `data_quality`
- `missing_data`
- `failed_gates`
- `recommended_max_action`
- `what_would_change_my_mind`

社交、funding、trending、单源新闻只影响 watch/paper priority，不能替代数据质量、流动性和历史验证。

`recommended_max_action` 允许 `block`，用于表达该角色否决某资产或策略；active handoff 最终会把任何 block/no_deploy 汇总为 `no_deploy`，不作为真实交易许可。

## Handoff 行为

当外部 JSON 通过校验：

- 写入 `research_panel`。
- 设置 `research_panel_missing=false`。
- 设置 `research_committee_degraded=false`。
- 保留 active monitor 限制：最高只是 handoff 给 manual。

当外部 JSON 缺失或失败：

- 写入 `research_panel_missing=true`。
- 写入 `research_panel_missing_reason`。
- 设置 `research_committee_degraded=true`。
- `max_allowed_action` 最高为 `watch / paper_only / risk_alert / no_deploy`。
- 若有质量审计输出，下一轮应先生成/消费 Research Evidence Backlog，修复 role-native evidence gaps 后再尝试解除 degraded。

## 禁止事项

- 不把本地脚本生成的观点伪装成 subagent 输出。
- 不把社交热度或单源消息写成真实概率。
- 不输出真实下单、撤单、提现、margin、futures、perpetual。
- 不把 active handoff 当作 manual 报告的最终动作。
