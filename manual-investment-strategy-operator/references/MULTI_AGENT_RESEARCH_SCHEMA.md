# Multi-Agent Research Schema

本文件定义 subagent 输出和 handoff `research_panel` 的结构。所有字段用于审计当时为什么这样判断，禁止事后无记录地改写结论。

## Agent Output

```json
{
  "agent_id": "crypto_market_agent",
  "asset_scope": ["SOLUSDT", "ADAUSDT"],
  "sources_used": [
    {
      "name": "binance_spot_24h",
      "url_or_provider": "Binance",
      "fresh_at": "ISO-8601",
      "status": "ok | fallback | failed",
      "coverage": "price_volume_order_book"
    }
  ],
  "source_refs": [
    {
      "source_ref": "src_binance_spot_24h",
      "name": "binance_spot_24h",
      "url_or_provider": "Binance",
      "fresh_at": "ISO-8601",
      "status": "ok | fallback | failed",
      "coverage": "price_volume_order_book"
    }
  ],
  "evidence_items": [
    {
      "field_id": "SOLUSDT.price",
      "value": 0,
      "unit": "USDT",
      "as_of": "ISO-8601",
      "source_ref": "src_binance_spot_24h",
      "verification_status": "verified | fallback | missing | disputed | stale",
      "missing_reason": ""
    }
  ],
  "signals": [
    {
      "asset": "SOL",
      "signal_type": "trend | liquidity | catalyst | risk | valuation | staking | macro",
      "direction": "bullish | neutral | bearish | mixed",
      "summary": "short reason",
      "time_window": "1d | 7d | 30d | 5y-10y"
    }
  ],
  "plain_language_notes": [
    "funding 指合约多空双方定期支付的费用，这里只用来观察情绪，不支持合约交易。"
  ],
  "confidence_pct": 0,
  "data_quality": "verified | degraded | disputed | stale | missing",
  "evidence_quality": "runtime-derived: verified | degraded_no_ok_source | degraded_source_failures | disputed | stale | missing | invalid",
  "action_readiness": "runtime-derived: ready_for_role_max_action | watch_or_paper_ready | not_ready_missing_data_or_failed_gates | blocked_by_data_quality | blocked_by_role_decision",
  "missing_data": [],
  "failed_gates": [],
  "recommended_max_action": "watch | paper_only | conditional_action | risk_alert | no_deploy | block | hold | trim_review | execute_now",
  "origin": "external_subagent | local_fallback",
  "what_would_change_my_mind": []
}
```

`origin` 由 `scripts/research_panel_runner.py` 在导入或本地 fallback 时注入；外部 subagent 可以提供该字段，但不是外部 JSON 的必填输入。运行时 panel 必须保留 `origin`，用于区分真实 subagent 与本地降级证据角色。

`data_quality` 是该角色对“决策输入完整性”的自评；它可以是 `degraded`，因为角色发现了真实阻断项，例如缺成本 lot、broker cash、fund flow 或 paper 样本。运行时会另外派生 `evidence_quality` 和 `action_readiness`：

- `evidence_quality=verified` 表示该角色 schema 完整、至少一个 source 为 `ok`、且没有 source 失败压过 ok source。它证明“研究员可靠地看到了证据”，不证明可以交易。
- `action_readiness` 表示该角色结论是否能支持动作。若有 material `missing_data` 或 `failed_gates`，它必须是 `not_ready_missing_data_or_failed_gates`，即使 `evidence_quality=verified`。
- 因此，Research Committee 可以是非降级的，同时最终 `max_allowed_action` 仍是 `watch/paper_only/conditional_action`。

## Research Panel

```json
{
  "run_id": "YYYYMMDD-report-seq",
  "agent_outputs": [],
  "researcher_votes": [
    {
      "agent_id": "crypto_market_agent",
      "asset": "SOL",
      "vote": "increase | hold | reduce | watch | block",
      "max_action": "conditional_action",
      "reason": "short reason"
    }
  ],
  "bull_case": "best supported upside case",
  "base_case": "most likely case",
  "bear_case": "main downside case",
  "disconfirming_evidence": [],
  "prior_thesis_status": "confirmed | weakened | invalidated | insufficient_data",
  "arbiter_decision": "final synthesis",
  "old_thesis_reuse_allowed": false,
  "missing_data_summary": [],
  "max_allowed_action": "watch",
  "research_committee_degraded": false,
  "research_panel_missing_reason": null,
  "research_method": "external_subagent_outputs | local_degraded_runner",
  "external_roles_used": [],
  "local_fallback_roles": [],
  "missing_external_roles": [],
  "required_external_roles": [],
  "external_agent_validation": {}
}
```

## Action Semantics

- `execute_now`: 只允许 manual skill 在完整数据、风险门、双 80 gate 和人工确认草案通过后输出。
- `conditional_action`: 条件触发后可考虑行动，但必须列出价格、时间、失效条件和复盘点。
- `paper_only`: 只做模拟，不使用真实资金。
- `watch`: 只观察，不新增风险。
- `risk_alert/no_deploy/block`: 暂停新增或退出研究，等待数据修复或 thesis 重新建立；任一关键角色给出 `block/no_deploy` 时，主 agent 对应资产不得新增。

## Degradation

- subagent 少于 6 个相关角色成功返回：`research_committee_degraded=true`。
- handoff 缺少 `research_panel`：manual 报告必须写入 `research_panel_missing`。
- 关键数据 `disputed/stale/missing`：主行动至少降级一级。
- 社交/新闻单源未确认：最多 `watch`。

## External Subagent Import

`scripts/research_panel_runner.py` 可通过 `--external-agent-outputs-json` 读取真实 subagent 输出。导入文件必须是 agent output 数组，或包含 `agent_outputs` 的对象。

在导入 report runner 前，建议先运行 `scripts/research_committee_quality_auditor.py`。该审计器会输出每个角色的 source 状态、verified 角色数量、missing data、failed gates、质量门 blockers 和 repair queue，并可生成 normalized output 包：

```bash
python3 manual-investment-strategy-operator/scripts/research_committee_quality_auditor.py \
  --external-agent-outputs-json /private/tmp/{run_id}-external-subagent-outputs.json \
  --normalized-output /private/tmp/{run_id}-external-subagent-outputs.normalized.json
```

normalized output 只修正 schema/枚举格式，不能把真实缺失的数据升级为 `verified`。

导入规则：

- `agent_id` 必须来自本 schema 的 8 个研究角色。
- 每个 agent output 必须包含所有必填字段。
- `sources_used` 中每个来源必须包含 `name`、`url_or_provider`、`fresh_at`、`status`、`coverage`。
- `source_refs` 中每个来源必须包含 `source_ref`、`name`、`url_or_provider`、`fresh_at`、`status`、`coverage`。
- `evidence_items` 中每个字段必须包含 `field_id`、`value`、`unit`、`as_of`、`source_ref`、`verification_status`、`missing_reason`；`verification_status=verified` 只能引用实际存在的 `source_ref`。
- `signals` 中每个信号必须包含 `asset`、`signal_type`、`direction`、`summary`、`time_window`。
- 出现 `funding / OI / spread / depth / slippage / drawdown / OOS / EV / walk-forward` 等硬术语时，必须在 `plain_language_notes` 写一句短解释。
- 外部 subagent 禁止输出 `execute_now`；该动作只能由 manual skill 在所有风控与人工确认草案通过后生成。
- 默认全覆盖模式下 8 个研究角色都应来自真实 external subagent；不相关角色可以返回 `asset_scope=["not_applicable"]` 并解释原因，但不能静默缺失。
- 至少 6 个唯一合法角色通过校验、`successful_known_role_count>=6`、`evidence_verified_known_role_count>=6`、`roles_with_ok_source_count>=6`、`committee_quality_gate_passed=true`、不是全部 missing/stale/disputed，且 `missing_external_roles=[]`，才可以视为非降级真实外部 Research Committee。
- 即使外部输出通过，`execute_now` 仍必须再经过 manual skill 的风险门、双 80 gate 和人工确认。

## Conservative Normalization

真实 subagent 常会返回人类可读状态，例如 `read_ok`、`partial`、`cautious_bullish` 或 `watch_or_conditional_only`。`scripts/research_panel_runner.py` 会先做保守标准化，再执行 schema 校验：

- `read_ok / verified_official / success` -> `source.status=ok`。
- `partial / fallback / api_reference_only / degraded` -> `source.status=fallback`。
- `failed / blocked / timeout / unavailable` -> `source.status=failed`。
- 非枚举 `data_quality` 默认降级为 `degraded`；只有明确无缺口的 `verified` 才保留 verified。
- 非枚举 `direction` 会折叠到 `bullish / neutral / bearish / mixed`。
- 非枚举 `recommended_max_action` 会折叠到最保守的可识别动作，无法识别则为 `watch`。

标准化只修正格式，不能提升动作等级。若少于 6 个角色达到 `evidence_quality=verified`，或少于 6 个角色有至少一个 `ok` source，`research_committee_degraded` 必须保持 `true`。即使 Research Committee 非降级，只要 `action_readiness` 显示缺成本、现金、paper 样本、概率校准或双 80 未通过，仍不得输出新的 `execute_now`。

示例文件：

- `examples/external_subagent_outputs.example.json`
