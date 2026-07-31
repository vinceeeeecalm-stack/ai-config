#!/usr/bin/env python3
"""Build a read-only closure package for unresolved goal blockers.

This script turns the system completion audit into a practical evidence
checklist. It does not mutate ledgers, place trades, move cash, or change
strategy parameters.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = ROOT / "active-alpha-paper-monitor"

DEFAULT_AUDIT = MANUAL_ROOT / "experiments" / "goal_system_completion_audit_20260530_full_goal_live_manual_v1_after_version_sync.json"
DEFAULT_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
DEFAULT_PORTFOLIO_LEDGER = ROOT / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json"
DEFAULT_PAPER_LEDGER = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
SAFE_PAPER_VALIDATION_PULSE_COMMAND = (
    "python3 active-alpha-paper-monitor/scripts/validation_progress_runner.py "
    "--cycles 1 --skip-fast --no-lock --compact-output --format json "
    "--exit-timeout-seconds 120 --validation-timeout-seconds 90"
)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PORTFOLIO_PACKAGER = load_module(SCRIPT_DIR / "portfolio_evidence_gap_packager.py", "portfolio_evidence_gap_packager_for_goal_closure")
CALIBRATION_PACKAGER = load_module(SCRIPT_DIR / "recommendation_calibration_packager.py", "recommendation_calibration_packager_for_goal_closure")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_if_exists(path: Path | str) -> Any:
    candidate = Path(path)
    if not candidate.exists():
        return {}
    return load_json(candidate)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def paper_metrics(path: Path) -> dict[str, Any]:
    ledger = load_json_if_exists(path)
    if not ledger:
        return {
            "status": "missing",
            "path": str(path),
            "open_count": 0,
            "closed_count": 0,
            "live_orders_enabled": None,
        }
    open_positions = ledger.get("open_positions") or []
    closed_trades = ledger.get("closed_trades") or []
    hit_count = sum(1 for trade in closed_trades if trade.get("outcome") in {"hit", "take_profit", "profit"})
    failed_count = sum(1 for trade in closed_trades if trade.get("outcome") in {"failed", "stop", "expired", "invalidated"})
    resolved = hit_count + failed_count
    return {
        "status": "ok",
        "path": str(path),
        "open_count": len(open_positions),
        "closed_count": len(closed_trades),
        "hit_count": hit_count,
        "failed_count": failed_count,
        "resolved_count": resolved,
        "win_rate_pct": round(hit_count / resolved * 100.0, 2) if resolved else None,
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
        "live_orders_enabled": ledger.get("live_orders_enabled"),
        "updated_at": ledger.get("updated_at"),
    }


def audit_check_map(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("requirement_id")): item
        for item in audit.get("checks") or []
        if isinstance(item, dict) and item.get("requirement_id")
    }


def item(
    blocker_id: str,
    priority: str,
    area: str,
    status: str,
    why_it_matters: str,
    required_evidence: list[str],
    user_action: str,
    automation_commands: list[str],
    max_allowed_until_closed: str,
    source: str,
) -> dict[str, Any]:
    return {
        "blocker_id": blocker_id,
        "priority": priority,
        "area": area,
        "status": status,
        "why_it_matters": why_it_matters,
        "required_evidence": required_evidence,
        "user_action": user_action,
        "automation_commands": automation_commands,
        "max_allowed_until_closed": max_allowed_until_closed,
        "source": source,
    }


def build_closure_items(
    audit: dict[str, Any],
    portfolio_package: dict[str, Any],
    calibration_package: dict[str, Any],
    paper: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = audit_check_map(audit)
    closure: list[dict[str, Any]] = []

    p0_gap_ids = ((portfolio_package.get("summary") or {}).get("p0_gap_ids") or [])
    p1_gap_ids = ((portfolio_package.get("summary") or {}).get("p1_gap_ids") or [])
    if p0_gap_ids or p1_gap_ids:
        closure.append(
            item(
                "portfolio_evidence_gaps",
                "P0" if p0_gap_ids else "P1",
                "持仓成本与现金通道",
                portfolio_package.get("status") or "unknown",
                "完整成本、lcETH 赎回条款和券商现金会影响仓位、收益口径和美股战术 sizing。",
                [
                    "lcETH Coinbase staking/redeem export or screenshot",
                    "ADA/SOL Binance/Ledger trade and staking reward lots",
                    "US equity broker settled cash and buying power timestamp",
                ],
                "补齐模板里的字段，尤其是 lcETH 成本/赎回与美股券商现金。",
                [
                    "python3 manual-investment-strategy-operator/scripts/portfolio_evidence_gap_packager.py --templates-dir manual-investment-strategy-operator/import_templates --markdown-output manual-investment-strategy-operator/reports/latest_portfolio_evidence_gap_package.md",
                    "python3 manual-investment-strategy-operator/scripts/cost_basis_reconciliation_audit.py --format json",
                ],
                "conditional_action_or_watch",
                "portfolio_evidence_gap_packager",
            )
        )

    gate = calibration_package.get("calibration_gate") or {}
    if int(gate.get("resolved_needed_after_ready_confirmations") or 0) > 0:
        closure.append(
            item(
                "recommendation_probability_calibration",
                "P0",
                "推荐结果校准",
                "insufficient_resolved_outcomes",
                "没有足够 hit/failed 结果，就不能把 80% 写成真实概率，只能当成观察或条件动作。",
                [
                    "At least 10 hit/failed recommendation outcomes",
                    "Price-window evidence for each reviewed recommendation",
                    "Human confirmation before writing outcome review",
                ],
                "等建议到期后逐条确认结果；不要把 superseded 自动算作命中或失败。",
                [
                    "python3 manual-investment-strategy-operator/scripts/recommendation_calibration_packager.py --markdown-output manual-investment-strategy-operator/reports/latest_recommendation_calibration_package.md",
                    "python3 manual-investment-strategy-operator/scripts/recommendation_outcome_reviewer.py --format json",
                ],
                "paper_only_or_conditional_action",
                "recommendation_calibration_packager",
            )
        )

    closed_needed = max(0, 20 - int(paper.get("closed_count") or 0))
    if closed_needed:
        closure.append(
            item(
                "paper_validation_sample_size",
                "P0",
                "短线策略模拟验证",
                "insufficient_closed_paper_trades",
                "美股/crypto 短线策略需要足够模拟样本，才有资格从 paper_only 晋级。",
                [
                    f"{closed_needed} more closed paper trades",
                    "Net return and drawdown summary",
                    "Walk-forward target research pass",
                ],
                "继续运行 active-alpha paper monitor，让候选经历完整入场、退出和复盘。",
                [
                    SAFE_PAPER_VALIDATION_PULSE_COMMAND,
                    "python3 manual-investment-strategy-operator/scripts/strategy_promotion_evaluator.py --format json",
                ],
                "paper_only",
                "active_alpha_paper_ledger",
            )
        )

    research_check = checks.get("research_committee_gate") or {}
    if research_check.get("severity") == "warning":
        closure.append(
            item(
                "research_committee_evidence_quality",
                "P1",
                "多研究员证据质量",
                "research_committee_degraded",
                "如果 Research Committee 缺少外部角色或高质量来源，主报告不能输出新的 execute_now。",
                [
                    "At least 6 evidence-verified roles",
                    "At least 6 roles with ok sources",
                    "Explicit bull/base/bear and disconfirming evidence",
                ],
                "使用 evidence backlog 生成的任务包补采各角色独立证据。",
                [
                    "python3 manual-investment-strategy-operator/scripts/research_evidence_backlog_builder.py --format json",
                    "python3 manual-investment-strategy-operator/scripts/research_subagent_task_packager.py --format json",
                ],
                "watch_or_conditional_action",
                "goal_system_completion_audit",
            )
        )

    outcome_check = checks.get("goal_outcome_not_yet_verified") or {}
    if outcome_check:
        closure.append(
            item(
                "financial_goal_not_yet_achieved",
                "P2",
                "长期目标状态",
                "not_yet_achieved",
                "5年/10年 10x 是目标路径，不是已经发生的事实；系统只能追踪目标差距和策略质量。",
                [
                    "Future portfolio value showing actual 10x",
                    "Ongoing monthly DCA records",
                    "Periodic goal path recalculation",
                ],
                "保持目标审计活跃，每次报告后继续追踪真实资产变化。",
                [
                    "python3 manual-investment-strategy-operator/scripts/goal_path_projection.py --format json",
                    "python3 manual-investment-strategy-operator/scripts/goal_system_completion_audit.py --format json",
                ],
                "goal_tracking_only",
                "goal_system_completion_audit",
            )
        )

    return closure


def build_package(
    audit_path: Path,
    ledger_path: Path,
    portfolio_ledger_path: Path,
    paper_ledger_path: Path,
    templates_dir: Path | None,
    as_of: dt.datetime,
) -> dict[str, Any]:
    audit = load_json_if_exists(audit_path)
    portfolio_package = PORTFOLIO_PACKAGER.build_package(
        ledger_path=portfolio_ledger_path,
        templates_dir=templates_dir,
    )
    calibration_package = CALIBRATION_PACKAGER.build_package(
        ledger_path=ledger_path,
        as_of=as_of,
        fetch_market_data=False,
        max_items=20,
        min_calibration_resolved=10,
    )
    paper = paper_metrics(paper_ledger_path)
    closure_items = build_closure_items(audit, portfolio_package, calibration_package, paper)
    readiness = (audit.get("summary") or {})
    return {
        "generated_at": utc_now(),
        "package_version": "goal-evidence-closure-package-v1",
        "read_only": True,
        "mutates_ledger": False,
        "live_orders_enabled": False,
        "human_confirmation_required": True,
        "inputs": {
            "goal_system_completion_audit": str(audit_path),
            "recommendation_ledger": str(ledger_path),
            "portfolio_ledger": str(portfolio_ledger_path),
            "paper_ledger": str(paper_ledger_path),
        },
        "readiness_summary": {
            "system_plan_implemented": readiness.get("system_plan_implemented"),
            "ready_for_manual_reports": readiness.get("ready_for_manual_reports"),
            "ready_for_crypto_dca_guidance": readiness.get("ready_for_crypto_dca_guidance"),
            "ready_for_tactical_execute_now": readiness.get("ready_for_tactical_execute_now"),
            "ready_for_auto_live_trading": readiness.get("ready_for_auto_live_trading"),
            "objective_financial_outcome_verified": readiness.get("objective_financial_outcome_verified"),
            "goal_complete": readiness.get("goal_complete"),
            "max_allowed_current_action": readiness.get("max_allowed_current_action"),
        },
        "source_packages": {
            "portfolio_evidence_gap_count": (portfolio_package.get("summary") or {}).get("evidence_gap_count"),
            "portfolio_blocking_gap_count": (portfolio_package.get("summary") or {}).get("blocking_gap_count"),
            "calibration_current_resolved": (calibration_package.get("calibration_gate") or {}).get("current_resolved"),
            "calibration_resolved_needed": (calibration_package.get("calibration_gate") or {}).get("resolved_needed_after_ready_confirmations"),
            "paper_closed_trades": paper.get("closed_count"),
            "paper_open_trades": paper.get("open_count"),
        },
        "closure_items": closure_items,
        "user_action_checklist": build_user_action_checklist(closure_items),
        "automation_commands": sorted({cmd for entry in closure_items for cmd in entry.get("automation_commands") or []}),
        "plain_term_notes": {
            "cost_lot": "一笔成交记录，包括数量、价格、时间、手续费和来源。",
            "broker_verified_cash": "券商账户带时间戳的 settled cash / buying power，不是口述现金。",
            "paper_validation": "模拟交易验证策略，不动真钱。",
            "calibration": "把历史预测和实际结果对齐，检查所谓 80% 概率是否可信。",
            "action_ceiling": "本轮允许的最大动作等级，不等于收益预测。",
        },
    }


def build_user_action_checklist(closure_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checklist: list[dict[str, Any]] = []
    for entry in closure_items:
        checklist.append(
            {
                "priority": entry.get("priority"),
                "area": entry.get("area"),
                "action": entry.get("user_action"),
                "required_evidence": entry.get("required_evidence"),
            }
        )
    return checklist


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(cell(part) for part in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    readiness = payload.get("readiness_summary") or {}
    source = payload.get("source_packages") or {}
    rows = [
        [
            entry.get("priority"),
            entry.get("area"),
            entry.get("blocker_id"),
            entry.get("status"),
            entry.get("max_allowed_until_closed"),
            entry.get("user_action"),
        ]
        for entry in payload.get("closure_items") or []
    ]
    command_rows = [[cmd] for cmd in payload.get("automation_commands") or []]
    notes = payload.get("plain_term_notes") or {}
    return "\n".join([
        "# Goal Evidence Closure Package",
        "",
        "本包只用于关闭证据缺口，不自动下单、不移动资金、不修改账本。",
        "",
        "## Readiness",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["system_plan_implemented", readiness.get("system_plan_implemented")],
                ["ready_for_manual_reports", readiness.get("ready_for_manual_reports")],
                ["ready_for_crypto_dca_guidance", readiness.get("ready_for_crypto_dca_guidance")],
                ["ready_for_tactical_execute_now", readiness.get("ready_for_tactical_execute_now")],
                ["goal_complete", readiness.get("goal_complete")],
                ["max_allowed_current_action", readiness.get("max_allowed_current_action")],
            ],
        ),
        "",
        "## Source Package Summary",
        "",
        markdown_table(
            ["Metric", "Value"],
            [[key, value] for key, value in source.items()],
        ),
        "",
        "## Closure Items",
        "",
        markdown_table(
            ["Priority", "Area", "Blocker", "Status", "Max Allowed", "User Action"],
            rows,
        ) if rows else "No open closure items.",
        "",
        "## Read-Only Commands",
        "",
        markdown_table(["Command"], command_rows) if command_rows else "No commands generated.",
        "",
        "## 术语小注",
        "",
        "\n".join(f"- `{key}`: {value}" for key, value in notes.items()),
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Package unresolved goal evidence blockers")
    parser.add_argument("--audit-json", default=str(DEFAULT_AUDIT))
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--portfolio-ledger", default=str(DEFAULT_PORTFOLIO_LEDGER))
    parser.add_argument("--paper-ledger", default=str(DEFAULT_PAPER_LEDGER))
    parser.add_argument("--templates-dir", default="")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_package(
        audit_path=Path(args.audit_json),
        ledger_path=Path(args.recommendation_ledger),
        portfolio_ledger_path=Path(args.portfolio_ledger),
        paper_ledger_path=Path(args.paper_ledger),
        templates_dir=Path(args.templates_dir) if args.templates_dir else None,
        as_of=dt.datetime.now(dt.timezone.utc).replace(microsecond=0),
    )
    if args.output_json:
        write_json(Path(args.output_json), payload)
    if args.output_md:
        write_text(Path(args.output_md), render_markdown(payload))
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
