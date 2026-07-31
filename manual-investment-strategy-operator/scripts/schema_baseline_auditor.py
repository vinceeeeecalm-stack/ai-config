#!/usr/bin/env python3
"""Audit SQL/schema baseline files for the manual investment skill.

The user may refer to "SQL documents", while the current workspace stores the
authoritative state as Markdown schema documents plus JSON ledgers. This
read-only auditor checks both possibilities and reports whether the baseline is
strong enough to support a manual strategy dispatch. It never mutates ledgers
and never authorizes trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


REQUIRED_BASELINE_FILES: list[dict[str, Any]] = [
    {
        "id": "portfolio_ledger_schema",
        "path": "unified-longterm-alpha-investor/references/PORTFOLIO_LEDGER_SCHEMA.md",
        "type": "markdown_schema",
        "required": True,
        "purpose": "Defines holdings, cash rails, staking, lots, recommendation history, validation and outcome fields.",
        "supports": ["portfolio_state", "cost_basis", "staking", "cash_rails", "recommendation_records"],
        "contains": ["Portfolio Ledger Schema", "Recommendation History", "Crypto Staking", "US Equity Spot"],
    },
    {
        "id": "api_priced_ledger_policy",
        "path": "unified-longterm-alpha-investor/references/API_PRICED_LEDGER_POLICY.md",
        "type": "markdown_policy",
        "required": True,
        "purpose": "Requires screenshot quantities but API/multi-source prices for valuation and action prices.",
        "supports": ["api_valuation", "data_quality", "action_price"],
        "contains": ["API", "截图", "market_value", "action_price"],
    },
    {
        "id": "portfolio_ledger",
        "path": "unified-longterm-alpha-investor/config/portfolio_ledger.json",
        "type": "json_ledger",
        "required": True,
        "purpose": "Current portfolio, holdings, cash rails, cost-basis coverage and pending updates.",
        "supports": ["current_holdings", "cash_rails", "staking", "goal_gap_inputs"],
        "required_keys": ["as_of", "account_summary", "cash_rails", "holdings", "cost_basis_reconciliation"],
    },
    {
        "id": "recommendation_history",
        "path": "manual-investment-strategy-operator/recommendations/recommendation_history.json",
        "type": "json_learning_ledger",
        "required": True,
        "purpose": "Manual recommendations, outcome reviews, proposed changes and probability calibration base.",
        "supports": ["learning_loop", "probability_calibration", "outcome_review"],
        "required_keys": ["schema_version", "recommendations", "outcome_reviews", "proposed_changes"],
    },
    {
        "id": "paper_portfolio_ledger",
        "path": "active-alpha-paper-monitor/paper_trades/paper_portfolio_ledger.json",
        "type": "json_paper_ledger",
        "required": True,
        "purpose": "Paper trading samples for strategy validation before any live promotion.",
        "supports": ["paper_validation", "promotion_gate", "drawdown_check"],
        "required_keys": ["ledger_version", "live_orders_enabled", "open_positions", "closed_trades", "equity_usd"],
    },
    {
        "id": "manual_strategy_config",
        "path": "manual-investment-strategy-operator/config/manual_strategy_config.json",
        "type": "json_config",
        "required": True,
        "purpose": "Manual dispatch goals, DCA defaults, target gates, risk rules and progressive learning settings.",
        "supports": ["five_to_ten_year_goal", "monthly_dca", "us_tactical_goal", "double_80_gate"],
        "required_keys": ["goal_10x_execution_plan", "goal_oriented_dca", "manual_dispatch_runner"],
    },
    {
        "id": "active_alpha_monitor_config",
        "path": "active-alpha-paper-monitor/config/active_alpha_monitor_config.json",
        "type": "json_config",
        "required": True,
        "purpose": "Active monitor paper/watch validation, no-live-order boundary and handoff target.",
        "supports": ["paper_monitor", "handoff", "validation_samples"],
        "required_keys": ["paper_portfolio", "handoff_target"],
    },
]


OBJECTIVE_REQUIREMENTS = [
    {
        "id": "five_year_10x_and_ten_year_fallback",
        "requirement": "Plan and dispatch logic must evaluate 5-year 10x and 10-year 10x target paths.",
        "needs": ["manual_strategy_config", "portfolio_ledger", "portfolio_ledger_schema"],
    },
    {
        "id": "monthly_1000_dca",
        "requirement": "Crypto DCA must default around $1,000/month and map each deployment to long-term goal contribution.",
        "needs": ["manual_strategy_config", "portfolio_ledger", "api_priced_ledger_policy"],
    },
    {
        "id": "current_cost_basis_included",
        "requirement": "Current holdings and partial/full cost basis must be included without inventing unknown lots.",
        "needs": ["portfolio_ledger", "portfolio_ledger_schema", "api_priced_ledger_policy"],
    },
    {
        "id": "us_equity_monthly_quarterly_tactical_goal",
        "requirement": "US equity tactical sleeve must track monthly/quarterly 50%+ goal without treating protected long-term holdings as tactical cash.",
        "needs": ["manual_strategy_config", "portfolio_ledger", "recommendation_history"],
    },
    {
        "id": "progressive_learning_loop",
        "requirement": "Each dispatch must record recommendations, review outcomes, and improve from 60% learning samples toward 80%+ validation.",
        "needs": ["recommendation_history", "paper_portfolio_ledger", "active_alpha_monitor_config", "manual_strategy_config"],
    },
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # defensive CLI reporting
        return None, f"{type(exc).__name__}: {exc}"


def inspect_baseline_file(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    path = root / spec["path"]
    row: dict[str, Any] = {
        "id": spec["id"],
        "path": str(path),
        "relative_path": spec["path"],
        "type": spec["type"],
        "required": bool(spec.get("required")),
        "purpose": spec["purpose"],
        "supports": spec.get("supports", []),
        "exists": path.exists(),
        "status": "missing",
        "details": [],
    }
    if not path.exists():
        row["details"].append("file_missing")
        return row

    row["size_bytes"] = path.stat().st_size
    if spec["type"].startswith("json"):
        data, error = load_json(path)
        if error:
            row["status"] = "invalid"
            row["details"].append(error)
            return row
        row["json_type"] = type(data).__name__
        if isinstance(data, dict):
            row["top_level_keys"] = list(data.keys())[:50]
            missing_keys = [key for key in spec.get("required_keys", []) if key not in data]
            row["missing_required_keys"] = missing_keys
            row["status"] = "ok" if not missing_keys else "degraded"
            add_metrics(row, spec["id"], data)
        else:
            row["status"] = "degraded"
            row["details"].append("json_top_level_not_object")
        return row

    text = path.read_text(encoding="utf-8", errors="replace")
    missing_markers = [marker for marker in spec.get("contains", []) if marker not in text]
    row["missing_required_markers"] = missing_markers
    row["status"] = "ok" if not missing_markers else "degraded"
    return row


def add_metrics(row: dict[str, Any], spec_id: str, data: dict[str, Any]) -> None:
    if spec_id == "recommendation_history":
        row["metrics"] = {
            "recommendations": len(data.get("recommendations") or []),
            "outcome_reviews": len(data.get("outcome_reviews") or []),
            "proposed_changes": len(data.get("proposed_changes") or []),
            "resolved_recommendations": sum(
                1
                for rec in data.get("recommendations") or []
                if rec.get("outcome_status") in {"hit", "failed", "not_triggered", "expired", "invalidated"}
            ),
        }
    elif spec_id == "paper_portfolio_ledger":
        row["metrics"] = {
            "live_orders_enabled": data.get("live_orders_enabled"),
            "open_positions": len(data.get("open_positions") or []),
            "closed_trades": len(data.get("closed_trades") or []),
            "equity_usd": data.get("equity_usd"),
            "net_return_pct": data.get("net_return_pct"),
        }
        if data.get("live_orders_enabled") is not False:
            row["status"] = "degraded"
            row["details"].append("paper_ledger_live_orders_enabled_not_false")
    elif spec_id == "portfolio_ledger":
        holdings = data.get("holdings") or []
        row["metrics"] = {
            "as_of": data.get("as_of"),
            "holding_count": len(holdings) if isinstance(holdings, list) else None,
            "strategy_version": data.get("strategy_version"),
            "cash_rails_present": isinstance(data.get("cash_rails"), dict),
            "ledger_pricing_policy": data.get("ledger_pricing_policy"),
        }
    elif spec_id == "manual_strategy_config":
        goal_plan = data.get("goal_10x_execution_plan") or {}
        tactical_tracker = goal_plan.get("us_tactical_performance_tracker") or {}
        dca = data.get("goal_oriented_dca") or {}
        row["metrics"] = {
            "default_monthly_dca_usd": goal_plan.get("default_monthly_dca_usd") or dca.get("monthly_dca_usd_default"),
            "us_tactical_monthly_target_pct": tactical_tracker.get("target_monthly_return_pct"),
            "us_tactical_quarterly_target_pct": tactical_tracker.get("target_quarterly_return_pct"),
            "goal_oriented_dca_enabled": bool(dca),
        }
    elif spec_id == "active_alpha_monitor_config":
        paper = data.get("paper_portfolio") or {}
        row["metrics"] = {
            "handoff_target": data.get("handoff_target"),
            "paper_ledger_path": paper.get("ledger_path"),
        }


def find_sql_files(search_roots: list[Path]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.sql"):
            resolved = str(path)
            if resolved not in seen:
                seen.add(resolved)
                found.append(resolved)
    return sorted(found)


def build_audit(root: Path, include_skill_roots: bool = True) -> dict[str, Any]:
    search_roots = [root]
    if include_skill_roots:
        search_roots.extend(
            [
                Path.home() / ".codex/skills/manual-investment-strategy-operator",
                Path.home() / ".qoderwork/skills/manual-investment-strategy-operator",
            ]
        )

    sql_files = find_sql_files(search_roots)
    baseline_rows = [inspect_baseline_file(root, spec) for spec in REQUIRED_BASELINE_FILES]
    by_id = {row["id"]: row for row in baseline_rows}

    blocking_rows = [row for row in baseline_rows if row["required"] and row["status"] in {"missing", "invalid"}]
    degraded_rows = [row for row in baseline_rows if row["status"] == "degraded"]
    if blocking_rows:
        status = "schema_baseline_blocked"
        max_allowed_action = "repair_only"
    elif degraded_rows:
        status = "schema_baseline_degraded"
        max_allowed_action = "conditional_action_or_watch"
    else:
        status = "verified_schema_baseline"
        max_allowed_action = "normal_gates_still_required"

    requirement_rows = []
    for requirement in OBJECTIVE_REQUIREMENTS:
        needed = requirement["needs"]
        missing_or_invalid = [
            item for item in needed if item not in by_id or by_id[item]["status"] in {"missing", "invalid"}
        ]
        degraded = [item for item in needed if by_id.get(item, {}).get("status") == "degraded"]
        requirement_rows.append(
            {
                **requirement,
                "status": "blocked" if missing_or_invalid else ("degraded" if degraded else "covered"),
                "missing_or_invalid": missing_or_invalid,
                "degraded_sources": degraded,
            }
        )

    return {
        "generated_at": utc_now(),
        "audit_version": "schema-baseline-audit-v1",
        "workspace_root": str(root),
        "sql_files_found": sql_files,
        "sql_file_count": len(sql_files),
        "sql_interpretation": (
            "sql_files_present"
            if sql_files
            else "no_sql_files_found_use_markdown_schema_and_json_ledgers_as_authoritative_baseline"
        ),
        "status": status,
        "max_allowed_action_from_schema_baseline": max_allowed_action,
        "live_orders_enabled": False,
        "mutates_ledger": False,
        "baseline_files": baseline_rows,
        "objective_requirement_coverage": requirement_rows,
        "blocking_files": [row["id"] for row in blocking_rows],
        "degraded_files": [row["id"] for row in degraded_rows],
        "operator_note": (
            "This audit only verifies the local schema/ledger baseline. It does not fetch market data, "
            "does not prove the 10x goal, and does not authorize execute_now."
        ),
    }


def render_markdown(audit: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Schema Baseline Audit | {audit['generated_at']}")
    lines.append("")
    lines.append("This read-only audit checks whether the current plan is grounded in the available SQL/schema/ledger baseline.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Status | `{audit['status']}` |")
    lines.append(f"| SQL files found | `{audit['sql_file_count']}` |")
    lines.append(f"| SQL interpretation | `{audit['sql_interpretation']}` |")
    lines.append(f"| Max action from schema baseline | `{audit['max_allowed_action_from_schema_baseline']}` |")
    lines.append(f"| Live orders enabled | `{audit['live_orders_enabled']}` |")
    lines.append("")
    if not audit["sql_files_found"]:
        lines.append("No `.sql` files were found in the workspace or installed manual skill roots. Current execution should treat Markdown schema docs and JSON ledgers as the authoritative baseline.")
        lines.append("")
    else:
        lines.append("SQL files found:")
        for path in audit["sql_files_found"]:
            lines.append(f"- `{path}`")
        lines.append("")

    lines.append("## Baseline Files")
    lines.append("")
    lines.append("| ID | Status | Purpose | Key metrics / gaps |")
    lines.append("|---|---|---|---|")
    for row in audit["baseline_files"]:
        metrics = row.get("metrics") or {}
        gaps = []
        if row.get("missing_required_keys"):
            gaps.append("missing keys: " + ", ".join(row["missing_required_keys"]))
        if row.get("missing_required_markers"):
            gaps.append("missing markers: " + ", ".join(row["missing_required_markers"]))
        if row.get("details"):
            gaps.extend(row["details"])
        metric_text = "; ".join(f"{key}={value}" for key, value in metrics.items()) if metrics else ("; ".join(gaps) if gaps else "ok")
        lines.append(f"| `{row['id']}` | `{row['status']}` | {row['purpose']} | {metric_text} |")

    lines.append("")
    lines.append("## Objective Coverage")
    lines.append("")
    lines.append("| Requirement | Status | Depends on | Gaps |")
    lines.append("|---|---|---|---|")
    for row in audit["objective_requirement_coverage"]:
        gaps = []
        if row["missing_or_invalid"]:
            gaps.append("missing/invalid: " + ", ".join(row["missing_or_invalid"]))
        if row["degraded_sources"]:
            gaps.append("degraded: " + ", ".join(row["degraded_sources"]))
        lines.append(
            f"| {row['requirement']} | `{row['status']}` | {', '.join(row['needs'])} | "
            f"{'; '.join(gaps) if gaps else 'covered'} |"
        )

    lines.append("")
    lines.append("## Plain-Language Note")
    lines.append("")
    lines.append("- `schema`: 数据结构说明，告诉系统账本里应该有哪些字段。")
    lines.append("- `ledger`: 账本，记录持仓、现金、成本、质押和建议/交易历史。")
    lines.append("- `SQL`: 数据库查询语言。当前没有 `.sql` 文件时，不代表系统不能执行；当前权威基础是 schema 文档和 JSON 账本。")
    lines.append("")
    lines.append(audit["operator_note"])
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit SQL/schema baseline files for manual strategy execution")
    parser.add_argument("--root", default=".", help="Workspace root")
    parser.add_argument("--output", help="JSON output path")
    parser.add_argument("--markdown-output", help="Markdown output path")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    audit = build_audit(root)
    if args.output:
        write_json(Path(args.output), audit)
    if args.markdown_output:
        path = Path(args.markdown_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(audit), encoding="utf-8")

    if args.format == "markdown":
        print(render_markdown(audit))
    else:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
