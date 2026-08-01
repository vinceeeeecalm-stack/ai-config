#!/usr/bin/env python3
"""Generate the strict Polymarket/Binance same-capital 30-day comparison artifact."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_core():
    path = ROOT / "scripts" / "polymarket_alpha.py"
    spec = importlib.util.spec_from_file_location("polymarket_window_core", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load_core()


def markdown(payload: dict) -> str:
    lines = [
        "# Polymarket / Binance Same-Window Comparison",
        "",
        f"- Comparison status: `{payload['comparison_status']}`",
        f"- Valid exact 30-day windows: {payload['valid_window_count']}",
        f"- Best consecutive wins: {payload['best_consecutive_windows_beating_binance']}",
        f"- Aggregate ROI advantage: {payload['aggregate_roi_advantage_pct_points']}",
        "",
        "A legacy or unmatched Binance ROI is never accepted as same-window evidence.",
        "Each valid window requires exact 30-day boundaries, identical initial capital,",
        "complete friction fields, and no unmarked position crossing either boundary.",
        "",
        "## Windows",
        "",
    ]
    if not payload["windows"]:
        lines.append("- No same-window Binance evidence supplied.")
    for row in payload["windows"]:
        failures = ", ".join(row["failed_gates"]) or "none"
        lines.append(
            f"- `{row.get('window_id')}` valid={str(row['valid_same_window']).lower()} "
            f"PM={row['polymarket_net_roi_pct']}% Binance={row['binance_net_roi_pct']}% "
            f"delta={row['roi_delta_pct_points']}pp; failures: {failures}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict 30-day same-window paper comparator")
    parser.add_argument("--policy", default=str(ROOT / "config" / "policy.json"))
    parser.add_argument("--ledger", default=str(ROOT / "data" / "paper_ledger.json"))
    parser.add_argument("--binance-windows", default=str(ROOT / "experiments" / "current-binance-30d-windows.json"))
    parser.add_argument("--output", default=str(ROOT / "experiments" / "current-same-window-comparison.json"))
    parser.add_argument("--report", default=str(ROOT / "reports" / "CURRENT_SAME_WINDOW_COMPARISON.md"))
    args = parser.parse_args()
    policy = core.read_json(args.policy)
    ledger = core.load_ledger(args.ledger, policy)
    core.assert_ledger_safe(ledger)
    comparison = core.compare_30d_windows(ledger, core.read_json(args.binance_windows))
    payload = {
        **comparison, "paper_only": True, "live_orders_enabled": False,
        "private_api_used": False, "goal_gate_passed": (
            comparison["best_consecutive_windows_beating_binance"] >= 3
            and comparison.get("aggregate_roi_advantage_pct_points") is not None
            and comparison["aggregate_roi_advantage_pct_points"] >= 5
        ),
    }
    core.write_json(args.output, payload)
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(markdown(payload), encoding="utf-8")
    temp.replace(target)
    print(json.dumps({
        "comparison_status": payload["comparison_status"], "valid_window_count": payload["valid_window_count"],
        "best_consecutive_windows_beating_binance": payload["best_consecutive_windows_beating_binance"],
        "goal_gate_passed": payload["goal_gate_passed"], "output": args.output, "report": args.report,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
