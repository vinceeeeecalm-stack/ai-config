# Handoff Protocol

本文件定义 monitor 如何把候选交给 `manual-investment-strategy-operator`。

## 基本原则

monitor 输出的是“候选”和“证据”，不是交易指令。

manual skill 收到 handoff 后必须重新检查：

- 当前持仓和现金通道。
- 长期目标和 DCA。
- 质押/锁仓流动性。
- 风险模型。
- 人工确认。

## Handoff 等级

| monitor_recommendation | 含义 | manual 默认处理 |
|---|---|---|
| `no_deploy` | 不合格 | 报告原因 |
| `watch` | 观察 | 加入观察区 |
| `paper_only` | 只做模拟 | 不动真金 |
| `paper_forward_candidate` | 可开始 paper | 记录虚拟交易 |
| `small_probe_review` | paper 后可能小仓 | 交给 manual 二次审查 |
| `us_open_scan` | 美股开盘动态扫描候选 | manual 重新识别当前战术仓和现金接力 |
| `social_key_person_intel` | Crypto 关键人物/官方社交情报 | manual 输出情报面板并重新检查价格、流动性和风险 |
| `daily_crypto_paper_auto_trader` | 日常 crypto paper 自动扫描和虚拟开平仓 | 只能作为 paper/watch 输入，manual 复核后才能进入真实建议 |
| `fast_crypto_paper_auto_trader` | 高频 crypto paper 入场/加仓扫描 | 只能作为 paper/watch 输入，manual 复核后才能进入真实建议 |
| `sunday_crypto_realistic_paper_loop` | 周日 crypto 真实感 paper 高频链路 | 只能作为 paper/watch 输入，manual 复核后才能进入真实建议 |
| `paper_position_exit_monitor` | 快速复盘 open paper 仓位和模拟退出 | 只能作为 paper exit/review 输入，不允许真实交易 |

## JSON Schema

```json
{
  "handoff_id": "YYYYMMDD-alpha-seq",
  "created_at": "ISO-8601",
  "source_skill": "active-alpha-paper-monitor",
  "target_skill": "manual-investment-strategy-operator",
  "candidate_type": "watch_candidate | paper_candidate | us_open_scan | social_key_person_intel | daily_crypto_paper_auto_trader | fast_crypto_paper_auto_trader | sunday_crypto_realistic_paper_loop | paper_position_exit_monitor | event_alert | risk_alert",
  "symbol": "ETHUSDT",
  "asset_class": "crypto",
  "time_horizon": "intraday | 1d-7d | 1w-1m",
  "rail_candidate": "crypto_rail | us_equity_rail | none",
  "multi_source_alpha_score_points": 0,
  "walk_forward_stage": "failed | research_watch | paper_only | small_probe_candidate | strict_pass_wait_current_signal",
  "current_signal": false,
  "paper_trade_plan": {
    "entry_rule": null,
    "stop_rule": null,
    "take_profit_rule": null,
    "max_holding_window": null
  },
  "candidate_symbol": null,
  "entry_zone": null,
  "target_price": null,
  "target_time_window": null,
  "stop_loss": null,
  "latest_exit_date": null,
  "forecast_probability_pct": null,
  "execution_readiness_score": null,
  "why_better_than_current_tactical_position": null,
  "candidate_cash_relay_priority": null,
  "evidence": [],
  "social_key_person_intel": [
    {
      "intel_id": "social-abc123",
      "platform": "official_rss | x | bluesky | farcaster | reddit",
      "person_or_entity": "Ethereum Foundation",
      "role": "official_project_account",
      "verified_identity_status": "official_source | known_public_person | manual_verify_required | unverified",
      "post_url": "https://...",
      "posted_at": "ISO-8601",
      "captured_at": "ISO-8601",
      "asset_tags": ["ETH"],
      "event_type": "roadmap_upgrade | tokenomics_unlock | listing_delisting | security_risk | regulatory_policy | staking_policy | ecosystem_partnership | fund_flow | founder_confirm_denial | market_rumor | general_commentary",
      "summary": "short summary",
      "source_credibility_score": 0,
      "market_relevance_score": 0,
      "social_intel_score_points": 0,
      "confirmation_status": "single_source_unconfirmed | official_confirmed | cross_source_confirmed",
      "price_reaction_window": "requires 1h/4h/24h price-volume confirmation",
      "recommended_max_action": "watch | paper_only | conditional_action | risk_alert",
      "risk_flags": []
    }
  ],
  "research_panel": {
    "run_id": "YYYYMMDD-run-type-seq",
    "agent_outputs": [
      {
        "agent_id": "macro_regime_agent",
        "origin": "external_subagent | local_fallback",
        "asset_scope": ["crypto", "us_equity"],
        "sources_used": [
          {
            "name": "FRED_or_public_market_data",
            "url_or_provider": "provider name or URL",
            "status": "ok",
            "fresh_at": "ISO-8601",
            "coverage": "rates_dxy_risk_appetite"
          }
        ],
        "signals": [],
        "confidence_pct": 0,
        "data_quality": "verified | degraded | disputed | stale | missing",
        "missing_data": [],
        "failed_gates": [],
        "recommended_max_action": "watch | paper_only | conditional_action | risk_alert | no_deploy | hold | trim_review",
        "what_would_change_my_mind": []
      }
    ],
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
    "external_agent_validation": {}
  },
  "failed_gates": [],
  "monitor_recommendation": "no_deploy",
  "paper_order_status": null,
  "max_allowed_action": "watch",
  "research_panel_missing": false,
  "research_panel_missing_reason": null,
  "research_committee_degraded": false,
  "requires_manual_review": true
}
```

## 文件命名

```text
handoffs/YYYY-MM-DD-alpha-handoff.json
handoffs/YYYY-MM-DD-us-open-handoff.json
reports/YYYY-MM-DD-alpha-monitor.md
reports/YYYY-MM-DD-us-open-monitor.md
handoffs/YYYY-MM-DD-social-key-person-intel-handoff.json
reports/YYYY-MM-DD-social-key-person-intel.md
experiments/YYYYMMDD-alpha-monitor.json
experiments/YYYYMMDD-social-key-person-intel.json
paper_trades/YYYY-MM-DD-paper-trades.json
```

## Research Panel 要求

- 每个 handoff 必须包含 `research_panel`；如果 subagent、数据源或研究角色不可用，必须包含 `research_panel_missing_reason` 并设置 `research_committee_degraded=true`。
- active monitor 不允许保留或输出 `execute_now`；外部 subagent 若给出 `execute_now`，active handoff 必须降级为 `conditional_action`，最终动作只能由 manual skill 二次审查。
- active monitor 不输出真实交易许可；`research_panel.max_allowed_action` 最高只能表达 monitor 层级的 `watch/paper_only/conditional_action/risk_alert/no_deploy`。
- 当 `research_panel_missing=true` 时，`monitor_recommendation` 和 candidate-level `monitor_recommendation` 最高只能是 `watch / paper_only / risk_alert / no_deploy / hold / trim_review`；不能使用 `small_probe_review`。如果脚本想保留预研究等级，应写到 `pre_research_candidate_grade`。
- `max_allowed_action` 表示 real-money/manual 层面的最高允许动作；当 research panel 缺失或降级时，`monitor_recommendation` 不得高于 `max_allowed_action`。若需要保留更高的预研究等级，写入 `pre_research_candidate_grade`，而不是提高 `monitor_recommendation`。
- `paper_opened`、`watch_or_review_only` 这类状态词不得写入 `monitor_recommendation`，应写到 `paper_order_status` 或 `opened_position`。
- manual skill 消费 handoff 后必须重新执行组合、现金、长期目标、风险和人工确认 gate。
- 社交或新闻 subagent 不能单独把候选推进到真实交易，只能影响观察优先级、风险提示和 conditional 条件。
