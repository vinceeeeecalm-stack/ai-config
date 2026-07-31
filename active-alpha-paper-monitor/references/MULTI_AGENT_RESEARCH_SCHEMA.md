# Multi-Agent Research Schema

本文件定义 active monitor handoff 中 `research_panel` 的结构。字段与 manual skill 对齐，方便 manual 做二次审查和投委会式仲裁。

## Agent Output

```json
{
  "agent_id": "us_equity_alpha_agent",
  "asset_scope": ["APLD", "SOXL"],
  "sources_used": [
    {
      "name": "market_movers_or_news_source",
      "url_or_provider": "provider name",
      "fresh_at": "ISO-8601",
      "status": "ok | fallback | failed",
      "coverage": "price_news_volume"
    }
  ],
  "signals": [
    {
      "asset": "APLD",
      "signal_type": "trend | catalyst | liquidity | risk | base_rate",
      "direction": "bullish | neutral | bearish | mixed",
      "summary": "short reason",
      "time_window": "intraday | 1d-7d | 1w-1m"
    }
  ],
  "confidence_pct": 0,
  "data_quality": "verified | degraded | disputed | stale | missing",
  "missing_data": [],
  "failed_gates": [],
  "recommended_max_action": "watch | paper_only | conditional_action | risk_alert | no_deploy | block | hold | trim_review",
  "what_would_change_my_mind": []
}
```

## Research Panel

```json
{
  "run_id": "YYYYMMDD-run-type-seq",
  "agent_outputs": [],
  "researcher_votes": [],
  "bull_case": "",
  "base_case": "",
  "bear_case": "",
  "disconfirming_evidence": [],
  "prior_thesis_status": "confirmed | weakened | invalidated | insufficient_data",
  "arbiter_decision": "",
  "old_thesis_reuse_allowed": false,
  "missing_data_summary": [],
  "max_allowed_action": "watch",
  "research_committee_degraded": false,
  "research_panel_missing_reason": null,
  "research_method": "external_subagent_outputs_embedded_by_active_monitor | missing",
  "external_roles_used": [],
  "local_fallback_roles": [],
  "missing_external_roles": [],
  "required_external_roles": [],
  "external_agent_validation": {}
}
```

## Degradation Rules

- 少于 6 个相关研究角色成功返回：`research_committee_degraded=true`。
- subagent 不可用：写入 `research_panel_missing_reason`。
- 关键数据 `disputed/stale/missing`：handoff 降级。
- 社交/新闻单源未确认：最多 `watch`。
- active monitor 不允许输出真实下单字段，只能 handoff 给 manual。
- active monitor 的 agent output 和 panel 都不得保留 `execute_now`；若外部 subagent 输出包含 `execute_now`，active 侧必须降级为 `conditional_action`，再交给 manual 复核。
- 外部 subagent 可以返回 `block` 表示资产/策略被否决；active handoff validator 允许该枚举，但 panel 层最大动作会保守落到 `no_deploy`。

## External Subagent Import

`research_subagent_task_package` is an upstream orchestration artifact. It is not an `agent_output` and is not a `research_panel`. A task package may include:

- `run_id`
- `external_output_target`
- `task_files`
- per-role task JSON and prompt markdown paths
- read-only context paths
- post-collection quality-audit commands

The active monitor may reference the package path in config or experiment metadata, but handoff validation remains based on the final `research_panel` or degraded `research_panel_missing_reason`. When a task package is used, collect per-role task outputs with `manual-investment-strategy-operator/scripts/research_subagent_output_collector.py` before passing `--external-agent-outputs-json` to active scripts.

active 脚本可通过 `--external-agent-outputs-json` 接收外部并行 subagent 结果；默认全覆盖模式要求 8 个角色都来自真实 external subagent。不相关角色可以返回 `asset_scope=["not_applicable"]` 并解释原因，但不能静默缺失。只有通过合法角色校验、6+ 成功角色、`evidence_verified_known_role_count>=6`、`roles_with_ok_source_count>=6`、`committee_quality_gate_passed=true`、不是全部 degraded，且没有缺失默认外部角色时才嵌入非降级 `research_panel`，否则写入 `research_panel_missing_reason` 并保持 `research_committee_degraded=true`。

US open、social key-person、daily/fast/Sunday paper loop 和 paper exit monitor 的报告、handoff、experiment 都必须展示或携带 `research_panel` / `research_panel_missing`、`research_committee_degraded`、`max_allowed_action` 和缺失原因。缺少真实外部 subagent 时，输出仍然可以作为 watch/paper 研究材料，但不能升级为真实交易许可；`monitor_recommendation` 不得高于 `max_allowed_action`，更高的预研究等级只能写入 `pre_research_candidate_grade`。

## Conservative Normalization

active monitor 只负责嵌入外部研究，不负责把社交、扫描或 paper 信号升级成真实交易。外部 subagent 若返回 `read_ok`、`partial`、`cautious_bullish`、`watch_or_conditional_only` 等人类可读状态，会由 manual skill 的 `research_panel_runner.py` 保守标准化后再嵌入：

- source 状态只会折叠为 `ok / fallback / failed`。
- 模糊或部分数据质量一律按 `degraded` 处理。
- 非标准方向折叠为 `bullish / neutral / bearish / mixed`。
- 非标准动作折叠为最保守可识别动作，无法识别则为 `watch`。

标准化只修正格式，不能提高动作等级；若 6+ 角色全部 degraded，或 evidence-verified 角色少于 6 个，或少于 6 个角色有至少一个 `ok` source，active handoff 必须继续标记 `research_committee_degraded=true`，最高只能输出 `watch / paper_only / risk_alert / no_deploy`。
