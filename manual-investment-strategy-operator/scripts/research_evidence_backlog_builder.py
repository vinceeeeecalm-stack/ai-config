#!/usr/bin/env python3
"""Build a repair backlog for Research Committee evidence gaps.

This script consumes the quality-audit JSON produced by
research_committee_quality_auditor.py and turns role-native evidence gaps into
concrete data collection tasks for the next subagent run. It does not fetch
market data and never authorizes trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROLE_EVIDENCE_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "macro_regime_agent": {
        "goal": "Upgrade macro pacing evidence without letting macro alone trigger a single-asset buy.",
        "source_categories": [
            {
                "id": "rates_policy",
                "examples": ["CME FedWatch or official Fed/FRED proxy", "Treasury yield curve"],
                "fields": ["fed_funds_or_rate_expectation", "2y_yield", "10y_yield", "10y_2y_spread", "timestamp"],
            },
            {
                "id": "inflation",
                "examples": ["BLS CPI", "BEA/FRED PCE"],
                "fields": ["latest_cpi_yoy", "latest_pce_yoy_or_missing_reason", "release_period", "timestamp"],
            },
            {
                "id": "risk_appetite",
                "examples": ["VIX", "DXY", "QQQ/SPY/SOXX or equivalents"],
                "fields": ["vix", "dxy_trend", "equity_risk_trend", "semiconductor_trend"],
            },
            {
                "id": "crypto_fund_flows",
                "examples": ["CoinShares weekly flows", "spot BTC/ETH ETF flow source if available"],
                "fields": ["btc_flow", "eth_flow", "digital_asset_flow", "source_url", "timestamp"],
            },
        ],
        "pass_condition": "At least rates/policy, inflation, risk appetite, and one fund-flow source are fresh or explicitly unavailable with fallback reason.",
    },
    "crypto_market_agent": {
        "goal": "Upgrade crypto trading evidence for DCA and tactical watch decisions.",
        "source_categories": [
            {
                "id": "spot_price_cross_check",
                "examples": ["Binance spot ticker/klines", "CoinGecko or CoinMarketCap", "one additional venue when available"],
                "fields": ["price", "1d_pct", "7d_pct", "30d_pct", "quote_volume", "source_timestamps"],
            },
            {
                "id": "order_book_liquidity",
                "examples": ["Binance depth", "venue order book for non-Binance pairs"],
                "fields": ["bid_ask_spread_bps", "depth_1pct_usd", "depth_2pct_usd", "slippage_note"],
            },
            {
                "id": "derivatives_context",
                "examples": ["Binance futures public funding/OI when relevant", "Coinglass or equivalent if available"],
                "fields": ["funding_rate", "open_interest", "basis_or_missing_reason"],
            },
            {
                "id": "tail_asset_supply_liquidity",
                "examples": ["Project tokenomics docs", "CMC/CG circulating supply", "exchange listing/depth evidence"],
                "fields": ["float", "unlock_schedule", "venue_count", "NIGHT_pair_status"],
            },
        ],
        "pass_condition": "Core assets have price plus liquidity; NIGHT or any tail asset must have float/unlock/depth before fresh add recommendations.",
    },
    "onchain_defi_agent": {
        "goal": "Upgrade long-term thesis evidence for SOL/ADA/ETH/lcETH/NIGHT and DCA sizing.",
        "source_categories": [
            {
                "id": "tvl_and_fees",
                "examples": ["DeFiLlama chain/protocol TVL", "DeFiLlama fees/revenue when available"],
                "fields": ["chain_tvl", "protocol_tvl", "fees_30d", "revenue_30d", "timestamp"],
            },
            {
                "id": "network_activity",
                "examples": ["chain explorers", "Artemis/TokenTerminal if available", "project dashboards"],
                "fields": ["active_addresses_or_missing", "transactions_or_missing", "developer_or_ecosystem_proxy"],
            },
            {
                "id": "staking_supply",
                "examples": ["Ledger/Coinbase staking docs", "protocol staking stats", "token unlock sources"],
                "fields": ["apy", "lock_or_cooldown", "inflation_or_rewards", "unlock_or_supply_risk"],
            },
            {
                "id": "asset_specific_gaps",
                "examples": ["lcETH terms", "NIGHT DUST utility and tokenomics", "ADA APY provider confirmation"],
                "fields": ["lceth_redemption_terms", "NIGHT_dust_utility", "ADA_provider_apy"],
            },
        ],
        "pass_condition": "Each recommended DCA asset has at least one fresh onchain/ecosystem source and staking/supply data, or is capped smaller.",
    },
    "social_news_agent": {
        "goal": "Upgrade official/key-person intelligence while keeping social signals below execute_now.",
        "source_categories": [
            {
                "id": "official_sources",
                "examples": ["project blogs/RSS", "foundation announcements", "exchange/issuer official posts"],
                "fields": ["post_url", "posted_at", "captured_at", "asset_tags", "event_type"],
            },
            {
                "id": "key_person_sources",
                "examples": ["X API if token exists", "Bluesky searchPosts", "Farcaster/Neynar", "Reddit/API fallback"],
                "fields": ["identity_status", "post_url", "posted_at", "summary", "confirmation_status"],
            },
            {
                "id": "market_confirmation",
                "examples": ["price/volume reaction from Binance/Yahoo", "news cross-source confirmation"],
                "fields": ["price_reaction_window", "volume_reaction", "cross_source_confirmation"],
            },
        ],
        "pass_condition": "Official or verified identity plus timestamps; single-source social remains watch only even after repair.",
    },
    "us_equity_alpha_agent": {
        "goal": "Upgrade US tactical candidate discovery versus the current deployable tactical position.",
        "source_categories": [
            {
                "id": "market_movers_screen",
                "examples": ["Yahoo/Nasdaq movers", "TwelveData/Finnhub/Alpha Vantage quote feeds"],
                "fields": ["candidate_symbol", "price", "volume", "1d_5d_20d_trend", "source_timestamps"],
            },
            {
                "id": "intraday_execution",
                "examples": ["quote bid/ask if available", "volume/liquidity proxy", "spread source"],
                "fields": ["entry_zone", "bid_ask_spread_or_proxy", "average_volume", "execution_readiness_inputs"],
            },
            {
                "id": "catalyst_validation",
                "examples": ["earnings/news/sector catalyst", "official filings/press releases"],
                "fields": ["confirmed_catalyst", "event_time", "why_better_than_current_tactical_position"],
            },
            {
                "id": "sample_tier_ev_inputs",
                "examples": ["base-rate evidence", "walk-forward/paper support", "untouched holdout", "target/stop/friction estimates"],
                "fields": ["target_price", "target_window", "sample_size", "calibration_status", "probability_range_pct", "target_return_pct", "stop_probability_pct", "stop_loss_return_pct", "friction_return_pct"],
            },
        ],
        "pass_condition": "Top 1-3 candidates have fresh data; an entry still requires a valid sample tier, positive conservative EV, RR>=2, a complete realtime signal, and the applicable account-risk cap.",
    },
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def by_agent(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("agent_id")): item for item in items if item.get("agent_id")}


def build_subagent_prompt(agent_id: str, task: dict[str, Any], run_id: str) -> str:
    fields = [
        "agent_id",
        "asset_scope",
        "sources_used",
        "source_refs",
        "evidence_items",
        "signals",
        "plain_language_notes",
        "confidence_pct",
        "data_quality",
        "missing_data",
        "failed_gates",
        "recommended_max_action",
        "what_would_change_my_mind",
    ]
    return (
        f"You are {agent_id}. Run independently for {run_id}; do not reuse old conclusions.\n"
        "Return exactly one JSON object with fields: "
        + ", ".join(fields)
        + ".\n"
        "Repair these evidence gaps: "
        + "; ".join(task.get("needed") or [])
        + ".\n"
        "Required source categories: "
        + "; ".join(category["id"] for category in task.get("source_categories") or [])
        + ".\n"
        "Machine verification requirements: "
        + "; ".join(task.get("machine_verification_requirements") or [])
        + ".\n"
        "Plain-language requirements: "
        + "; ".join(task.get("plain_language_requirements") or [])
        + ".\n"
        "If data is unavailable, record it in missing_data and downgrade. Do not output execute_now."
    )


def build_backlog(audit: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    validation = audit.get("external_agent_validation") or {}
    quality_requirements = validation.get("committee_quality_gate_requirements") or {}
    audit_repairs = by_agent(audit.get("repair_queue") or [])
    role_rows = by_agent(audit.get("role_rows") or [])
    action_blockers = by_agent(audit.get("action_blocker_queue") or [])
    tasks: list[dict[str, Any]] = []
    run_id = run_id or f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M')}-research-evidence-backlog"

    for agent_id, repair in audit_repairs.items():
        playbook = ROLE_EVIDENCE_PLAYBOOKS.get(agent_id, {})
        row = role_rows.get(agent_id, {})
        blocker = action_blockers.get(agent_id, {})
        task = {
            "agent_id": agent_id,
            "priority": repair.get("priority", "P1"),
            "current_evidence_quality": row.get("evidence_quality"),
            "current_action_readiness": row.get("action_readiness"),
            "needed": repair.get("needed") or [],
            "goal": playbook.get("goal", "Repair role-native evidence gaps."),
            "source_categories": playbook.get("source_categories", []),
            "pass_condition": playbook.get("pass_condition", "At least one fresh ok source and no role-native material gaps."),
            "action_blockers_to_keep_visible": blocker.get("blockers") or [],
            "expected_max_action_after_evidence_repair": (
                "watch_or_paper_or_conditional; entry still requires sample/EV/signal/risk evidence, promotion evidence, and human confirmation"
            ),
            "machine_verification_requirements": [
                "Every ok source must include name, url_or_provider, fresh_at, status, and specific coverage.",
                "Every role output must include source_refs with stable source_ref ids and evidence_items that cite those ids.",
                "Every evidence item must include field_id, value, unit, as_of, source_ref, verification_status, and missing_reason.",
                "Machine-readable numbers should include value, unit, as_of/fresh_at, and provider or URL.",
                "If only a webpage/snippet/manual observation is available, mark the source fallback and keep the missing machine field visible.",
                "Do not remove action blockers such as broker cash, cost basis, paper sample, or probability calibration unless the artifact directly proves them.",
            ],
            "plain_language_requirements": [
                "Explain hard terms such as funding, OI, spread, depth, slippage, drawdown, OOS, and walk-forward in simple Chinese-ready notes inside signals.",
                "Also put those explanations in plain_language_notes so the collector/auditor can verify they exist.",
                "Keep recommended_max_action conservative; social/news or single-source data cannot exceed watch or conditional_action.",
            ],
            "subagent_prompt": "",
        }
        task["subagent_prompt"] = build_subagent_prompt(agent_id, task, run_id)
        tasks.append(task)

    evidence_verified = validation.get("evidence_verified_known_role_count", validation.get("verified_known_role_count", 0))
    target_evidence_verified = quality_requirements.get("min_evidence_verified_known_roles", 6)
    roles_needed = max(0, int(target_evidence_verified or 6) - int(evidence_verified or 0))
    return {
        "run_id": run_id,
        "generated_at": utc_now(),
        "source_quality_audit": audit.get("run_id"),
        "source_path": audit.get("source_path"),
        "backlog_version": "research-evidence-backlog-v1",
        "live_orders_enabled": False,
        "current_quality_status": audit.get("status"),
        "current_max_allowed_action": audit.get("max_allowed_action"),
        "evidence_goal": {
            "current_evidence_verified_roles": evidence_verified,
            "target_evidence_verified_roles": target_evidence_verified,
            "additional_evidence_verified_roles_needed": roles_needed,
            "roles_with_ok_sources": validation.get("roles_with_ok_source_count"),
            "target_roles_with_ok_sources": quality_requirements.get("min_roles_with_ok_sources", 6),
            "committee_quality_gate_passed": validation.get("committee_quality_gate_passed"),
        },
        "research_evidence_tasks": tasks,
        "non_evidence_action_blockers": audit.get("action_blocker_queue") or [],
        "next_run_command_hint": (
            "After subagents return repaired JSON, run research_committee_quality_auditor.py, "
            "then research_panel_runner.py --external-agent-outputs-json with the normalized package."
        ),
        "operator_note": (
            "This backlog is a data-collection plan. It does not fetch data, does not change strategy weights, "
            "and does not authorize real trading."
        ),
    }


def render_markdown(backlog: dict[str, Any]) -> str:
    goal = backlog["evidence_goal"]
    lines = [
        f"# Research Evidence Backlog | {backlog['run_id']}",
        "",
        "This backlog converts Research Committee quality gaps into next-run data collection tasks. It does not authorize trades.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Current quality status | `{backlog['current_quality_status']}` |",
        f"| Current max action | `{backlog['current_max_allowed_action']}` |",
        f"| Evidence-verified roles | {goal['current_evidence_verified_roles']} / {goal['target_evidence_verified_roles']} |",
        f"| Additional evidence roles needed | {goal['additional_evidence_verified_roles_needed']} |",
        f"| Roles with ok sources | {goal['roles_with_ok_sources']} / {goal['target_roles_with_ok_sources']} |",
        "",
        "## Evidence Repair Tasks",
        "",
    ]
    if not backlog["research_evidence_tasks"]:
        lines.append("- none")
    for task in backlog["research_evidence_tasks"]:
        categories = ", ".join(category["id"] for category in task.get("source_categories") or [])
        needed = "; ".join(task.get("needed") or [])
        lines.extend(
            [
                f"### `{task['agent_id']}`",
                "",
                f"- Priority: `{task['priority']}`",
                f"- Current evidence quality: `{task.get('current_evidence_quality')}`",
                f"- Needed: {needed or 'none'}",
                f"- Required source categories: {categories or 'at least one fresh ok source'}",
                f"- Pass condition: {task.get('pass_condition')}",
                f"- Action cap after repair: {task.get('expected_max_action_after_evidence_repair')}",
                "",
            ]
        )
    lines.extend(["## Non-Evidence Action Blockers", ""])
    blockers = backlog.get("non_evidence_action_blockers") or []
    if not blockers:
        lines.append("- none")
    for item in blockers:
        blocker_text = "; ".join(item.get("blockers") or [])
        lines.append(f"- `{item.get('agent_id')}` `{item.get('action_readiness')}`: {blocker_text}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Research Committee evidence repair backlog")
    parser.add_argument("--quality-audit-json", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    audit = load_json(args.quality_audit_json)
    backlog = build_backlog(audit, run_id=args.run_id or None)
    if args.output:
        write_json(Path(args.output), backlog)
    if args.markdown_output:
        write_text(Path(args.markdown_output), render_markdown(backlog))
    if args.format == "markdown":
        print(render_markdown(backlog))
    else:
        print(json.dumps(backlog, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
