#!/usr/bin/env python3
"""Stable target-sprint sandbox cycle runner.

One entrypoint for automation:
1. Review existing independent sandbox positions.
2. If open slots remain, run the conservative recovery scout.
3. Summarize whether the Binance public-data paper flow is progressing toward
   the sandbox monthly compounding target.

No real orders. No private API. No main ledger mutation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


PRIMARY_ROOT = Path("/Users/vincentpan/Documents/investing/active-alpha-paper-monitor")
SHADOW_ROOT = Path("/private/tmp/active-alpha-paper-monitor-shadow/active-alpha-paper-monitor")
SCRIPT_ROOT = SHADOW_ROOT / "scripts"
LEDGER_REL = Path("paper_trades/target_sprint_sandbox_ledger.json")
LIVE_ORDERS_ENABLED = False
PRIVATE_API_USED = False
ALLOW_REAL_ORDERS = False
MAIN_LEDGER_MUTATED = False


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return now_utc().astimezone(timezone(timedelta(hours=8)))


def rid() -> str:
    return f"{now_local().strftime('%Y%m%d-%H%M%S')}-stable-target-sprint-cycle"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_child(args: List[str], timeout: int) -> Dict[str, Any]:
    started = now_utc()
    try:
        completed = subprocess.run(
            args,
            cwd=str(PRIMARY_ROOT.parent),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        payload = None
        if completed.stdout.strip().startswith("{"):
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError:
                payload = None
        return {
            "args": args,
            "started_at": iso(started),
            "finished_at": iso(now_utc()),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "payload": payload,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "args": args,
            "started_at": iso(started),
            "finished_at": iso(now_utc()),
            "returncode": "timeout",
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            "payload": None,
        }


def ledger_summary() -> Dict[str, Any]:
    ledger = read_json(SHADOW_ROOT / LEDGER_REL) or read_json(PRIMARY_ROOT / LEDGER_REL) or {}
    month = now_local().strftime("%Y-%m")
    baseline = (ledger.get("monthly_goal_baselines") or {}).get(month) or {}
    target = float(baseline.get("target_equity_usd") or 1000.0)
    equity = float(ledger.get("equity_usd") or 0.0)
    return {
        "cash_usd": ledger.get("cash_usd"),
        "equity_usd": ledger.get("equity_usd"),
        "open_count": len(ledger.get("open_positions") or []),
        "open_symbols": [item.get("symbol") for item in ledger.get("open_positions") or [] if isinstance(item, dict)],
        "closed_count": len(ledger.get("closed_trades") or []),
        "realized_pnl_usd": ledger.get("realized_pnl_usd"),
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
        "target_equity_usd": target,
        "gap_to_target_usd": round(max(0.0, target - equity), 6),
        "progress_to_target_pct": round(equity / target * 100.0, 6) if target else None,
        "live_orders_enabled": ledger.get("live_orders_enabled"),
        "private_api_used": ledger.get("private_api_used"),
        "main_ledger_mutated": ledger.get("main_ledger_mutated"),
    }


def render_report(payload: Dict[str, Any]) -> str:
    before = payload["ledger_before"]
    after = payload["ledger_after"]
    lines = [
        f"# Stable Target Sprint Cycle - {payload['created_at_local']}",
        "",
        "## 结论",
        f"- Review child: `{payload['review_child']['returncode']}`；Scout child: `{payload['scout_child']['returncode'] if payload.get('scout_child') else 'skipped'}`。",
        f"- Sandbox 权益：`${after.get('equity_usd')}`；目标 `${after.get('target_equity_usd')}`；缺口 `${after.get('gap_to_target_usd')}`。",
        f"- Open 仓：`{', '.join(after.get('open_symbols') or []) or 'none'}`；closed `{after.get('closed_count')}`。",
        f"- 本轮新增开仓：`{payload.get('opened_count', 0)}`；本轮关闭：`{payload.get('closed_count_delta', 0)}`。",
        f"- Target-sprint paper 容量上限 `{payload.get('max_open')}`，用于在现金闲置且信号通过时扩大模拟样本；不改变真实交易权限。",
        "- 当前仍不能证明完成复利目标；cycle 只证明自动模拟执行链路可继续运行。",
        "",
        "## 账户变化",
        f"- Before equity `${before.get('equity_usd')}` / after `${after.get('equity_usd')}`。",
        f"- Before open `{before.get('open_count')}` / after `{after.get('open_count')}`。",
        "",
        "## 安全边界",
        "- `live_orders_enabled=false`，`private_api_used=false`，`allow_real_orders=false`，`main_ledger_mutated=false`。",
        "- 只使用 Binance public market data；未调用账户、真实订单、提现、margin、futures、perpetual 或签名私有 API。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Stable target sprint cycle")
    parser.add_argument("--max-open", type=int, default=12)
    parser.add_argument("--max-new", type=int, default=2)
    parser.add_argument("--review-timeout", type=int, default=45)
    parser.add_argument("--scout-timeout", type=int, default=90)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    run = rid()
    before = ledger_summary()
    review_child = run_child(
        [sys.executable, str(SCRIPT_ROOT / "stable_target_sprint_sandbox_review.py"), "--format", "json"],
        timeout=args.review_timeout,
    )
    middle = ledger_summary()
    scout_child = None
    if int(middle.get("open_count") or 0) < args.max_open:
        scout_child = run_child(
            [
                sys.executable,
                str(SCRIPT_ROOT / "stable_recovery_sandbox_scout.py"),
                "--max-open",
                str(args.max_open),
                "--max-new",
                str(args.max_new),
                "--dynamic-universe",
                "--dynamic-top",
                "35",
                "--format",
                "json",
            ],
            timeout=args.scout_timeout,
        )
    after = ledger_summary()
    opened_count = 0
    if scout_child and isinstance(scout_child.get("payload"), dict):
        opened_count = len(scout_child["payload"].get("opened_positions") or [])
    payload = {
        "run_id": run,
        "created_at": iso(now_utc()),
        "created_at_local": now_local().isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "mode": "stable_target_sprint_cycle",
        "live_orders_enabled": LIVE_ORDERS_ENABLED,
        "private_api_used": PRIVATE_API_USED,
        "allow_real_orders": ALLOW_REAL_ORDERS,
        "main_ledger_mutated": MAIN_LEDGER_MUTATED,
        "ledger_before": before,
        "ledger_after_review": middle,
        "ledger_after": after,
        "review_child": review_child,
        "scout_child": scout_child,
        "max_open": args.max_open,
        "max_new": args.max_new,
        "opened_count": opened_count,
        "closed_count_delta": int(after.get("closed_count") or 0) - int(before.get("closed_count") or 0),
        "overall_status": "cycle_ran_target_not_proven",
        "target_complete": bool(float(after.get("equity_usd") or 0.0) >= float(after.get("target_equity_usd") or 1000.0)),
    }
    stamp = now_local().strftime("%Y-%m-%d-%H%M")
    experiment_rel = Path("experiments") / f"{run}.json"
    report_rel = Path("reports") / f"{stamp}-stable-target-sprint-cycle.md"
    payload["outputs"] = {
        "experiment": str(PRIMARY_ROOT / experiment_rel),
        "report": str(PRIMARY_ROOT / report_rel),
        "shadow_experiment": str(SHADOW_ROOT / experiment_rel),
        "shadow_report": str(SHADOW_ROOT / report_rel),
    }
    report = render_report(payload)
    write_json(SHADOW_ROOT / experiment_rel, payload)
    write_text(SHADOW_ROOT / report_rel, report)
    # Primary writes are best effort only; do not block the stable path.
    try:
        write_json(PRIMARY_ROOT / experiment_rel, payload)
        write_text(PRIMARY_ROOT / report_rel, report)
    except Exception as exc:  # noqa: BLE001
        payload["primary_write_warning"] = f"{type(exc).__name__}: {exc}"
        write_json(SHADOW_ROOT / experiment_rel, payload)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
