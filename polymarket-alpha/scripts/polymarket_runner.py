#!/usr/bin/env python3
"""One-cycle Polymarket public-data and paper-only orchestration runner."""
from __future__ import annotations

import argparse
import importlib.util
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load_module("polymarket_alpha_runner_core", ROOT / "scripts" / "polymarket_alpha.py")
public_data = load_module("polymarket_public_data_runner_core", ROOT / "scripts" / "polymarket_public_data.py")
evolver = load_module("polymarket_strategy_evolver_runner_core", ROOT / "scripts" / "polymarket_strategy_evolver.py")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_optional(path: str | None, default: Any) -> Any:
    if not path:
        return deepcopy(default)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def discovery_entry_contract(max_markets: int, manifest: dict[str, Any], verification: dict[str, Any]) -> tuple[bool, list[str]]:
    complete = (
        max_markets == 0 and manifest.get("data_status") == "ok"
        and verification.get("status") == "pass" and int(manifest.get("failed_request_count") or 0) == 0
        and manifest.get("terminal_cursor_proven") is True
    )
    blockers = []
    if manifest.get("data_status") != "ok" or verification.get("status") != "pass":
        blockers.append("public_snapshot_degraded_or_integrity_failed")
    if not complete:
        blockers.append("full_market_discovery_not_complete")
    return complete, blockers


def validate_model_governance(estimates: dict[str, Any], registry: dict[str, Any], approvals: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if approvals.get("paper_only") is not True or approvals.get("live_orders_enabled") is not False or approvals.get("private_api_used") is not False:
        raise ValueError("unsafe model approvals")
    if registry.get("paper_only") is not True or registry.get("live_orders_enabled") is not False or registry.get("private_api_used") is not False:
        raise ValueError("unsafe model registry")
    eligible=set(str(value) for value in registry.get("eligible_paper_model_versions",[]));approved=set(str(value) for value in approvals.get("approved_model_versions",[]));filtered={};rejections=[]
    for market_id,estimate in estimates.items():
        version=str((estimate or {}).get("model_version") or "");reasons=[]
        if not version:reasons.append("estimate_model_version_missing")
        if version not in eligible:reasons.append("model_not_eligible_in_registry")
        if version not in approved:reasons.append("manual_model_approval_missing")
        if reasons:rejections.append({"market_id":str(market_id),"model_version":version or None,"reasons":reasons})
        else:filtered[str(market_id)]=estimate
    blockers=["unapproved_or_ineligible_probability_estimates_present"] if rejections else []
    return filtered,{"status":"blocked" if blockers else "ok","estimates_received":len(estimates),"estimates_accepted":len(filtered),"rejections":rejections,"blockers":blockers,"eligible_registry_versions":sorted(eligible),"manually_approved_versions":sorted(approved),"paper_only":True,"live_orders_enabled":False,"private_api_used":False}


def open_market_data(ledger: dict[str, Any], books: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    resolutions: dict[str, Any] = {}
    market_states: dict[str, Any] = {}
    errors = []
    pending_counterfactuals = [
        row for row in ledger.get("closed_positions", [])
        if row.get("exit_reason") and row.get("counterfactual_status") == "pending"
    ]
    positions = list(ledger.get("open_positions", [])) + pending_counterfactuals
    fetched_markets: set[str] = set()
    for position in positions:
        market_id = str(position["market_id"])
        if market_id not in fetched_markets:
            fetched_markets.add(market_id)
            try:
                market = public_data.get_json(f"{public_data.GAMMA_BASE}/markets/{quote(market_id, safe='')}")
                market_states[market_id] = market
                inferred = public_data.infer_resolution(market)
                winner = inferred.get("winning_outcome")
                if winner is not None:
                    resolutions[market_id] = str(winner).upper()
            except Exception as exc:
                errors.append({"market_id": market_id, "endpoint": "gamma_market", "error": f"{type(exc).__name__}:{exc}"})
        token_id = str(position.get("token_id") or position.get("yes_token_id") or "")
        if token_id and token_id not in books:
            try:
                books[token_id] = public_data.get_json(f"{public_data.CLOB_BASE}/book?token_id={quote(token_id, safe='')}")
            except Exception as exc:
                errors.append({"market_id": market_id, "endpoint": "book", "error": f"{type(exc).__name__}:{exc}"})
    return resolutions, market_states, errors


def markdown_report(payload: dict[str, Any]) -> str:
    scan = payload["scan"]
    audit = payload["goal_audit"]
    lines = [
        "# Polymarket Paper Runner",
        "",
        f"- Run ID: `{payload['run_id']}`",
        f"- 状态: `{payload['status']}`",
        f"- 全市场扫描: {scan['markets_scanned']}",
        f"- 通过严格门: {len(scan['selected_candidates'])}",
        f"- 本轮模拟开仓: {len(payload['paper_entry']['opened'])}",
        f"- 本轮模拟退出: {len(payload['position_monitor']['exited'])}",
        f"- 本轮结算: {len(payload['settlement']['settled'])}",
        f"- 本轮反事实结算: {len(payload['settlement'].get('counterfactual_resolved', []))}",
        f"- 追加式持仓观察: {payload['ledger_after']['position_observation_count']}",
        f"- Open paper 仓: {payload['ledger_after']['open_count']}",
        f"- Closed paper 仓: {payload['ledger_after']['closed_count']}",
        f"- Paper 权益: ${payload['ledger_after']['equity_usd']:.2f}",
        f"- 有效策略版本: `{payload['effective_strategy_version']}`",
        f"- 模型治理: `{payload.get('model_governance',{}).get('status')}`",
        f"- 本轮学习变更: apply {len(payload['strategy_learning']['applied'])} / revert {len(payload['strategy_learning']['reverted'])}",
        f"- Goal complete: `{str(audit['goal_complete']).lower()}`",
        "",
        "## 安全边界",
        "",
        "- 只读取 Polymarket 官方公开 Gamma/CLOB 数据。",
        "- 不含账户、签名、订单、撤单、提现或私有 API。",
        "- 概率模型、来源、规则或盘口任一缺失时空仓。",
        "- Research Committee 当前降级；最高动作仅为 paper-only。",
        "",
        "## Top Pass 原因",
        "",
    ]
    for row in scan.get("all_decisions", [])[:10]:
        failures = ", ".join(row.get("failed_gates", [])) or "passed"
        lines.append(f"- `{row['market_id']}` [{row.get('side')}] {row.get('question')}: {failures}")
    return "\n".join(lines) + "\n"


def run_once(
    policy_path: Path, ledger_path: Path, overlay_path: Path, estimates: dict[str, Any], events: dict[str, Any],
    max_markets: int, book_limit: int, keyset_max_pages: int, keyset_wall_clock_seconds: float,
    snapshot_dir: Path, historical_replay: dict[str, Any],
    binance_metrics: dict[str, Any], dry_run: bool, model_governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_id = f"pm-run-{compact_time()}"
    base_policy = core.read_json(policy_path)
    if base_policy.get("paper_only") is not True or base_policy.get("live_orders_enabled") is not False:
        raise ValueError("unsafe policy")
    overlay_before = evolver.load_overlay(overlay_path, base_policy)
    policy = core.load_effective_policy(base_policy, overlay_before)
    manifest = public_data.build_snapshot(
        "live", max_markets, 100, book_limit,
        True, False, snapshot_dir, aux_market_ids=set(estimates),
        keyset_max_pages=keyset_max_pages, keyset_wall_clock_seconds=keyset_wall_clock_seconds,
    )
    verification = public_data.verify_manifest(snapshot_dir)
    markets = json.loads((snapshot_dir / "markets.json").read_text(encoding="utf-8"))
    books = json.loads((snapshot_dir / "books.json").read_text(encoding="utf-8"))
    real_ledger = core.load_ledger(ledger_path, policy)
    core.assert_ledger_safe(real_ledger)
    ledger = deepcopy(real_ledger)
    before = {
        "open_count": len(ledger.get("open_positions", [])),
        "closed_count": len(ledger.get("closed_positions", [])),
        "equity_usd": float(ledger.get("equity_usd", policy["initial_equity_usd"])),
    }
    resolutions, market_states, open_fetch_errors = open_market_data(ledger, books)
    observation_at = now_iso()
    counterfactual_monitor = core.monitor_pending_counterfactuals(ledger, books, policy, observed_at=observation_at)
    settlement = core.settle(ledger, resolutions, policy) if resolutions else {"settled": [], "pending": [row["paper_trade_id"] for row in ledger.get("open_positions", [])], "counterfactual_resolved": []}
    monitor_events = deepcopy(events)
    failed_market_ids = {str(row.get("market_id")) for row in open_fetch_errors}
    for position in ledger.get("open_positions", []):
        market_id = str(position["market_id"])
        state = deepcopy(monitor_events.get(market_id) or {})
        evidence_ids = list(state.get("evidence_ids") or [])
        if market_id in failed_market_ids:
            state["data_status"] = "missing"
            evidence_ids.append(f"{run_id}:public_market_fetch_error:{market_id}")
        if market_id not in estimates:
            state["data_status"] = "missing"
            evidence_ids.append(f"{run_id}:approved_estimate_missing:{market_id}")
        state["evidence_ids"] = sorted(set(str(value) for value in evidence_ids))
        monitor_events[market_id] = state
    monitor = core.monitor_positions(ledger, books, estimates, monitor_events, policy, observed_at=observation_at) if ledger.get("open_positions") else {"exited": [], "held": [], "degraded": []}
    learning_result = evolver.evolve(ledger, base_policy, overlay_before)
    overlay_after = learning_result["overlay"]
    policy = core.load_effective_policy(base_policy, overlay_after)
    scan = core.scan_payload(markets, books, estimates, policy, float(ledger.get("equity_usd", policy["initial_equity_usd"])))
    market_discovery_complete, entry_blockers = discovery_entry_contract(max_markets, manifest, verification)
    governance_blockers=list((model_governance or {}).get("blockers",[]));entry_blockers.extend(governance_blockers)
    runner_status = "blocked_model_governance" if governance_blockers else ("ok" if market_discovery_complete else "degraded_no_new_entry")
    if entry_blockers:
        entry = {"opened": [], "blocked": entry_blockers}
    else:
        entry = core.enter_scan(scan, ledger, policy)
    ledger["events"].append({
        "at": observation_at, "type": "paper_observation_cycle", "run_id": run_id,
        "runner_status": runner_status,
        "markets_scanned": scan["markets_scanned"], "full_market_requested": max_markets == 0,
        "market_discovery_complete": market_discovery_complete,
        "snapshot_integrity_passed": verification.get("status") == "pass",
        "new_entry_blocked": bool(entry_blockers), "paper_only": True,
        "live_orders_enabled": False, "private_api_used": False,
    })
    ledger["updated_at"] = observation_at
    after = {
        "open_count": len(ledger.get("open_positions", [])),
        "closed_count": len(ledger.get("closed_positions", [])),
        "paper_order_count": len(ledger.get("paper_orders", [])),
        "equity_usd": float(ledger.get("equity_usd", policy["initial_equity_usd"])),
        "cash_usd": float(ledger.get("cash_usd", policy["initial_equity_usd"])),
        "position_observation_count": len(ledger.get("position_observations", [])),
        "pending_counterfactual_count": sum(
            row.get("counterfactual_status") == "pending" for row in ledger.get("closed_positions", [])
        ),
    }
    goal_audit = core.audit_ledger(ledger, binance_metrics, historical_replay)
    status = runner_status
    payload = {
        "schema_version": "polymarket-runner-v1", "run_id": run_id, "created_at": now_iso(),
        "status": status, "dry_run": dry_run, "paper_only": True,
        "live_orders_enabled": False, "private_api_used": False,
        "snapshot_manifest": manifest, "snapshot_verification": verification,
        "open_position_market_states": market_states, "open_position_fetch_errors": open_fetch_errors,
        "settlement": settlement, "position_monitor": monitor,
        "counterfactual_monitor": counterfactual_monitor, "scan": scan, "paper_entry": entry,
        "strategy_learning": learning_result["summary"],
        "model_governance": model_governance or {"status":"not_provided","blockers":[]},
        "effective_strategy_version": policy["effective_strategy_version"],
        "overlay_change_ids": policy["overlay_change_ids"],
        "ledger_before": before, "ledger_after": after,
        "research_panel_missing": True,
        "research_panel_missing_reason": "subagent_research_not_requested_or_attached_for_this_runner",
        "research_committee_degraded": True, "max_allowed_action": "paper_only",
        "goal_audit": goal_audit,
    }
    if not dry_run:
        core.write_json(ledger_path, ledger)
        core.write_json(overlay_path, overlay_after)
    return payload


def self_test() -> dict[str, Any]:
    policy = core.read_json(ROOT / "config" / "policy.json")
    scan = core.scan_payload([], {}, {}, policy)
    assert scan["selected_candidates"] == []
    ledger = core.new_ledger(policy)
    assert ledger["live_orders_enabled"] is False and ledger["private_api_used"] is False
    approvals={"approved_model_versions":[],"paper_only":True,"live_orders_enabled":False,"private_api_used":False};registry={"eligible_paper_model_versions":[],"paper_only":True,"live_orders_enabled":False,"private_api_used":False}
    filtered,governance=validate_model_governance({"m":{"model_version":"failed-v1"}},registry,approvals);assert filtered=={} and governance["status"]=="blocked"
    return {"status": "pass", "tests": ["empty_scan_no_entry", "paper_only_ledger", "unapproved_model_estimate_blocked"], "live_orders_enabled": False, "private_api_used": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket one-cycle paper runner")
    parser.add_argument("--policy", default=str(ROOT / "config" / "policy.json"))
    parser.add_argument("--ledger", default=str(ROOT / "data" / "paper_ledger.json"))
    parser.add_argument("--overlay", default=str(ROOT / "config" / "paper_strategy_overlay.json"))
    parser.add_argument("--estimates-json")
    parser.add_argument("--model-registry", default=str(ROOT / "experiments" / "current-model-registry.json"))
    parser.add_argument("--model-approvals", default=str(ROOT / "config" / "model_approvals.json"))
    parser.add_argument("--events-json")
    parser.add_argument("--historical-replay-json", default=str(ROOT / "experiments" / "20260711-historical-market-baseline.json"))
    parser.add_argument("--binance-metrics-json", default=str(ROOT / "experiments" / "current-binance-30d-windows.json"))
    parser.add_argument("--max-markets", type=int, default=0, help="0 scans all active markets, capped internally at 10000")
    parser.add_argument("--book-limit", type=int, default=50, help="fetch books for top markets plus every market with an estimate")
    parser.add_argument("--keyset-max-pages", type=int, default=50)
    parser.add_argument("--keyset-wall-clock-seconds", type=float, default=90.0)
    parser.add_argument("--snapshot-dir")
    parser.add_argument("--output")
    parser.add_argument("--report")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    timestamp = compact_time()
    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else ROOT / "cache" / "runs" / timestamp
    output = Path(args.output) if args.output else ROOT / "experiments" / f"{timestamp}-polymarket-runner.json"
    report = Path(args.report) if args.report else ROOT / "reports" / f"{timestamp}-polymarket-runner.md"
    raw_estimates=load_optional(args.estimates_json, {});approved_estimates,model_governance=validate_model_governance(raw_estimates,load_optional(args.model_registry, {}),load_optional(args.model_approvals, {}))
    payload = run_once(
        Path(args.policy), Path(args.ledger), Path(args.overlay), approved_estimates, load_optional(args.events_json, {}),
        args.max_markets, args.book_limit, args.keyset_max_pages, args.keyset_wall_clock_seconds,
        snapshot_dir, load_optional(args.historical_replay_json, {}),
        load_optional(args.binance_metrics_json, {}), args.dry_run, model_governance,
    )
    atomic_json(output, payload)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(markdown_report(payload), encoding="utf-8")
    print(json.dumps({
        "run_id": payload["run_id"], "status": payload["status"],
        "markets_scanned": payload["scan"]["markets_scanned"],
        "selected_candidates": len(payload["scan"]["selected_candidates"]),
        "paper_opened": payload["paper_entry"]["opened"], "paper_exited": payload["position_monitor"]["exited"],
        "goal_complete": payload["goal_audit"]["goal_complete"],
        "dry_run": args.dry_run, "live_orders_enabled": False,
        "output": str(output), "report": str(report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
