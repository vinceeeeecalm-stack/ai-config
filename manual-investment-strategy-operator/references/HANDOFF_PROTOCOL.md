# Handoff Protocol

本文件定义 `manual-investment-strategy-operator` 与 `active-alpha-paper-monitor` 的关系。

## 关系

| 能力 | 角色 | 输出 | 下游 |
|---|---|---|---|
| active-alpha-paper-monitor | 雷达、paper、事件监控 | 候选、paper记录、事件警报 | manual-investment-strategy-operator |
| manual-investment-strategy-operator | 组合决策、报告、人工确认草案 | 持仓报告、操作建议、proposed changes | 用户 |

monitor 发现机会，但不决定真实交易。manual skill 消费机会，但必须重新检查组合、现金、风险和长期目标。

manual skill 的“主动获取数据”只在用户手动调度时发生：它可以在本次报告内主动刷新市场、情绪、宏观和持仓数据，但不持续后台扫描、不自动下单、不自动移动资金。持续扫描、paper trading 和验证样本积累属于 active skill；manual skill 只读取结果并重新仲裁。

## Handoff 文件

monitor 应输出 Markdown 和 JSON 两类文件：

```text
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-alpha-handoff.json
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-us-open-handoff.json
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-social-key-person-intel-handoff.json
active-alpha-paper-monitor/reports/YYYY-MM-DD-alpha-monitor.md
active-alpha-paper-monitor/reports/YYYY-MM-DD-us-open-monitor.md
active-alpha-paper-monitor/reports/YYYY-MM-DD-social-key-person-intel.md
```

manual skill 读取 handoff 后，只把候选视为输入，不视为指令。

## Handoff Schema

```json
{
  "handoff_id": "YYYYMMDD-alpha-seq",
  "created_at": "ISO-8601",
  "source_skill": "active-alpha-paper-monitor",
  "strategy_version": "vX.Y",
  "candidate_type": "watch_candidate | paper_candidate | us_open_scan | social_key_person_intel | event_alert | risk_alert",
  "asset_class": "crypto | us_equity",
  "symbol": "ETHUSDT",
  "rail_candidate": "crypto_rail | us_equity_rail | none",
  "multi_source_alpha_score_points": 0,
  "walk_forward_stage": "failed | research_watch | paper_only | small_probe_candidate | strict_pass_wait_current_signal",
  "current_signal": false,
  "paper_trade": {
    "entry_price": null,
    "stop_price": null,
    "take_profit_price": null,
    "time_window": null,
    "status": "not_started | open | closed"
  },
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
        "agent_id": "crypto_market_agent",
        "origin": "external_subagent | local_fallback",
        "asset_scope": ["ETHUSDT"],
        "sources_used": [
          {
            "name": "binance_spot_24h",
            "url_or_provider": "Binance",
            "status": "ok",
            "fresh_at": "ISO-8601",
            "coverage": "price_volume_liquidity"
          }
        ],
        "signals": [],
        "confidence_pct": 0,
        "data_quality": "verified | degraded | disputed | stale | missing",
        "missing_data": [],
        "failed_gates": [],
        "recommended_max_action": "watch | paper_only | conditional_action | risk_alert | no_deploy | block | hold | trim_review | execute_now",
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
    "research_method": "external_subagent_outputs | local_degraded_runner",
    "external_roles_used": [],
    "local_fallback_roles": [],
    "missing_external_roles": [],
    "required_external_roles": [
      "prior_thesis_challenge_agent",
      "portfolio_state_agent",
      "macro_regime_agent",
      "crypto_market_agent",
      "onchain_defi_agent",
      "social_news_agent",
      "us_equity_alpha_agent",
      "backtest_validation_agent"
    ],
    "external_agent_validation": {}
  },
  "fresh_market_intelligence_snapshot": {
    "snapshot_id": "YYYYMMDD-manual-market-refresh-seq",
    "captured_at": "ISO-8601",
    "asset_scope": [],
    "sources_attempted": [],
    "sources_succeeded": [],
    "market_sentiment_summary": "",
    "macro_summary": "",
    "crypto_market_summary": "",
    "us_equity_market_summary": "",
    "missing_or_failed_sources": [],
    "downgrade_effect": "none | smaller_size | conditional_only | block_execute_now",
    "market_intelligence_degraded": false
  },
  "failed_gates": [],
  "monitor_recommendation": "no_deploy | watch | paper_only | small_probe_review",
  "entry_zone": null,
  "target_price": null,
  "target_time_window": null,
  "stop_loss": null,
  "latest_exit_date": null,
  "forecast_probability_pct": null,
  "execution_readiness_score": null,
  "why_better_than_current_tactical_position": null,
  "source_position_to_fund": null,
  "candidate_cash_relay_priority": null,
  "requires_manual_review": true
}
```

## Manual 处理规则

- `no_deploy`: 报告说明原因，不部署。
- `watch`: 放入观察区，定义下次复盘时间。
- `paper_only`: 记录虚拟交易，不使用真实资金。
- `small_probe_review`: 只有在 manual skill 再次通过组合层风险后，才能提出人工确认小仓草案。
- `us_open_scan`: 当作美股开盘动态扫描候选输入；manual skill 必须重新识别当前战术仓、现金接力状态、入场价是否仍有效和双 80 gate。
- `social_key_person_intel`: 输出 Crypto Key Person Intelligence Panel；官方风险可触发暂停新增风险，正向消息最多影响 watch/conditional，不得单独扩大实盘仓位。
- `daily_crypto_paper_auto_trader`: 只作为 crypto paper/watch 输入；manual 必须重新检查持仓、现金、长期 DCA 和 risk gate，不得把 paper open 当作真实交易建议。
- `sunday_crypto_realistic_paper_loop`: 只作为周日高频 paper 输入；manual 必须重新检查多源数据、现金通道和人工确认，不得直接升级为真实交易。
- `research_panel`: 作为 Research Committee Gate 的输入。manual skill 必须重新仲裁，不得把 monitor 的 `max_allowed_action` 当成真实交易许可。
- active handoff 自带的 `research_panel` 只代表 monitor 层研究证据，不能直接等同于本次 manual Research Committee。manual 报告必须重新运行 `scripts/research_panel_runner.py`，校验外部 subagent 输出、持仓、现金通道、长期目标、风险门和人工确认后，才允许形成操作草案。
- active handoff 自带的市场数据只代表 monitor 取数时点，不能替代本次 manual `Fresh Market Intelligence Panel`。manual 报告若无法刷新关键市场和情绪数据，必须标记 `market_intelligence_degraded` 并阻止新的 `execute_now`。

任何 handoff 都不能绕过：

- 现金通道确认。
- 质押/锁仓流动性检查。
- 美股工具范围检查。
- 风险模型。
- 用户人工确认。

## Research Panel 处理规则

- monitor handoff 缺少 `research_panel` 时，manual 报告必须写入 `research_panel_missing`，并把 monitor 候选降级为 `watch/paper_only/no_deploy`。
- `research_committee_degraded=true` 时，不允许从该 handoff 生成新的 `execute_now`。
- manual 必须至少复核 6 个相关研究角色；不足时标记 `research_committee_degraded`。
- `prior_thesis_status=invalidated` 或 `insufficient_data` 时，旧结论不得直接沿用到本次主建议。
- 任一关键 subagent 输出 `block/no_deploy/risk_alert` 时，对应资产不得新增真实仓位。
