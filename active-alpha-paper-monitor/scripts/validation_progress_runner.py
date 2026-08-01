#!/usr/bin/env python3
"""Orchestrate paper validation runs and show evidence progress.

This runner is intentionally boring and conservative: it only calls existing
paper/validation scripts, records their outputs, and compares the validation
sample audit before and after the cycle. It never enables live orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
DURABLE_KLINE_CACHE_ROOT = ACTIVE_ROOT / "cache" / "binance_klines"
DEFAULT_DYNAMIC_SCAN_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "TRXUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "SUIUSDT",
    "WLDUSDT",
    "NEARUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "AAVEUSDT",
    "INJUSDT",
    "SEIUSDT",
    "FETUSDT",
    "RENDERUSDT",
    "PEPEUSDT",
]
DEFAULT_VALIDATION_DYNAMIC_MAX_SYMBOLS = 18
CORE_LIQUIDITY_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
MARKET_MOOD_ANCHOR_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
STABLE_OR_FIAT_BASES = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "USDD",
    "USDE",
    "USDS",
    "USD1",
    "RLUSD",
    "EURI",
    "USTC",
    "PYUSD",
    "DAI",
    "BUSD",
    "EUR",
    "TRY",
    "BRL",
    "AUD",
    "GBP",
}
LEVERAGED_TOKEN_MARKERS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
SYMBOL_BUCKETS = {
    "BTCUSDT": "core_store_of_value",
    "ETHUSDT": "core_yield_beta",
    "BNBUSDT": "core_exchange",
    "SOLUSDT": "large_cap_beta",
    "XRPUSDT": "large_cap_payment",
    "ADAUSDT": "large_cap_l1",
    "TRXUSDT": "defensive_cashflow_chain",
    "LINKUSDT": "infrastructure",
    "AVAXUSDT": "large_cap_l1",
    "AAVEUSDT": "defi_bluechip",
    "SUIUSDT": "high_beta_l1",
    "INJUSDT": "high_beta_defi",
    "SEIUSDT": "high_beta_l1",
    "WLDUSDT": "high_beta_ai",
    "FETUSDT": "high_beta_ai",
    "RENDERUSDT": "high_beta_ai",
    "DOGEUSDT": "meme_liquid",
    "PEPEUSDT": "meme_liquid",
}
HIGH_BETA_BUCKETS = {"large_cap_beta", "high_beta_l1", "high_beta_defi", "high_beta_ai", "meme_liquid"}
CORE_DEFENSIVE_BUCKETS = {"core_store_of_value", "core_yield_beta", "core_exchange", "defensive_cashflow_chain"}
SOCIAL_TAG_ALIASES = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "ADA": "ADAUSDT",
    "TRX": "TRXUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "SUI": "SUIUSDT",
    "WLD": "WLDUSDT",
    "NEAR": "NEARUSDT",
    "LINK": "LINKUSDT",
    "AVAX": "AVAXUSDT",
    "AAVE": "AAVEUSDT",
    "INJ": "INJUSDT",
    "SEI": "SEIUSDT",
    "FET": "FETUSDT",
    "RENDER": "RENDERUSDT",
    "RNDR": "RENDERUSDT",
    "PEPE": "PEPEUSDT",
    "BNB": "BNBUSDT",
}
BINANCE_PUBLIC_HOSTS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
SHARED_PAPER_LOCK_PATH = Path(
    os.environ.get("ACTIVE_ALPHA_SHARED_PAPER_LOCK_PATH", "/private/tmp/active_alpha_sunday_crypto_realistic_paper.lock")
)
SHARED_PAPER_LOCK_STALE_SECONDS = 55 * 60


def local_now() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def artifact_stamp(now: dt.datetime) -> str:
    """Return a per-invocation stamp so repeated runs cannot overwrite evidence."""
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def relative_or_absolute(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def shared_lock_pid_state(content: str) -> dict[str, Any]:
    raw = str(content or "").strip().split()[0] if str(content or "").strip() else ""
    try:
        pid = int(raw)
    except ValueError:
        return {"status": "unknown", "pid": None, "reason": "lock_content_does_not_start_with_pid"}
    if pid <= 0:
        return {"status": "unknown", "pid": pid, "reason": "lock_pid_not_positive"}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"status": "dead", "pid": pid, "reason": "lock_pid_not_running"}
    except PermissionError:
        return {"status": "alive", "pid": pid, "reason": "lock_pid_exists_permission_denied"}
    except OSError as exc:
        return {"status": "unknown", "pid": pid, "reason": f"{type(exc).__name__}: {exc}"}
    return {"status": "alive", "pid": pid, "reason": "lock_pid_running"}


def shared_paper_lock_state() -> dict[str, Any]:
    if not SHARED_PAPER_LOCK_PATH.exists():
        return {"status": "clear", "path": str(SHARED_PAPER_LOCK_PATH)}
    try:
        stat = SHARED_PAPER_LOCK_PATH.stat()
        age_seconds = max(0.0, time.time() - stat.st_mtime)
    except OSError:
        age_seconds = None
    try:
        content = SHARED_PAPER_LOCK_PATH.read_text(encoding="utf-8")[:500]
    except Exception as exc:  # noqa: BLE001
        content = f"unreadable_lock_content: {type(exc).__name__}: {exc}"
    pid_state = shared_lock_pid_state(content)
    stale_reason = None
    if age_seconds is not None and age_seconds >= SHARED_PAPER_LOCK_STALE_SECONDS:
        stale_reason = "lock_age_exceeds_threshold"
    elif pid_state.get("status") == "dead":
        stale_reason = "lock_pid_not_running"
    is_stale = stale_reason is not None
    return {
        "status": "stale" if is_stale else "held",
        "path": str(SHARED_PAPER_LOCK_PATH),
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "stale_after_seconds": SHARED_PAPER_LOCK_STALE_SECONDS,
        "content_preview": content,
        "pid_state": pid_state,
        "stale_reason": stale_reason,
        "operator_note": (
            "The shared paper loop lock is stale. The runner may remove it before starting."
            if is_stale
            else (
                "A paper loop lock is already present. This runner will not start ledger-mutating paper child tasks; "
                "wait for the active automation to finish, then retry."
            )
        ),
    }


def clear_stale_shared_paper_lock(lock_state: dict[str, Any]) -> dict[str, Any]:
    if lock_state.get("status") != "stale":
        return {"status": "skipped_not_stale", "lock_state": lock_state}
    try:
        SHARED_PAPER_LOCK_PATH.unlink()
    except FileNotFoundError:
        return {"status": "already_clear", "lock_state": lock_state}
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "lock_state": lock_state,
            "error": f"{type(exc).__name__}: {exc}",
            "operator_note": "Could not remove stale shared paper lock; runner must skip ledger-mutating children.",
        }
    return {
        "status": "removed_stale_lock",
        "lock_state": lock_state,
        "operator_note": "Removed stale shared paper lock before starting the validation runner.",
    }


def render_lock_held_report(run: dict[str, Any]) -> str:
    lock_state = run.get("shared_paper_lock") or {}
    return "\n".join(
        [
            f"# Validation Progress Runner | {run['run_id']}",
            "",
            "This paper validation runner skipped execution because another paper automation already holds the shared ledger lock.",
            "",
            "## Summary",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| Status | `{run['status']}` |",
            f"| Live orders enabled | `{run['live_orders_enabled']}` |",
            f"| Private API keys used | `{run['private_api_keys_used']}` |",
            f"| Dry run | `{run['dry_run']}` |",
            f"| Lock path | `{lock_state.get('path')}` |",
            f"| Lock age seconds | `{lock_state.get('age_seconds')}` |",
            "",
            "## Operator Note",
            "",
            str(lock_state.get("operator_note") or "Existing paper automation is active; retry after the lock clears."),
            "",
        ]
    )


def emit_lock_held_run(args: argparse.Namespace, now: dt.datetime, run_id: str) -> int:
    lock_state = shared_paper_lock_state()
    date = now.strftime("%Y-%m-%d")
    report_path = ACTIVE_ROOT / "reports" / f"{date}-validation-progress-runner-{run_id}.md"
    experiment_path = ACTIVE_ROOT / "experiments" / f"{run_id}-validation-progress-runner.json"
    run = {
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "local_time": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "runner_version": "validation-progress-runner-v1",
        "status": "skipped_lock_held",
        "live_orders_enabled": False,
        "private_api_keys_used": False,
        "dry_run": args.dry_run,
        "offline_fixture": args.offline_fixture,
        "shared_paper_lock": lock_state,
        "dynamic_scan_pool_enabled": False,
        "dynamic_scan_universe": {
            "status": "skipped_lock_held",
            "reason": "shared_paper_lock_held_before_runner_start",
        },
        "child_runs": [],
        "child_failures": [],
        "safety_errors": [],
        "outputs": {
            "report": relative_or_absolute(report_path),
            "experiment": relative_or_absolute(experiment_path),
        },
    }
    if not args.dry_run:
        write_json(experiment_path, run)
        write_text(report_path, render_lock_held_report(run))
    output_payload = {
        "run_id": run["run_id"],
        "status": run["status"],
        "live_orders_enabled": False,
        "private_api_used": False,
        "private_api_keys_used": False,
        "allow_real_orders": False,
        "dry_run": run["dry_run"],
        "shared_paper_lock": lock_state,
        "child_runs": [],
        "child_failures": [],
        "safety_errors": [],
        "outputs": run["outputs"],
    }
    if args.format == "markdown":
        print(render_lock_held_report(run))
    else:
        print(json.dumps(output_payload if args.compact_output else run, ensure_ascii=False, indent=2))
    return 0


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def split_symbols(value: str | None) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for raw in (value or "").split(","):
        symbol = raw.strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


def public_json(path: str, timeout_seconds: int = 18) -> tuple[Any | None, dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for host in BINANCE_PUBLIC_HOSTS:
        url = f"{host}{path}"
        connect_timeout = str(max(1, min(2, int(timeout_seconds))))
        try:
            result = subprocess.run(
                [
                    "curl",
                    "--compressed",
                    "--connect-timeout",
                    connect_timeout,
                    "--max-time",
                    str(timeout_seconds),
                    "--retry",
                    "0",
                    "-sS",
                    url,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            attempts.append({"status": "failed", "url": url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if result.returncode != 0:
            attempts.append(
                {
                    "status": "failed",
                    "url": url,
                    "returncode": result.returncode,
                    "error": result.stderr.strip() or f"returncode={result.returncode}",
                    "bytes_received": len(result.stdout or ""),
                }
            )
            continue
        try:
            return json.loads(result.stdout), {
                "status": "ok",
                "url": url,
                "base_url": host,
                "attempt_count": len(attempts) + 1,
                "fallback_failure_count": len(attempts),
                "attempts": attempts,
            }
        except json.JSONDecodeError as exc:
            attempts.append(
                {
                    "status": "failed",
                    "url": url,
                    "error": f"JSONDecodeError: {exc}",
                    "body": result.stdout[:160],
                    "bytes_received": len(result.stdout or ""),
                }
            )
    return None, {
        "status": "failed",
        "path": path,
        "base_urls": BINANCE_PUBLIC_HOSTS,
        "attempt_count": len(attempts),
        "fallback_failure_count": len(attempts),
        "attempts": attempts,
    }


def fetch_anchor_change(symbol: str, interval: str, limit: int = 5) -> dict[str, Any]:
    payload, meta = public_json(f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", timeout_seconds=3)
    if not isinstance(payload, list) or len(payload) < 2:
        return {"symbol": symbol, "interval": interval, "status": "missing", "meta": meta}
    try:
        first_open = as_float(payload[0][1], None)
        last_close = as_float(payload[-1][4], None)
        last_open = as_float(payload[-1][1], None)
    except (IndexError, TypeError):
        return {"symbol": symbol, "interval": interval, "status": "malformed", "meta": meta}
    if not first_open or not last_close or not last_open:
        return {"symbol": symbol, "interval": interval, "status": "malformed_price", "meta": meta}
    window_change = (last_close / first_open - 1.0) * 100.0
    current_candle_change = (last_close / last_open - 1.0) * 100.0
    return {
        "symbol": symbol,
        "interval": interval,
        "status": "ok",
        "window_change_pct": round(window_change, 6),
        "current_candle_change_pct": round(current_candle_change, 6),
        "bars": len(payload),
        "meta": meta,
    }


def build_short_term_anchor_profile() -> dict[str, Any]:
    """Use liquid anchors as a short-horizon market atmosphere check."""
    rows: list[dict[str, Any]] = []
    for symbol in MARKET_MOOD_ANCHOR_SYMBOLS:
        for interval, limit in (("1h", 5), ("4h", 4)):
            rows.append(fetch_anchor_change(symbol, interval, limit=limit))
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    one_hour = [as_float(row.get("window_change_pct"), 0.0) or 0.0 for row in ok_rows if row.get("interval") == "1h"]
    four_hour = [as_float(row.get("window_change_pct"), 0.0) or 0.0 for row in ok_rows if row.get("interval") == "4h"]
    avg_1h = sum(one_hour) / len(one_hour) if one_hour else 0.0
    avg_4h = sum(four_hour) / len(four_hour) if four_hour else 0.0
    positive_1h_pct = (sum(1 for value in one_hour if value > 0) / len(one_hour) * 100.0) if one_hour else 0.0
    positive_4h_pct = (sum(1 for value in four_hour if value > 0) / len(four_hour) * 100.0) if four_hour else 0.0
    failed_rows = [row for row in rows if row.get("status") != "ok"]
    if not ok_rows:
        state = "unavailable"
        overlay = "short_term_anchor_unavailable"
    elif avg_1h >= 0.45 and avg_4h >= 0.35 and positive_1h_pct >= 75.0:
        state = "risk_appetite_accelerating"
        overlay = "expand_high_beta_if_24h_breadth_confirms"
    elif avg_1h <= -0.45 and avg_4h <= -0.35 and positive_1h_pct <= 25.0:
        state = "risk_appetite_fading"
        overlay = "contract_width_and_raise_core_floor"
    elif avg_1h > 0.25 and avg_4h < 0:
        state = "rebound_attempt"
        overlay = "watch_liquid_rebound_without_chasing"
    elif avg_1h < -0.25 and avg_4h > 0:
        state = "pullback_after_strength"
        overlay = "avoid_late_high_beta_chase"
    else:
        state = "neutral"
        overlay = "no_short_term_override"
    return {
        "status": "ok" if ok_rows else "degraded",
        "state": state,
        "overlay": overlay,
        "avg_1h_anchor_change_pct": round(avg_1h, 6),
        "avg_4h_anchor_change_pct": round(avg_4h, 6),
        "positive_1h_anchor_pct": round(positive_1h_pct, 6),
        "positive_4h_anchor_pct": round(positive_4h_pct, 6),
        "ok_count": len(ok_rows),
        "failed_count": len(failed_rows),
        "anchors": ok_rows,
        "failures": failed_rows[:4],
    }


def load_open_symbols() -> list[str]:
    ledger_path = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    symbols: list[str] = []
    seen: set[str] = set()
    for position in ledger.get("open_positions") or []:
        symbol = str(position.get("symbol") or "").strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


def latest_social_handoff() -> Path | None:
    paths = sorted((ACTIVE_ROOT / "handoffs").glob("*social-key-person-intel-handoff.json"))
    return paths[-1] if paths else None


def social_handoff_state(max_age_hours: float = 12.0) -> dict[str, Any]:
    path = latest_social_handoff()
    if not path:
        return {
            "status": "missing",
            "should_refresh": True,
            "latest_path": None,
            "age_hours": None,
            "max_age_hours": max_age_hours,
            "reason": "no_social_key_person_handoff_found",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unreadable",
            "should_refresh": True,
            "latest_path": str(path),
            "age_hours": None,
            "max_age_hours": max_age_hours,
            "reason": f"social_handoff_unreadable: {exc}",
        }
    created_at = payload.get("created_at")
    try:
        created = dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        age = (dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - created).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        age = max_age_hours + 1
    is_stale = age > max_age_hours
    return {
        "status": "stale" if is_stale else "fresh",
        "should_refresh": is_stale,
        "latest_path": str(path),
        "created_at": created_at,
        "age_hours": round(age, 4),
        "max_age_hours": max_age_hours,
        "intel_count": len(payload.get("social_key_person_intel") or []),
        "reason": "social_handoff_stale" if is_stale else "social_handoff_fresh",
    }


def symbols_to_assets(symbols: list[str]) -> list[str]:
    assets: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        text = str(symbol or "").upper().strip()
        if text.endswith("USDT"):
            text = text[:-4]
        text = {"RENDER": "RENDER", "RNDR": "RENDER"}.get(text, text)
        if text and text not in seen:
            assets.append(text)
            seen.add(text)
    return assets


def refresh_social_handoff(seed_symbols: list[str], max_network_queries: int, timeout_seconds: int) -> dict[str, Any]:
    assets = symbols_to_assets(seed_symbols)
    if not assets:
        assets = symbols_to_assets(DEFAULT_DYNAMIC_SCAN_SYMBOLS)
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "social_key_person_monitor.py"),
        "--symbols",
        ",".join(assets[:24]),
        "--lookback-hours",
        "24",
        "--stale-hours",
        "48",
        "--max-items-per-source",
        "4",
        "--max-network-queries",
        str(max(0, int(max_network_queries))),
    ]
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": cmd,
            "started_at": started.isoformat(),
            "timeout_seconds": timeout_seconds,
            "stdout_tail": (exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
            "operator_note": "Social refresh timed out; dynamic scan falls back to market breadth and Binance ticker data.",
        }
    payload, parse_error = load_json_from_stdout(result.stdout)
    return {
        "status": "ok" if result.returncode == 0 else "failed",
        "command": cmd,
        "started_at": started.isoformat(),
        "finished_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "returncode": result.returncode,
        "stdout_json": payload,
        "stdout_parse_error": parse_error,
        "stderr_tail": result.stderr[-1000:] if result.stderr else "",
        "operator_note": "Social refresh is context-only; it can reprioritize watch/paper candidates but cannot trigger live trading.",
    }


def social_symbol_scores(max_age_hours: float = 12.0) -> dict[str, dict[str, Any]]:
    path = latest_social_handoff()
    if not path:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    created_at = payload.get("created_at")
    try:
        created = dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        age = (dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - created).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        age = max_age_hours + 1
    if age > max_age_hours:
        return {}
    scores: dict[str, dict[str, Any]] = {}
    for item in payload.get("social_key_person_intel") or []:
        action = str(item.get("recommended_max_action") or "watch")
        event = str(item.get("event_type") or "")
        base_score = as_float(item.get("social_intel_score_points"), 0.0) or 0.0
        if action == "risk_alert":
            points = min(4.0, base_score / 30.0)
            long_points = 0.0
            risk_points = points
            note = f"risk_alert:{event}"
        elif action in {"paper_only", "conditional_action"}:
            points = min(4.0, base_score / 30.0)
            long_points = points
            risk_points = 0.0
            note = f"positive_or_conditional:{event}"
        else:
            points = min(1.0, base_score / 80.0)
            long_points = points * 0.35
            risk_points = 0.0
            note = f"watch:{event}"
        for tag in item.get("asset_tags") or []:
            raw = str(tag).upper().strip()
            symbol = SOCIAL_TAG_ALIASES.get(raw) or (f"{raw}USDT" if raw.isalnum() else "")
            if not symbol:
                continue
            current = scores.setdefault(
                symbol,
                {
                    "score": 0.0,
                    "long_score": 0.0,
                    "risk_score": 0.0,
                    "notes": [],
                    "source_path": str(path),
                    "age_hours": round(age, 3),
                },
            )
            current["score"] = round(min(12.0, (as_float(current.get("score"), 0.0) or 0.0) + points), 6)
            current["long_score"] = round(min(12.0, (as_float(current.get("long_score"), 0.0) or 0.0) + long_points), 6)
            current["risk_score"] = round(min(12.0, (as_float(current.get("risk_score"), 0.0) or 0.0) + risk_points), 6)
            if note not in current["notes"]:
                current["notes"].append(note)
    return scores


def is_scan_eligible_usdt_symbol(symbol: str) -> bool:
    if not symbol.endswith("USDT"):
        return False
    if any(marker in symbol for marker in LEVERAGED_TOKEN_MARKERS):
        return False
    base = symbol[:-4]
    return bool(base) and base not in STABLE_OR_FIAT_BASES


def append_unique(target: list[str], symbol: str, seen: set[str], limit: int) -> None:
    symbol = str(symbol or "").strip().upper()
    if symbol and symbol not in seen and len(target) < limit:
        target.append(symbol)
        seen.add(symbol)


def symbol_bucket(symbol: str) -> str:
    return SYMBOL_BUCKETS.get(symbol, "market_mover")


def dynamic_symbol_bucket(symbol: str, change_pct: float, quote_volume: float, trade_count: float) -> str:
    static_bucket = symbol_bucket(symbol)
    if static_bucket != "market_mover":
        return static_bucket
    if quote_volume >= 5_000_000 and trade_count >= 35_000 and change_pct >= 8.0:
        return "high_beta_dynamic"
    if quote_volume >= 20_000_000 and abs(change_pct) >= 5.0:
        return "large_cap_or_infra"
    return static_bucket


def dynamic_pool_family(bucket: str | None) -> str:
    if bucket == "meme_liquid":
        return "meme_liquid"
    if bucket in CORE_DEFENSIVE_BUCKETS:
        return "core_defensive"
    if bucket in HIGH_BETA_BUCKETS or str(bucket or "").startswith("high_beta_"):
        return "high_beta"
    if bucket in {"large_cap_l1", "large_cap_payment", "infrastructure", "defi_bluechip"}:
        return "large_cap_or_infra"
    return "market_mover"


def dynamic_pool_shape_policy(mood: dict[str, Any], max_symbols: int) -> dict[str, Any]:
    limit = max(1, int(max_symbols or 1))
    regime = str((mood or {}).get("market_regime") or "mixed_selective")
    sentiment = str((mood or {}).get("sentiment_state") or "neutral_or_missing_social")
    if regime == "risk_off_rebound_watch":
        policy = {
            "reason": "risk_off_preserve_liquidity_and_rebound_watch",
            "min_slots": {
                "core_defensive": max(2, math.ceil(limit * 0.25)),
                "large_cap_or_infra": max(1, math.ceil(limit * 0.10)),
            },
            "max_slots": {
                "high_beta": max(2, math.floor(limit * 0.30)),
                "meme_liquid": max(1, math.floor(limit * 0.08)),
            },
        }
    elif regime == "risk_on_momentum":
        policy = {
            "reason": "risk_on_expand_high_beta_and_leaders",
            "min_slots": {
                "core_defensive": max(2, math.ceil(limit * 0.15)),
                "high_beta": max(4, math.ceil(limit * 0.35)),
            },
            "max_slots": {
                "high_beta": max(5, math.floor(limit * 0.65)),
                "meme_liquid": max(2, math.floor(limit * 0.20)),
            },
        }
    elif regime == "selective_high_beta_rotation":
        policy = {
            "reason": "selective_rotation_prioritize_volume_acceleration_and_confirmed_social",
            "min_slots": {
                "core_defensive": max(2, math.ceil(limit * 0.12)),
                "high_beta": max(4, math.ceil(limit * 0.30)),
                "social_catalyst": max(1, math.ceil(limit * 0.10)),
            },
            "max_slots": {
                "high_beta": max(4, math.floor(limit * 0.60)),
                "meme_liquid": max(1, math.floor(limit * 0.16)),
            },
        }
    else:
        policy = {
            "reason": "mixed_market_balance_liquid_leaders_and_confirmed_catalysts",
            "min_slots": {
                "core_defensive": max(2, math.ceil(limit * 0.20)),
                "large_cap_or_infra": max(1, math.ceil(limit * 0.12)),
            },
            "max_slots": {
                "high_beta": max(3, math.floor(limit * 0.45)),
                "meme_liquid": max(1, math.floor(limit * 0.12)),
            },
        }
    if sentiment == "risk_alert_cluster":
        policy["reason"] += "_risk_alert_social_penalty"
        policy["sentiment_overlay"] = "risk_alert_contract_high_beta_and_raise_core_floor"
        policy["min_slots"]["core_defensive"] = max(
            policy["min_slots"].get("core_defensive", 0),
            max(2, math.ceil(limit * 0.30)),
        )
        policy["min_slots"]["large_cap_or_infra"] = max(
            policy["min_slots"].get("large_cap_or_infra", 0),
            max(1, math.ceil(limit * 0.15)),
        )
        high_beta_cap = max(1, math.floor(limit * 0.25))
        policy["max_slots"]["high_beta"] = min(policy["max_slots"].get("high_beta", limit), high_beta_cap)
        if "high_beta" in policy["min_slots"]:
            policy["min_slots"]["high_beta"] = min(
                policy["min_slots"]["high_beta"],
                policy["max_slots"]["high_beta"],
            )
        policy["max_slots"]["meme_liquid"] = min(policy["max_slots"].get("meme_liquid", limit), 1)
        policy["min_slots"].pop("social_catalyst", None)
        policy["max_slots"]["social_catalyst"] = 1
    elif sentiment == "positive_catalyst_cluster":
        policy["reason"] += "_positive_social_catalyst_expansion"
        policy["sentiment_overlay"] = "positive_catalyst_expand_confirmed_social_watchlist"
        if regime == "risk_off_rebound_watch":
            policy["min_slots"]["social_catalyst"] = 1
            policy["max_slots"]["social_catalyst"] = max(1, math.floor(limit * 0.12))
        else:
            policy["min_slots"]["social_catalyst"] = max(1, math.ceil(limit * 0.12))
            policy["max_slots"]["social_catalyst"] = max(2, math.floor(limit * 0.25))
    else:
        policy["sentiment_overlay"] = "none"
    return policy


def dynamic_pool_effective_limit(
    mood: dict[str, Any],
    configured_limit: int,
    protected_count: int = 0,
) -> tuple[int, dict[str, Any]]:
    configured = max(1, int(configured_limit or 1))
    protected_floor = max(1, int(protected_count or 0) + 2)
    regime = str((mood or {}).get("market_regime") or "mixed_selective")
    sentiment = str((mood or {}).get("sentiment_state") or "neutral_or_missing_social")
    multiplier = 1.0
    reason = "mixed_or_neutral_keep_configured_width"
    if regime == "risk_off_rebound_watch":
        multiplier = 0.80
        reason = "risk_off_contract_width_to_liquid_rebound_watch"
    elif regime == "risk_on_momentum":
        multiplier = 1.25
        reason = "risk_on_expand_width_for_high_beta_rotation"
    elif regime == "selective_high_beta_rotation":
        multiplier = 1.15
        reason = "selective_rotation_expand_width_for_volume_acceleration"
    if sentiment == "positive_catalyst_cluster":
        multiplier += 0.10
        reason += "_positive_social_catalyst_boost"
    elif sentiment == "risk_alert_cluster":
        multiplier *= 0.75
        reason += "_risk_alert_social_contraction"
    effective = max(protected_floor, int(round(configured * multiplier)))
    effective = min(max(configured, protected_floor) * 2, effective)
    return effective, {
        "configured_limit": configured,
        "effective_limit": effective,
        "protected_floor": protected_floor,
        "multiplier": round(multiplier, 6),
        "reason": reason,
        "market_regime": regime,
        "sentiment_state": sentiment,
    }


def explain_pool_change_from_static_baseline(
    mood: dict[str, Any],
    pool_width_policy: dict[str, Any],
    social_state: dict[str, Any] | None = None,
) -> str:
    regime = str((mood or {}).get("market_regime") or "mixed_selective")
    short_state = str((mood or {}).get("short_term_state") or "unavailable")
    sentiment = str((mood or {}).get("sentiment_state") or "neutral_or_missing_social")
    width_reason = str((pool_width_policy or {}).get("reason") or "configured_width")
    social_freshness = str((social_state or {}).get("freshness_status") or "")
    if sentiment == "neutral_or_missing_social" and social_freshness in {"missing", "stale"}:
        social_clause = "social handoff stale/missing, no sentiment width expansion"
    elif sentiment == "positive_catalyst_cluster":
        social_clause = "fresh positive catalyst cluster reserves social watch slots"
    elif sentiment == "risk_alert_cluster":
        social_clause = "fresh risk-alert cluster contracts speculative buckets"
    else:
        social_clause = "sentiment has no pool-width expansion"
    return (
        f"{regime}/{short_state}: {width_reason}; "
        f"{social_clause}; static symbols used only as baseline/protected fallback"
    )


def select_symbols_by_dynamic_pool_policy(
    ranked: list[dict[str, Any]],
    protected_symbols: list[str],
    base_symbols: list[str],
    explicit_symbols: list[str],
    max_symbols: int,
    mood: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    policy = dynamic_pool_shape_policy(mood, max_symbols)
    selected: list[str] = []
    seen: set[str] = set()
    counts = {
        "core_defensive": 0,
        "high_beta": 0,
        "meme_liquid": 0,
        "large_cap_or_infra": 0,
        "market_mover": 0,
        "social_catalyst": 0,
    }
    item_by_symbol = {item.get("symbol"): item for item in ranked}

    def item_family(item: dict[str, Any] | None) -> str:
        return dynamic_pool_family((item or {}).get("bucket"))

    def is_social_catalyst(item: dict[str, Any] | None) -> bool:
        long_score = as_float((item or {}).get("social_long_score"), 0.0) or 0.0
        risk_score = as_float((item or {}).get("social_risk_score"), 0.0) or 0.0
        return long_score > 0 and long_score >= risk_score

    def add_symbol(symbol: str | None) -> bool:
        symbol = str(symbol or "").upper()
        if not symbol or symbol in seen or len(selected) >= max_symbols:
            return False
        selected.append(symbol)
        seen.add(symbol)
        item = item_by_symbol.get(symbol)
        family = item_family(item) if item else dynamic_pool_family(symbol_bucket(symbol))
        counts[family] = counts.get(family, 0) + 1
        if item and is_social_catalyst(item):
            counts["social_catalyst"] = counts.get("social_catalyst", 0) + 1
        return True

    for symbol in protected_symbols:
        add_symbol(symbol)
    if not explicit_symbols:
        for symbol in CORE_LIQUIDITY_SYMBOLS:
            add_symbol(symbol)

    for quota_name, minimum in (policy.get("min_slots") or {}).items():
        for item in ranked:
            if counts.get(quota_name, 0) >= int(minimum or 0):
                break
            if quota_name == "social_catalyst":
                if is_social_catalyst(item):
                    add_symbol(item.get("symbol"))
            elif item_family(item) == quota_name:
                add_symbol(item.get("symbol"))

    for item in ranked:
        if len(selected) >= max_symbols:
            break
        family = item_family(item)
        caps = policy.get("max_slots") or {}
        if counts.get(family, 0) >= int(caps.get(family, max_symbols) or max_symbols):
            continue
        if is_social_catalyst(item) and counts.get("social_catalyst", 0) >= int(caps.get("social_catalyst", max_symbols) or max_symbols):
            continue
        add_symbol(item.get("symbol"))

    for symbol in base_symbols:
        add_symbol(symbol)

    policy["selected_counts"] = counts
    policy["min_slot_gaps"] = {
        name: max(0, int(minimum or 0) - int(counts.get(name, 0) or 0))
        for name, minimum in (policy.get("min_slots") or {}).items()
    }
    policy["max_slot_excess"] = {
        name: max(0, int(counts.get(name, 0) or 0) - int(maximum or 0))
        for name, maximum in (policy.get("max_slots") or {}).items()
    }
    policy["quota_status"] = (
        "met"
        if not any(policy["min_slot_gaps"].values()) and not any(policy["max_slot_excess"].values())
        else "partial_due_to_protected_symbols_or_pool_limit"
    )
    return selected, policy


def build_market_mood_profile(
    liquid_rows: list[dict[str, Any]],
    by_symbol: dict[str, dict[str, Any]],
    social_scores: dict[str, dict[str, Any]],
    short_term_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    changes = [as_float(row.get("priceChangePercent"), 0.0) or 0.0 for row in liquid_rows]
    positive_count = sum(1 for value in changes if value > 0)
    breadth = (positive_count / len(changes) * 100.0) if changes else 0.0
    strong_gainers_pct = (sum(1 for value in changes if value >= 5.0) / len(changes) * 100.0) if changes else 0.0
    hard_sellers_pct = (sum(1 for value in changes if value <= -5.0) / len(changes) * 100.0) if changes else 0.0
    top20 = sorted(changes, reverse=True)[:20]
    top20_avg = sum(top20) / len(top20) if top20 else 0.0
    average_change = sum(changes) / len(changes) if changes else 0.0
    btc_change = as_float((by_symbol.get("BTCUSDT") or {}).get("priceChangePercent"), 0.0) or 0.0
    eth_change = as_float((by_symbol.get("ETHUSDT") or {}).get("priceChangePercent"), 0.0) or 0.0
    social_long_total = sum(as_float(item.get("long_score"), 0.0) or 0.0 for item in social_scores.values())
    social_risk_total = sum(as_float(item.get("risk_score"), 0.0) or 0.0 for item in social_scores.values())

    short_state = str((short_term_profile or {}).get("state") or "unavailable")
    short_avg_1h = as_float((short_term_profile or {}).get("avg_1h_anchor_change_pct"), 0.0) or 0.0
    short_avg_4h = as_float((short_term_profile or {}).get("avg_4h_anchor_change_pct"), 0.0) or 0.0

    if hard_sellers_pct >= 18.0 or breadth < 32.0 or min(btc_change, eth_change) <= -2.0:
        regime = "risk_off_rebound_watch"
        atmosphere = "defensive_liquidity_first"
        pool_bias = "core_liquidity_plus_rebound_candidates"
        weights = {
            "momentum_multiplier": 0.75,
            "absolute_move_multiplier": 1.45,
            "core_defensive_bonus": 5.0,
            "high_beta_bonus": -1.5,
            "long_social_multiplier": 0.65,
            "risk_social_penalty": 1.25,
        }
    elif (
        breadth >= 58.0
        and btc_change > 0.2
        and eth_change > 0.2
        and top20_avg >= 3.0
        and short_state != "risk_appetite_fading"
    ):
        regime = "risk_on_momentum"
        atmosphere = "broad_risk_appetite"
        pool_bias = "high_beta_momentum_and_large_cap_leaders"
        weights = {
            "momentum_multiplier": 2.75,
            "absolute_move_multiplier": 0.55,
            "core_defensive_bonus": 2.0,
            "high_beta_bonus": 3.25,
            "long_social_multiplier": 1.35,
            "risk_social_penalty": 0.75,
        }
    elif (
        (strong_gainers_pct >= 10.0 and breadth >= 42.0 and top20_avg >= 5.0)
        or (short_state == "risk_appetite_accelerating" and breadth >= 42.0 and top20_avg >= 2.0)
    ):
        regime = "selective_high_beta_rotation"
        atmosphere = "speculative_rotation"
        pool_bias = "top_volume_acceleration_plus_social_narratives"
        weights = {
            "momentum_multiplier": 2.15,
            "absolute_move_multiplier": 1.0,
            "core_defensive_bonus": 1.0,
            "high_beta_bonus": 3.75,
            "long_social_multiplier": 1.55,
            "risk_social_penalty": 0.85,
        }
    else:
        regime = "mixed_selective"
        atmosphere = "selective_rotation"
        pool_bias = "liquid_leaders_with_confirmed_catalysts"
        weights = {
            "momentum_multiplier": 1.6,
            "absolute_move_multiplier": 0.9,
            "core_defensive_bonus": 2.5,
            "high_beta_bonus": 1.0,
            "long_social_multiplier": 1.0,
            "risk_social_penalty": 1.0,
        }

    if social_risk_total > max(2.5, social_long_total * 1.2):
        sentiment_state = "risk_alert_cluster"
        weights["long_social_multiplier"] *= 0.6
        weights["risk_social_penalty"] *= 1.35
        weights["high_beta_bonus"] = min(weights.get("high_beta_bonus", 0.0), 0.5)
        weights["core_defensive_bonus"] = max(weights.get("core_defensive_bonus", 0.0), 3.5)
        sentiment_overlay = "risk_alert_reweight_to_core_liquidity"
    elif social_long_total >= 6.0:
        sentiment_state = "positive_catalyst_cluster"
        weights["long_social_multiplier"] *= 1.25
        if regime != "risk_off_rebound_watch":
            weights["high_beta_bonus"] += 0.5
        sentiment_overlay = "positive_catalyst_reweight_to_confirmed_narratives"
    elif social_long_total > 0.0:
        sentiment_state = "light_positive_context"
        sentiment_overlay = "light_social_context_score_only"
    else:
        sentiment_state = "neutral_or_missing_social"
        sentiment_overlay = "none"

    short_term_overlay = "none"
    if short_state == "risk_appetite_fading":
        weights["momentum_multiplier"] *= 0.75
        weights["high_beta_bonus"] = min(weights.get("high_beta_bonus", 0.0), 0.5)
        weights["core_defensive_bonus"] = max(weights.get("core_defensive_bonus", 0.0), 3.5)
        short_term_overlay = "risk_appetite_fading_contract_high_beta"
        if regime == "risk_on_momentum":
            regime = "mixed_selective"
            atmosphere = "risk_on_fading_to_selective"
            pool_bias = "core_liquidity_plus_only_confirmed_momentum"
    elif short_state == "risk_appetite_accelerating" and regime != "risk_off_rebound_watch":
        weights["momentum_multiplier"] *= 1.12
        weights["high_beta_bonus"] += 0.75
        short_term_overlay = "short_term_risk_appetite_acceleration"
    elif short_state == "rebound_attempt":
        weights["absolute_move_multiplier"] *= 1.15
        weights["core_defensive_bonus"] = max(weights.get("core_defensive_bonus", 0.0), 3.0)
        short_term_overlay = "liquid_rebound_watch_without_chase"
    elif short_state == "pullback_after_strength":
        weights["momentum_multiplier"] *= 0.9
        short_term_overlay = "pullback_after_strength_reduce_chase"

    return {
        "market_regime": regime,
        "market_atmosphere": atmosphere,
        "pool_bias": pool_bias,
        "sentiment_state": sentiment_state,
        "positive_breadth_pct": round(breadth, 6),
        "average_change_24h_pct": round(average_change, 6),
        "strong_gainers_pct": round(strong_gainers_pct, 6),
        "hard_sellers_pct": round(hard_sellers_pct, 6),
        "top20_average_change_24h_pct": round(top20_avg, 6),
        "btc_change_24h_pct": round(btc_change, 6),
        "eth_change_24h_pct": round(eth_change, 6),
        "social_long_total": round(social_long_total, 6),
        "social_risk_total": round(social_risk_total, 6),
        "sentiment_overlay": sentiment_overlay,
        "short_term_anchor_profile": short_term_profile or {},
        "short_term_state": short_state,
        "short_term_overlay": short_term_overlay,
        "short_term_avg_1h_anchor_change_pct": round(short_avg_1h, 6),
        "short_term_avg_4h_anchor_change_pct": round(short_avg_4h, 6),
        "scoring_weights": {key: round(value, 6) for key, value in weights.items()},
    }


def build_dynamic_scan_universe(existing_symbols: str, max_symbols: int) -> tuple[str, dict[str, Any]]:
    open_symbols = load_open_symbols()
    explicit_symbols = split_symbols(existing_symbols)
    protected_symbols = [*open_symbols, *explicit_symbols]
    social_state = social_handoff_state(max_age_hours=12.0)
    social_scores = social_symbol_scores(max_age_hours=12.0)
    ticker_payload, ticker_meta = public_json("/api/v3/ticker/24hr")
    if not isinstance(ticker_payload, list):
        selected: list[str] = []
        seen: set[str] = set()
        for symbol in [*protected_symbols, *CORE_LIQUIDITY_SYMBOLS, *DEFAULT_DYNAMIC_SCAN_SYMBOLS]:
            append_unique(selected, symbol, seen, max_symbols)
        return ",".join(selected), {
            "status": "fallback_static",
            "reason": "binance_all_ticker_unavailable",
            "why_pool_changed_from_static_baseline": (
                "Binance market breadth unavailable; using open/explicit/core/static survival fallback only"
            ),
            "ticker_meta": ticker_meta,
            "social_handoff_state": social_state,
            "social_symbol_scores": social_scores,
            "open_symbols": open_symbols,
            "explicit_symbols": explicit_symbols,
            "selection_policy": "protected_open_and_explicit_first_then_static_fallback",
            "selected_symbols": selected,
        }

    rows = [row for row in ticker_payload if isinstance(row, dict) and is_scan_eligible_usdt_symbol(str(row.get("symbol") or ""))]
    liquid_rows = [row for row in rows if (as_float(row.get("quoteVolume"), 0.0) or 0.0) >= 5_000_000]
    by_symbol = {str(row.get("symbol")): row for row in rows}
    short_term_profile = build_short_term_anchor_profile()
    mood = build_market_mood_profile(liquid_rows, by_symbol, social_scores, short_term_profile=short_term_profile)
    regime = str(mood.get("market_regime") or "mixed_selective")
    atmosphere = str(mood.get("market_atmosphere") or "selective_rotation")
    weights = mood.get("scoring_weights") or {}
    effective_max_symbols, pool_width_policy = dynamic_pool_effective_limit(
        mood,
        max_symbols,
        protected_count=len(protected_symbols),
    )

    ranked: list[dict[str, Any]] = []
    for row in liquid_rows:
        symbol = str(row.get("symbol") or "")
        change = as_float(row.get("priceChangePercent"), 0.0) or 0.0
        quote_volume = as_float(row.get("quoteVolume"), 0.0) or 0.0
        trade_count = as_float(row.get("count"), 0.0) or 0.0
        bucket = dynamic_symbol_bucket(symbol, change, quote_volume, trade_count)
        volume_score = min(24.0, max(0.0, math.log10(max(quote_volume, 1.0)) - 6.0) * 8.0)
        participation_score = min(8.0, max(0.0, math.log10(max(trade_count, 1.0)) - 4.0) * 3.0)
        momentum_multiplier = as_float(weights.get("momentum_multiplier"), 1.6) or 1.6
        absolute_move_multiplier = as_float(weights.get("absolute_move_multiplier"), 0.9) or 0.9
        move_score = max(0.0, min(change, 14.0)) * momentum_multiplier
        move_score += max(0.0, min(abs(change), 18.0) - 4.0) * absolute_move_multiplier
        if regime == "risk_off_rebound_watch":
            if -8.0 <= change <= 2.5:
                move_score += 5.0
        social = social_scores.get(symbol) or {}
        default_watch_bonus = 1.5 if symbol in DEFAULT_DYNAMIC_SCAN_SYMBOLS else 0.0
        core_liquidity_bonus = as_float(weights.get("core_defensive_bonus"), 2.5) if bucket in CORE_DEFENSIVE_BUCKETS else 0.0
        high_beta_bonus = as_float(weights.get("high_beta_bonus"), 1.0) if bucket in HIGH_BETA_BUCKETS else 0.0
        social_long_score = as_float(social.get("long_score"), 0.0) or 0.0
        social_risk_score = as_float(social.get("risk_score"), 0.0) or 0.0
        social_score = social_long_score * (as_float(weights.get("long_social_multiplier"), 1.0) or 1.0)
        social_penalty = social_risk_score * (as_float(weights.get("risk_social_penalty"), 1.0) or 1.0)
        score = (
            volume_score
            + participation_score
            + move_score
            + social_score
            + default_watch_bonus
            + core_liquidity_bonus
            + high_beta_bonus
            - social_penalty
        )
        ranked.append(
            {
                "symbol": symbol,
                "bucket": bucket,
                "score": round(score, 6),
                "price_change_24h_pct": round(change, 6),
                "quote_volume_24h_usd": round(quote_volume, 6),
                "trade_count_24h": int(trade_count),
                "social_score": round(social_score, 6),
                "social_long_score": round(social_long_score, 6),
                "social_risk_score": round(social_risk_score, 6),
                "social_penalty": round(social_penalty, 6),
                "default_watch_bonus": default_watch_bonus,
                "core_liquidity_bonus": core_liquidity_bonus,
                "high_beta_bonus": high_beta_bonus,
                "social_notes": list((social.get("notes") or [])[:4]),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    selected, pool_shape_policy = select_symbols_by_dynamic_pool_policy(
        ranked,
        protected_symbols,
        DEFAULT_DYNAMIC_SCAN_SYMBOLS,
        explicit_symbols,
        effective_max_symbols,
        mood,
    )
    included_ranked_symbols = set(selected)
    selected_dynamic_candidates = [item for item in ranked if item.get("symbol") in included_ranked_symbols]
    social_hit_count = sum(1 for item in ranked if (as_float(item.get("social_score"), 0.0) or 0.0) > 0)
    return ",".join(selected), {
        "status": "ok",
        "ticker_meta": ticker_meta,
        "market_mood_profile": mood,
        "market_regime": regime,
        "market_atmosphere": atmosphere,
        "pool_bias": mood.get("pool_bias"),
        "sentiment_state": mood.get("sentiment_state"),
        "sentiment_overlay": mood.get("sentiment_overlay"),
        "short_term_state": mood.get("short_term_state"),
        "short_term_overlay": mood.get("short_term_overlay"),
        "short_term_anchor_profile": mood.get("short_term_anchor_profile"),
        "short_term_avg_1h_anchor_change_pct": mood.get("short_term_avg_1h_anchor_change_pct"),
        "short_term_avg_4h_anchor_change_pct": mood.get("short_term_avg_4h_anchor_change_pct"),
        "positive_breadth_pct": mood.get("positive_breadth_pct"),
        "average_change_24h_pct": mood.get("average_change_24h_pct"),
        "strong_gainers_pct": mood.get("strong_gainers_pct"),
        "hard_sellers_pct": mood.get("hard_sellers_pct"),
        "top20_average_change_24h_pct": mood.get("top20_average_change_24h_pct"),
        "btc_change_24h_pct": mood.get("btc_change_24h_pct"),
        "eth_change_24h_pct": mood.get("eth_change_24h_pct"),
        "social_long_total": mood.get("social_long_total"),
        "social_risk_total": mood.get("social_risk_total"),
        "liquid_usdt_symbol_count": len(liquid_rows),
        "configured_max_symbols": max_symbols,
        "max_symbols": effective_max_symbols,
        "pool_width_policy": pool_width_policy,
        "why_pool_changed_from_static_baseline": explain_pool_change_from_static_baseline(
            mood,
            pool_width_policy,
            social_state,
        ),
        "open_symbols": open_symbols,
        "explicit_symbols": explicit_symbols,
        "selection_policy": (
            "open_positions_and_explicit_symbols_are_preserved; remaining slots are ranked by "
            "market atmosphere, short-term anchor mood, sentiment state, breadth, 24h movement, liquidity, participation, "
            "core/high-beta bucket bias, fresh social handoff, and market-mood bucket quotas"
        ),
        "pool_shape_policy": pool_shape_policy,
        "selected_symbols": selected,
        "selected_dynamic_candidates": selected_dynamic_candidates[:12],
        "top_dynamic_candidates": ranked[:12],
        "social_hit_count": social_hit_count,
        "social_handoff_state": social_state,
        "social_symbol_scores": social_scores,
    }


def load_json_from_stdout(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    text = stdout.strip()
    if not text:
        return None, "empty stdout"
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload, None
        return None, "stdout JSON was not an object"
    except json.JSONDecodeError as exc:
        return None, f"stdout was not valid JSON: {exc}"


def run_command(label: str, args: list[str], timeout_seconds: int) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    process = subprocess.Popen(
        args,
        cwd=str(WORKSPACE_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        terminated_with = "SIGTERM"
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            terminated_with = "SIGKILL"
            stdout, stderr = process.communicate()
        finished = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        stdout = stdout or ""
        stderr = stderr or ""
        payload, parse_error = load_json_from_stdout(stdout)
        return {
            "label": label,
            "command": args,
            "returncode": 124,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "stdout_json": payload,
            "stdout_parse_error": parse_error or "child timed out",
            "stderr_tail": stderr[-4000:] if stderr else "",
            "status": "timeout",
            "timeout_seconds": timeout_seconds,
            "terminated_process_group": True,
            "terminated_with": terminated_with,
        }
    finished = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    stdout = stdout or ""
    stderr = stderr or ""
    payload, parse_error = load_json_from_stdout(stdout)
    return {
        "label": label,
        "command": args,
        "returncode": process.returncode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "stdout_json": payload,
        "stdout_parse_error": parse_error,
        "stderr_tail": stderr[-4000:] if stderr else "",
        "status": "ok" if process.returncode == 0 and payload is not None else "failed",
    }


def walk_objects(value: Any, path: str = "$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk_objects(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_objects(child, f"{path}[{index}]")


def load_json_if_exists(path_text: str) -> Any | None:
    path = Path(path_text)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    if not path.exists() or not path.is_file() or path.suffix.lower() != ".json":
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def script_cmd(script_name: str) -> list[str]:
    return [sys.executable, str(SCRIPT_DIR / script_name)]


def cache_file_count(path_text: str) -> int:
    return len(glob.glob(str(Path(path_text).expanduser() / "*.json")))


def resolve_cache_dir_text(path_text: str) -> str:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return str(path)
    active_candidate = ACTIVE_ROOT / path
    if active_candidate.exists():
        return str(active_candidate.resolve())
    workspace_candidate = WORKSPACE_ROOT / path
    if workspace_candidate.exists():
        return str(workspace_candidate.resolve())
    return str(active_candidate.resolve())


def resolve_walkforward_cache_dirs(args: argparse.Namespace) -> str:
    if args.walkforward_cache_dirs:
        return ",".join(
            resolve_cache_dir_text(item.strip())
            for item in str(args.walkforward_cache_dirs).split(",")
            if item.strip()
        )
    dynamic_candidates = sorted(
        [
            path
            for pattern in (
                str(DURABLE_KLINE_CACHE_ROOT / "dynamic_*"),
                "/private/tmp/binance_klines_cache_dynamic_*",
            )
            for path in glob.glob(pattern)
            if cache_file_count(path) > 0
        ],
        key=lambda path: Path(path).stat().st_mtime,
        reverse=True,
    )
    candidates = [
        *dynamic_candidates[:3],
        "/private/tmp/binance_klines_cache_validation_20260530",
        "/private/tmp/binance_klines_cache_sunday_crypto_realistic",
        "/private/tmp/binance_klines_cache_daily_crypto_paper_auto_trader",
        "/private/tmp/binance_klines_cache_fast_crypto_paper_auto_trader",
        "/private/tmp/binance_klines_cache_v216",
        "/private/tmp/binance_klines_cache_v216_retry",
    ]
    populated = [path for path in candidates if cache_file_count(path) > 0]
    return ",".join(populated or candidates)


def validation_audit(label: str, args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("validation_sample_auditor.py") + [
        "--format",
        "json",
        "--paper-dir",
        args.paper_dir,
        "--experiment-dir",
        args.experiment_dir,
        "--recommendation-ledger",
        args.recommendation_ledger,
    ]
    return run_command(label, cmd, timeout_seconds=args.validation_timeout_seconds)


def paper_testnet_risk_control_audit(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("paper_testnet_risk_control_auditor.py") + ["--compact-output"]
    if args.dry_run:
        cmd.append("--dry-run")
    return run_command("paper_testnet_risk_control_audit", cmd, timeout_seconds=args.validation_timeout_seconds)


def dynamic_kline_cache_builder(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = args.dynamic_cache_dir
    if not cache_dir:
        cache_dir = str(DURABLE_KLINE_CACHE_ROOT / f"dynamic_{local_now().strftime('%Y%m%d')}")
    seed_symbols = split_symbols(args.symbols) or DEFAULT_DYNAMIC_SCAN_SYMBOLS
    top_symbols = max(int(args.dynamic_top_symbols or 0), len(seed_symbols), 1)
    cmd = script_cmd("binance_kline_cache_builder.py") + [
        "--cache-dir",
        cache_dir,
        "--top-symbols",
        str(top_symbols),
        "--seed-symbols",
        ",".join(seed_symbols),
        "--intervals",
        args.dynamic_cache_intervals,
        "--max-kline-requests",
        str(args.dynamic_cache_max_kline_requests),
        "--format",
        "json",
    ]
    child = run_command(
        "binance_kline_cache_builder",
        cmd,
        timeout_seconds=args.dynamic_cache_timeout_seconds,
    )
    payload = child.get("stdout_json")
    if isinstance(payload, dict):
        now = local_now()
        path = ACTIVE_ROOT / "experiments" / f"{now.strftime('%Y%m%d-%H%M')}-binance-kline-cache-builder.json"
        if int(payload.get("file_count") or 0) > 0 and not args.dry_run:
            write_json(path, payload)
        if int(payload.get("file_count") or 0) > 0:
            child["persisted_json"] = str(path.relative_to(WORKSPACE_ROOT))
        else:
            child["persist_skipped_reason"] = "file_count=0; not persisted as kline cache evidence"
    return child


def apply_dynamic_kline_prefetch_filter(
    args: argparse.Namespace,
    child: dict[str, Any],
    protected_symbols: list[str],
    dynamic_scan_universe: dict[str, Any],
) -> dict[str, Any]:
    payload = child.get("stdout_json")
    if not isinstance(payload, dict):
        result = {"status": "skipped_no_payload", "removed_symbols": [], "cache_dir": ""}
        dynamic_scan_universe["kline_prefetch_filter"] = result
        return result
    cache_dir = str(payload.get("cache_dir") or "")
    if cache_dir and int(payload.get("file_count") or 0) > 0:
        setattr(args, "fast_cache_dir_override", cache_dir)
    failed_symbols: set[str] = set()
    for failure in payload.get("failures") or []:
        if not isinstance(failure, dict):
            continue
        symbol = str(failure.get("symbol") or "").upper().strip()
        if not symbol:
            name = str(failure.get("name") or "")
            parts = name.split("_")
            if len(parts) >= 2:
                symbol = parts[1].upper().strip()
        if symbol:
            failed_symbols.add(symbol)
    current_symbols = split_symbols(args.symbols)
    protected = {str(symbol).upper().strip() for symbol in protected_symbols if str(symbol).strip()}
    removed = [symbol for symbol in current_symbols if symbol in failed_symbols and symbol not in protected]
    if removed:
        args.symbols = ",".join([symbol for symbol in current_symbols if symbol not in set(removed)])
    result = {
        "status": "filtered" if removed else "unchanged",
        "cache_dir": cache_dir,
        "failed_symbols": sorted(failed_symbols),
        "protected_symbols": sorted(protected),
        "removed_symbols": removed,
        "effective_symbols_after_filter": split_symbols(args.symbols),
        "operator_note": "Non-protected symbols with failed/stale Kline prefetch are removed before fast paper scan; open and explicit symbols are preserved.",
    }
    dynamic_scan_universe["kline_prefetch_filter"] = result
    return result


def append_common_child_args(cmd: list[str], args: argparse.Namespace) -> list[str]:
    if args.dry_run:
        cmd.append("--dry-run")
    if args.offline_fixture:
        cmd.append("--offline-fixture")
    if args.no_lock:
        cmd.append("--no-lock")
    if args.external_agent_outputs_json:
        cmd.extend(["--external-agent-outputs-json", args.external_agent_outputs_json])
    return cmd


def append_fast_fetch_budget_args(cmd: list[str], args: argparse.Namespace) -> list[str]:
    if getattr(args, "market_fetch_wall_clock_seconds", 0):
        cmd.extend(["--market-fetch-wall-clock-seconds", str(args.market_fetch_wall_clock_seconds)])
    if getattr(args, "kline_fetch_wall_clock_seconds", 0):
        cmd.extend(["--kline-fetch-wall-clock-seconds", str(args.kline_fetch_wall_clock_seconds)])
    if getattr(args, "info_fetch_wall_clock_seconds", 0):
        cmd.extend(["--info-fetch-wall-clock-seconds", str(args.info_fetch_wall_clock_seconds)])
    return cmd


def paper_exit_monitor(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("paper_position_exit_monitor.py") + ["--compact-output"]
    return run_command(
        "paper_position_exit_monitor",
        append_common_child_args(cmd, args),
        timeout_seconds=args.exit_timeout_seconds,
    )


def fast_paper_scan(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("fast_crypto_paper_auto_trader.py") + ["--compact-output", "--allow-outside-window"]
    if args.symbols:
        cmd.extend(["--symbols", args.symbols])
    if args.fast_intervals:
        cmd.extend(["--intervals", args.fast_intervals])
    cache_dir = getattr(args, "fast_cache_dir_override", "")
    if cache_dir:
        cmd.extend(["--cache-dir", str(cache_dir)])
    cmd = append_fast_fetch_budget_args(cmd, args)
    return run_command(
        "fast_crypto_paper_auto_trader",
        append_common_child_args(cmd, args),
        timeout_seconds=args.fast_timeout_seconds,
    )


def recovery_shadow_scan(args: argparse.Namespace, capacity_reason: dict[str, Any] | None, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a dry-run scan while capacity is paused.

    This is deliberately not a paper entry path. It bypasses the validation
    capacity gate only inside a dry-run child command so the system can keep
    observing recovery-queue candidates while exposure is paused.
    """
    cmd = script_cmd("fast_crypto_paper_auto_trader.py") + [
        "--compact-output",
        "--allow-outside-window",
        "--dry-run",
        "--ignore-validation-capacity-gate",
    ]
    plan = plan or {}
    plan_symbols = [str(s).upper() for s in (plan.get("symbols") or []) if str(s).strip()]
    if args.symbols:
        cmd.extend(["--symbols", args.symbols])
    elif plan_symbols:
        cmd.extend(["--symbols", ",".join(plan_symbols)])
    if plan.get("cache_dir"):
        cmd.extend(["--cache-dir", str(plan.get("cache_dir"))])
    intervals = args.recovery_shadow_intervals or args.fast_intervals
    if intervals:
        cmd.extend(["--intervals", intervals])
    cmd = append_fast_fetch_budget_args(cmd, args)
    if args.offline_fixture:
        cmd.append("--offline-fixture")
    if args.no_lock:
        cmd.append("--no-lock")
    if args.external_agent_outputs_json:
        cmd.extend(["--external-agent-outputs-json", args.external_agent_outputs_json])
    child = run_command(
        "fast_crypto_recovery_shadow_scan",
        cmd,
        timeout_seconds=args.recovery_shadow_timeout_seconds,
    )
    child["recovery_shadow_scan"] = True
    child["capacity_gate_reason"] = capacity_reason or {}
    payload = child.get("stdout_json")
    if isinstance(payload, dict):
        payload["status"] = payload.get("status") or "ok"
        payload["dry_run"] = True
        payload["ignore_validation_capacity_gate"] = True
        payload["recovery_shadow_scan"] = {
            "enabled": True,
            "dry_run": True,
            "ignore_validation_capacity_gate": True,
            "prefetch_cache_dir": plan.get("cache_dir"),
            "prefetch_symbols": plan_symbols,
            "prefetch_intervals": plan.get("intervals") or [],
            "reason": capacity_reason,
            "operator_note": (
                "Shadow scan is observation-only. It may bypass capacity gating for dry-run scanning, "
                "but it cannot open paper positions or authorize live trading."
            ),
        }
    return child


def daily_paper_scan(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("daily_crypto_paper_auto_trader.py") + ["--compact-output", "--allow-outside-window"]
    if args.symbols:
        cmd.extend(["--symbols", args.symbols])
    if args.daily_intervals:
        cmd.extend(["--intervals", args.daily_intervals])
    return run_command(
        "daily_crypto_paper_auto_trader",
        append_common_child_args(cmd, args),
        timeout_seconds=args.daily_timeout_seconds,
    )


def walkforward_refresh(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("weekly_goal_strategy_lab.py")
    cache_dirs = resolve_walkforward_cache_dirs(args)
    if cache_dirs:
        cmd.extend(["--cache-dirs", cache_dirs])
    if args.symbols:
        cmd.extend(["--symbols", args.symbols])
    if args.walkforward_intervals:
        cmd.extend(["--intervals", args.walkforward_intervals])
    child = run_command(
        "weekly_goal_strategy_lab",
        cmd,
        timeout_seconds=args.walkforward_timeout_seconds,
    )
    payload = child.get("stdout_json")
    if isinstance(payload, dict):
        now = local_now()
        path = ACTIVE_ROOT / "experiments" / f"{now.strftime('%Y%m%d-%H%M')}-weekly-goal-validation-refresh.json"
        if int(payload.get("frames_loaded") or 0) > 0 and not args.dry_run:
            write_json(path, payload)
        if int(payload.get("frames_loaded") or 0) > 0:
            child["persisted_json"] = str(path.relative_to(WORKSPACE_ROOT))
        else:
            child["persist_skipped_reason"] = "frames_loaded=0; not persisted as walk-forward evidence"
    return child


def current_signal_probe(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("current_signal_probe.py")
    cache_dirs = resolve_walkforward_cache_dirs(args)
    if cache_dirs:
        cmd.extend(["--cache-dirs", cache_dirs])
    current_signal_symbols = split_symbols(args.symbols)
    if args.current_signal_max_symbols > 0 and len(current_signal_symbols) > args.current_signal_max_symbols:
        current_signal_symbols = current_signal_symbols[: args.current_signal_max_symbols]
    if current_signal_symbols:
        cmd.extend(["--symbols", ",".join(current_signal_symbols)])
    if args.walkforward_intervals:
        cmd.extend(["--intervals", args.walkforward_intervals])
    child = run_command(
        "current_signal_probe",
        cmd,
        timeout_seconds=args.current_signal_timeout_seconds,
    )
    payload = child.get("stdout_json")
    if isinstance(payload, dict):
        now = local_now()
        path = ACTIVE_ROOT / "experiments" / f"{now.strftime('%Y%m%d-%H%M')}-current-signal-probe.json"
        if int(payload.get("frames_loaded") or 0) > 0 and not args.dry_run:
            write_json(path, payload)
        if int(payload.get("frames_loaded") or 0) > 0:
            child["persisted_json"] = str(path.relative_to(WORKSPACE_ROOT))
        else:
            child["persist_skipped_reason"] = "frames_loaded=0; not persisted as current-signal evidence"
    return child


def resolve_dir(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    return path


def latest_current_signal_probe_state(args: argparse.Namespace, now: dt.datetime) -> dict[str, Any]:
    """Return freshness state for persisted current-signal evidence.

    This runner may be scheduled hourly. When new samples are paused, we still
    want stale research evidence to refresh occasionally, but not every hour.
    File mtime is the authoritative freshness marker because older artifacts do
    not all carry a stable created_at field.
    """
    experiment_dir = resolve_dir(args.experiment_dir)
    threshold_hours = max(0.0, float(args.current_signal_stale_hours))
    paths = sorted(
        experiment_dir.glob("*current-signal-probe.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    if not paths:
        return {
            "status": "missing",
            "should_refresh": True,
            "threshold_hours": threshold_hours,
            "latest_path": None,
            "latest_any_path": None,
            "latest_mtime_utc": None,
            "age_hours": None,
            "usable_frames_loaded": None,
            "reason": "no_current_signal_probe_artifact_found",
        }
    latest_any = paths[0]
    latest_usable: Path | None = None
    latest_any_payload = load_json_if_exists(str(latest_any))
    latest_any_frames_loaded = (
        int(latest_any_payload.get("frames_loaded") or 0)
        if isinstance(latest_any_payload, dict)
        else None
    )
    for candidate in paths:
        payload = load_json_if_exists(str(candidate))
        if isinstance(payload, dict) and int(payload.get("frames_loaded") or 0) > 0:
            latest_usable = candidate
            break
    if latest_usable is None:
        mtime = dt.datetime.fromtimestamp(latest_any.stat().st_mtime, tz=dt.timezone.utc)
        return {
            "status": "missing_usable",
            "should_refresh": True,
            "threshold_hours": threshold_hours,
            "latest_path": None,
            "latest_any_path": str(latest_any.relative_to(WORKSPACE_ROOT)),
            "latest_mtime_utc": mtime.replace(microsecond=0).isoformat(),
            "age_hours": None,
            "usable_frames_loaded": None,
            "latest_any_frames_loaded": latest_any_frames_loaded,
            "reason": "latest_current_signal_probe_has_no_loaded_frames",
        }
    latest = latest_usable
    mtime = dt.datetime.fromtimestamp(latest.stat().st_mtime, tz=dt.timezone.utc)
    now_utc = now.astimezone(dt.timezone.utc)
    age_hours = max(0.0, (now_utc - mtime).total_seconds() / 3600.0)
    is_stale = age_hours >= threshold_hours
    usable_payload = load_json_if_exists(str(latest)) or {}
    return {
        "status": "stale" if is_stale else "fresh",
        "should_refresh": is_stale,
        "threshold_hours": threshold_hours,
        "latest_path": str(latest.relative_to(WORKSPACE_ROOT)),
        "latest_any_path": str(latest_any.relative_to(WORKSPACE_ROOT)),
        "latest_mtime_utc": mtime.replace(microsecond=0).isoformat(),
        "age_hours": round(age_hours, 4),
        "usable_frames_loaded": int(usable_payload.get("frames_loaded") or 0) if isinstance(usable_payload, dict) else None,
        "latest_any_frames_loaded": latest_any_frames_loaded,
        "reason": "latest_usable_current_signal_probe_exceeds_stale_threshold" if is_stale else "latest_usable_current_signal_probe_is_fresh",
    }


def current_signal_near_miss_sampler(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("current_signal_near_miss_sampler.py") + ["--format", "json", *current_signal_sampler_context_args(args)]
    if args.dry_run:
        cmd.append("--dry-run")
    return run_command(
        "current_signal_near_miss_sampler",
        cmd,
        timeout_seconds=args.validation_timeout_seconds,
    )


def current_signal_robustness_audit(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("current_signal_robustness_batch.py") + ["--compact-output"]
    cache_dirs = resolve_walkforward_cache_dirs(args)
    if cache_dirs:
        cmd.extend(["--cache-dirs", cache_dirs])
    if args.dry_run:
        cmd.append("--dry-run")
    return run_command(
        "current_signal_robustness_audit",
        cmd,
        timeout_seconds=args.validation_timeout_seconds,
    )


def current_signal_quality_scout_sampler(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("current_signal_near_miss_sampler.py") + [
        "--format",
        "json",
        "--quality-scout-only",
        *current_signal_sampler_context_args(args),
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    return run_command(
        "current_signal_quality_scout_sampler",
        cmd,
        timeout_seconds=args.validation_timeout_seconds,
    )


def current_signal_sampler_context_args(args: argparse.Namespace) -> list[str]:
    dynamic = getattr(args, "dynamic_scan_universe", None)
    dynamic = dynamic if isinstance(dynamic, dict) else {}
    return [
        "--market-regime",
        str(dynamic.get("market_regime") or "unknown_or_not_attached"),
        "--market-atmosphere",
        str(dynamic.get("market_atmosphere") or "unknown_or_not_attached"),
        "--short-term-state",
        str(dynamic.get("short_term_state") or "unknown_or_not_attached"),
        "--sentiment-state",
        str(dynamic.get("sentiment_state") or "unknown_or_not_attached"),
        "--market-context-source",
        "validation_progress_runner_dynamic_scan",
    ]


def strategy_recovery_optimizer(args: argparse.Namespace) -> dict[str, Any]:
    cmd = script_cmd("strategy_recovery_optimizer.py") + [
        "--format",
        "json",
        "--paper-dir",
        args.paper_dir,
        "--experiment-dir",
        args.experiment_dir,
        "--recommendation-ledger",
        args.recommendation_ledger,
    ]
    child = run_command(
        "strategy_recovery_optimizer",
        cmd,
        timeout_seconds=args.validation_timeout_seconds,
    )
    payload = child.get("stdout_json")
    if isinstance(payload, dict):
        now = local_now()
        path = ACTIVE_ROOT / "experiments" / f"{now.strftime('%Y%m%d-%H%M')}-strategy-recovery-optimizer.json"
        if not args.dry_run and payload.get("status") in {"ok", "no_eligible_recovery_candidates"}:
            write_json(path, payload)
            child["persisted_json"] = str(path.relative_to(WORKSPACE_ROOT))
        elif payload.get("status") not in {"ok", "no_eligible_recovery_candidates"}:
            child["persist_skipped_reason"] = f"optimizer status={payload.get('status')}"
    return child


def strategy_iteration_backlog_sync(args: argparse.Namespace) -> dict[str, Any]:
    if args.dry_run:
        return skipped_child(
            "strategy_iteration_backlog",
            {
                "status": "skipped_dry_run",
                "operator_note": "Dry-run mode does not write backlog artifacts or update paper-only learning state.",
            },
            script_cmd("strategy_iteration_backlog.py") + ["--compact-output"],
            skipped_status="skipped_dry_run",
        )
    cmd = script_cmd("strategy_iteration_backlog.py") + ["--compact-output"]
    return run_command(
        "strategy_iteration_backlog",
        cmd,
        timeout_seconds=args.validation_timeout_seconds,
    )


def paper_strategy_auto_evolver(args: argparse.Namespace) -> dict[str, Any]:
    if args.dry_run:
        return skipped_child(
            "paper_strategy_auto_evolver",
            {
                "status": "skipped_dry_run",
                "operator_note": "Dry-run mode does not mutate the paper-only strategy overlay.",
            },
            script_cmd("paper_strategy_auto_evolver.py") + ["--compact-output"],
            skipped_status="skipped_dry_run",
        )
    cmd = script_cmd("paper_strategy_auto_evolver.py") + ["--compact-output"]
    return run_command(
        "paper_strategy_auto_evolver",
        cmd,
        timeout_seconds=args.validation_timeout_seconds,
    )


def latest_strategy_recovery_optimizer_payload(args: argparse.Namespace) -> dict[str, Any]:
    experiment_dir = resolve_dir(args.experiment_dir)
    paths = sorted(
        experiment_dir.glob("*strategy-recovery-optimizer.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, dict):
            continue
        forbidden = []
        for obj_path, obj in walk_objects(payload):
            for key in ("live_orders_enabled", "private_api_used", "private_api_keys_used", "allow_real_orders"):
                if obj.get(key) is True:
                    forbidden.append(f"{obj_path}.{key}=true")
        if forbidden:
            return {
                "status": "blocked_forbidden_flags",
                "path": str(path.relative_to(WORKSPACE_ROOT)),
                "forbidden_flags": forbidden,
                "eligible_retest_queue": [],
            }
        return {
            "status": payload.get("status") or "ok",
            "path": str(path.relative_to(WORKSPACE_ROOT)),
            "eligible_retest_queue": payload.get("eligible_retest_queue") or [],
        }
    return {
        "status": "missing",
        "path": None,
        "eligible_retest_queue": [],
    }


def recovery_shadow_prefetch_plan(args: argparse.Namespace) -> dict[str, Any]:
    state = latest_strategy_recovery_optimizer_payload(args)
    queue = state.get("eligible_retest_queue") or []
    explicit_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    max_symbols = max(1, int(args.recovery_shadow_prefetch_max_symbols or 1))
    symbols: list[str] = []
    if explicit_symbols:
        symbols = explicit_symbols[:max_symbols]
    else:
        for item in queue:
            symbol = str(item.get("symbol") or "").upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= max_symbols:
                break
    configured_intervals = [s.strip() for s in args.recovery_shadow_intervals.split(",") if s.strip()]
    queue_intervals = []
    for item in queue:
        interval = str(item.get("interval") or "").strip()
        if interval and interval not in queue_intervals:
            queue_intervals.append(interval)
    intervals = configured_intervals or queue_intervals or ["1m", "5m", "15m", "1h", "4h", "1d"]
    cache_dir = args.recovery_shadow_cache_dir or str(
        DURABLE_KLINE_CACHE_ROOT / "recovery_shadow" / local_now().strftime("%Y%m%d")
    )
    return {
        "recovery_optimizer_state": state,
        "symbols": symbols,
        "intervals": intervals,
        "cache_dir": cache_dir,
        "uses_explicit_symbols": bool(explicit_symbols),
    }


def recovery_shadow_kline_prefetch(args: argparse.Namespace, plan: dict[str, Any]) -> dict[str, Any]:
    symbols = plan.get("symbols") or []
    intervals = plan.get("intervals") or []
    if args.offline_fixture:
        return skipped_child(
            "recovery_shadow_kline_prefetch",
            {
                "status": "skipped_offline_fixture",
                "operator_note": "Offline fixture mode uses synthetic fixture klines; public Binance prefetch is skipped.",
            },
            script_cmd("binance_kline_cache_builder.py"),
            skipped_status="skipped_offline_fixture",
        )
    if not symbols:
        return skipped_child(
            "recovery_shadow_kline_prefetch",
            {
                "status": "skipped_no_symbols",
                "recovery_optimizer_state": plan.get("recovery_optimizer_state"),
                "operator_note": "No recovery queue symbols were available for prefetch.",
            },
            script_cmd("binance_kline_cache_builder.py"),
            skipped_status="skipped_no_symbols",
        )
    cmd = script_cmd("binance_kline_cache_builder.py") + [
        "--cache-dir",
        str(plan.get("cache_dir")),
        "--seed-symbols",
        ",".join(symbols),
        "--top-symbols",
        str(len(symbols)),
        "--intervals",
        ",".join(intervals),
        "--coverage-profile",
        "requested_only",
        "--min-bars",
        str(args.recovery_shadow_prefetch_min_bars),
        "--target-bars",
        str(args.recovery_shadow_prefetch_target_bars),
        "--format",
        "json",
    ]
    child = run_command(
        "recovery_shadow_kline_prefetch",
        cmd,
        timeout_seconds=args.dynamic_cache_timeout_seconds,
    )
    child["recovery_shadow_prefetch"] = True
    child["recovery_shadow_prefetch_plan"] = plan
    payload = child.get("stdout_json")
    if isinstance(payload, dict):
        payload["recovery_shadow_prefetch"] = {
            "enabled": True,
            "purpose": "Prefetch recovery queue symbols/intervals before dry-run shadow scan.",
            "symbols": symbols,
            "intervals": intervals,
            "cache_dir": plan.get("cache_dir"),
            "operator_note": "Public Binance market-data cache only; no account, private, or order endpoint is used.",
        }
        now = local_now()
        path = ACTIVE_ROOT / "experiments" / f"{now.strftime('%Y%m%d-%H%M')}-recovery-shadow-kline-prefetch.json"
        if not args.dry_run and int(payload.get("file_count") or 0) > 0:
            write_json(path, payload)
            child["persisted_json"] = str(path.relative_to(WORKSPACE_ROOT))
        elif int(payload.get("file_count") or 0) <= 0:
            child["persist_skipped_reason"] = "file_count=0; not persisted as recovery shadow prefetch evidence"
    return child


def validation_plan(audit_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(audit_payload, dict):
        return {}
    return audit_payload.get("validation_sample_plan") or {}


def validation_capacity(audit_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(audit_payload, dict):
        return {}
    return audit_payload.get("validation_capacity_state") or {}


def near_miss_capacity_skip(audit_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    capacity = validation_capacity(audit_payload)
    if not capacity:
        return None
    sample_action = str(capacity.get("sample_action") or "")
    runner_mode = str(capacity.get("recommended_runner_mode") or "")
    skip_actions = {"pause_new_samples", "hold_new_samples_temporarily"}
    skip_modes = {"exit_only_until_slots_free", "exit_first_then_reassess", "exit_monitor_only"}
    if sample_action in skip_actions or runner_mode in skip_modes:
        return {
            "sample_action": sample_action,
            "recommended_runner_mode": runner_mode,
            "reason": capacity.get("reason"),
            "next_runner_hint": capacity.get("next_runner_hint"),
            "open_count": capacity.get("open_count"),
            "effective_max_open_positions": capacity.get("effective_max_open_positions"),
            "open_slots_remaining": capacity.get("open_slots_remaining"),
            "operator_note": "New paper sampling skipped by validation capacity gate; exit review still runs and this never affects live trading.",
        }
    return None


def parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def load_paper_ledger(args: argparse.Namespace) -> dict[str, Any]:
    ledger_path = resolve_dir(args.paper_dir) / "paper_portfolio_ledger.json"
    if not ledger_path.exists():
        return {
            "status": "missing",
            "ledger_path": str(ledger_path.relative_to(WORKSPACE_ROOT)) if str(ledger_path).startswith(str(WORKSPACE_ROOT)) else str(ledger_path),
            "reason": "paper_portfolio_ledger_missing",
        }
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unreadable",
            "ledger_path": str(ledger_path.relative_to(WORKSPACE_ROOT)) if str(ledger_path).startswith(str(WORKSPACE_ROOT)) else str(ledger_path),
            "reason": f"paper_portfolio_ledger_unreadable: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "ledger_path": str(ledger_path.relative_to(WORKSPACE_ROOT)) if str(ledger_path).startswith(str(WORKSPACE_ROOT)) else str(ledger_path),
            "reason": "paper_portfolio_ledger_json_not_object",
        }
    payload["_ledger_path"] = str(ledger_path.relative_to(WORKSPACE_ROOT)) if str(ledger_path).startswith(str(WORKSPACE_ROOT)) else str(ledger_path)
    return payload


def pause_resume_guard_skip(args: argparse.Namespace, now: dt.datetime) -> dict[str, Any] | None:
    """Force a review-first runner pass after a pause or stale ledger.

    Hourly paper trading relies on the ledger being freshly reviewed. If the
    automation has been paused, an operator may resume with open positions that
    are stale, expired, or near expiry. In that case this runner should review
    exits first and defer any new sampling until the next invocation.
    """
    if args.disable_pause_resume_guard:
        return None
    ledger = load_paper_ledger(args)
    ledger_status = ledger.get("status")
    if ledger_status in {"missing", "unreadable", "invalid"}:
        return {
            "sample_action": "pause_new_samples",
            "recommended_runner_mode": "exit_monitor_only",
            "reason": ledger.get("reason") or ledger_status,
            "ledger_path": ledger.get("ledger_path"),
            "operator_note": "New paper sampling skipped because the paper ledger is not safely readable; repair or review ledger before opening new paper positions.",
        }
    open_positions = ledger.get("open_positions") if isinstance(ledger.get("open_positions"), list) else []
    if not open_positions:
        return None
    now_utc = now.astimezone(dt.timezone.utc)
    expired: list[dict[str, Any]] = []
    near_expiry: list[dict[str, Any]] = []
    lookahead_hours = max(0.0, float(args.resume_guard_expiry_lookahead_hours))
    for position in open_positions:
        if not isinstance(position, dict):
            continue
        expires_at = parse_datetime(position.get("expires_at"))
        if expires_at is None:
            continue
        hours_to_expiry = (expires_at - now_utc).total_seconds() / 3600.0
        item = {
            "paper_trade_id": position.get("paper_trade_id"),
            "symbol": position.get("symbol"),
            "expires_at": expires_at.isoformat(),
            "hours_to_expiry": round(hours_to_expiry, 4),
        }
        if hours_to_expiry <= 0:
            expired.append(item)
        elif lookahead_hours > 0 and hours_to_expiry <= lookahead_hours:
            near_expiry.append(item)
    updated_at = parse_datetime(ledger.get("updated_at"))
    ledger_age_hours: float | None = None
    if updated_at is not None:
        ledger_age_hours = max(0.0, (now_utc - updated_at).total_seconds() / 3600.0)
    stale = (
        ledger_age_hours is None
        or ledger_age_hours >= max(0.0, float(args.resume_guard_max_ledger_age_hours))
    )
    if expired or near_expiry or stale:
        reasons: list[str] = []
        if expired:
            reasons.append("open_positions_expired")
        if near_expiry:
            reasons.append("open_positions_near_expiry")
        if stale:
            reasons.append("ledger_stale_with_open_positions")
        return {
            "sample_action": "pause_new_samples",
            "recommended_runner_mode": "exit_monitor_only",
            "reason": ",".join(reasons),
            "ledger_path": ledger.get("_ledger_path"),
            "ledger_updated_at": ledger.get("updated_at"),
            "ledger_age_hours": round(ledger_age_hours, 4) if ledger_age_hours is not None else None,
            "max_ledger_age_hours": float(args.resume_guard_max_ledger_age_hours),
            "open_count": len(open_positions),
            "expired_positions": expired,
            "near_expiry_positions": near_expiry,
            "operator_note": "Pause/resume guard skipped new paper sampling; run exit review first, then reassess new entries in the next runner pass.",
        }
    return None


def merge_skip_reasons(
    capacity_reason: dict[str, Any] | None,
    pause_resume_reason: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if capacity_reason and pause_resume_reason:
        merged = dict(capacity_reason)
        merged["pause_resume_guard"] = pause_resume_reason
        merged["operator_note"] = (
            "New paper sampling skipped by validation capacity gate and pause/resume guard; "
            "exit review still runs first and this never affects live trading."
        )
        return merged
    return capacity_reason or pause_resume_reason


def skipped_child(
    label: str,
    reason: dict[str, Any],
    args: list[str] | None = None,
    skipped_status: str = "skipped_capacity_gate",
) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return {
        "label": label,
        "command": args or [],
        "returncode": 0,
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
        "duration_seconds": 0.0,
        "stdout_json": {
            "run_id": f"{now.strftime('%Y%m%d-%H%M')}-{label}",
            "status": skipped_status,
            "live_orders_enabled": False,
            "opened_count": 0,
            "skipped_reason": reason,
        },
        "stdout_parse_error": None,
        "stderr_tail": "",
        "status": "skipped",
    }


def portfolio_metrics(audit_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(audit_payload, dict):
        return {}
    return audit_payload.get("current_portfolio_metrics") or {}


def paper_metrics(audit_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(audit_payload, dict):
        return {}
    return audit_payload.get("paper_sample_metrics") or {}


def progress_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before_portfolio = portfolio_metrics(before)
    after_portfolio = portfolio_metrics(after)
    before_paper = paper_metrics(before)
    after_paper = paper_metrics(after)
    before_plan = validation_plan(before)
    after_plan = validation_plan(after)
    before_gaps = before_plan.get("sample_gaps") or {}
    after_gaps = after_plan.get("sample_gaps") or {}
    return {
        "equity_usd_delta": round(
            (as_float(after_portfolio.get("equity_usd"), 0.0) or 0.0)
            - (as_float(before_portfolio.get("equity_usd"), 0.0) or 0.0),
            6,
        ),
        "net_return_pct_delta": round(
            (as_float(after_portfolio.get("net_return_pct"), 0.0) or 0.0)
            - (as_float(before_portfolio.get("net_return_pct"), 0.0) or 0.0),
            6,
        ),
        "closed_count_delta": as_int(after_paper.get("closed_count")) - as_int(before_paper.get("closed_count")),
        "open_count_delta": as_int(after_paper.get("open_count")) - as_int(before_paper.get("open_count")),
        "paper_order_count_delta": as_int(after_portfolio.get("paper_order_count")) - as_int(before_portfolio.get("paper_order_count")),
        "failed_gates_before": before_plan.get("failed_gates") or [],
        "failed_gates_after": after_plan.get("failed_gates") or [],
        "sample_gaps_before": before_gaps,
        "sample_gaps_after": after_gaps,
        "closed_paper_trades_needed_delta": as_int(after_gaps.get("closed_paper_trades_needed"))
        - as_int(before_gaps.get("closed_paper_trades_needed")),
        "calibration_resolved_needed_delta": as_int(after_gaps.get("calibration_resolved_needed"))
        - as_int(before_gaps.get("calibration_resolved_needed")),
        "walkforward_target_research_pass_needed_delta": as_int(after_gaps.get("walkforward_target_research_pass_needed"))
        - as_int(before_gaps.get("walkforward_target_research_pass_needed")),
        "max_allowed_action_after": after_plan.get("max_allowed_action"),
    }


def child_live_order_errors(children: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for item in children:
        payloads = [item.get("stdout_json") or {}]
        outputs = (item.get("stdout_json") or {}).get("outputs") or {}
        for path_text in outputs.values():
            if isinstance(path_text, str):
                extra = load_json_if_exists(path_text)
                if extra is not None:
                    payloads.append(extra)
        if item.get("persisted_json"):
            extra = load_json_if_exists(item["persisted_json"])
            if extra is not None:
                payloads.append(extra)
        for payload_index, payload in enumerate(payloads):
            for path, obj in walk_objects(payload):
                for key in ("live_orders_enabled", "private_api_used", "private_api_keys_used", "allow_real_orders"):
                    if obj.get(key) is True:
                        errors.append(f"{item['label']} payload[{payload_index}]{path}.{key}=true")
    return errors


def no_entry_block_reason(row: dict[str, Any]) -> str:
    recovery_decision = str(row.get("validation_recovery_decision") or "")
    recovery_match = row.get("validation_recovery_match")
    entry_mode = row.get("entry_mode_estimate")
    if recovery_decision.startswith("block"):
        return f"validation_recovery_plan_block:{recovery_match or 'unknown'}"
    if entry_mode == "not_entry_eligible":
        return "not_entry_eligible"
    if row.get("stage") == "research_watch":
        return "research_watch_only"
    if row.get("total_recent_loss_penalty_points"):
        return "recent_loss_penalty"
    return "not_selected_or_no_current_entry"


def build_no_entry_summary(run: dict[str, Any]) -> dict[str, Any]:
    fast_child = next((item for item in run.get("child_runs", []) if item.get("label") == "fast_crypto_paper_auto_trader"), {})
    fast_payload = fast_child.get("stdout_json") or {}
    near_miss_child = next((item for item in run.get("child_runs", []) if item.get("label") == "current_signal_near_miss_sampler"), {})
    near_miss_payload = near_miss_child.get("stdout_json") or {}
    fast_new = fast_payload.get("new_paper_trades") or []
    near_miss_opened = near_miss_payload.get("opened_positions") or near_miss_payload.get("new_paper_trades") or []
    attribution = fast_payload.get("candidate_attribution_summary") or []
    dynamic_scan = run.get("dynamic_scan_universe") or run.get("dynamic_scan_pool") or {}
    market_context = {
        "market_regime": dynamic_scan.get("market_regime") or "unknown_or_not_attached",
        "market_atmosphere": dynamic_scan.get("market_atmosphere") or "unknown_or_not_attached",
        "short_term_state": dynamic_scan.get("short_term_state") or "unknown_or_not_attached",
        "sentiment_state": dynamic_scan.get("sentiment_state") or "unknown_or_not_attached",
        "pool_width_policy": dynamic_scan.get("pool_width_policy"),
        "pool_shape_policy": dynamic_scan.get("pool_shape_policy"),
        "selected_symbol_count": dynamic_scan.get("selected_symbol_count") or len(dynamic_scan.get("selected_symbols") or []),
        "source": "validation_progress_runner_dynamic_scan",
    }
    reason_counts: dict[str, int] = {}
    top_blocked: list[dict[str, Any]] = []
    for row in attribution[:16]:
        reason = no_entry_block_reason(row)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if len(top_blocked) < 10:
            top_blocked.append(
                {
                    "symbol": row.get("symbol"),
                    "stage": row.get("stage"),
                    "interval": row.get("interval"),
                    "strategy_family": row.get("strategy_family"),
                    "entry_mode_estimate": row.get("entry_mode_estimate"),
                    "info_pressure_score": row.get("info_pressure_score"),
                    "selection_score": row.get("selection_score"),
                    "oos_win_rate_pct": row.get("oos_win_rate_pct"),
                    "oos_net_return_pct": row.get("oos_net_return_pct"),
                    "recent_loss_penalty_points": row.get("total_recent_loss_penalty_points"),
                    "validation_recovery_decision": row.get("validation_recovery_decision"),
                    "validation_recovery_match": row.get("validation_recovery_match"),
                    "primary_block_reason": reason,
                    "market_regime": market_context["market_regime"],
                    "market_atmosphere": market_context["market_atmosphere"],
                    "short_term_state": market_context["short_term_state"],
                    "sentiment_state": market_context["sentiment_state"],
                    "market_context_source": market_context["source"],
                    "market_context": market_context,
                }
            )
    near_miss_skipped = near_miss_payload.get("skipped_candidates") or []
    return {
        "status": "no_new_entry" if not fast_new and not near_miss_opened else "new_entry_recorded",
        "fast_child_status": fast_child.get("status"),
        "near_miss_child_status": near_miss_child.get("status"),
        "fast_new_paper_trades": fast_new,
        "near_miss_opened_positions": near_miss_opened,
        "fast_candidate_count": ((fast_payload.get("scan_summary") or {}).get("deduplicated_candidate_count")),
        "fast_data_status": fast_payload.get("data_status"),
        "fast_top_candidate_count": len(attribution),
        "reason_counts": reason_counts,
        "top_blocked_candidates": top_blocked,
        "near_miss_skipped_count": len(near_miss_skipped),
        "near_miss_skipped": near_miss_skipped[:8],
        "operator_note": (
            "No-entry summary is paper-only diagnosis. It explains why this runner did not add exposure; "
            "it does not authorize live trades or weaken recovery/liquidity/capacity gates."
        ),
    }


def render_report(run: dict[str, Any]) -> str:
    before = run["pre_validation"].get("stdout_json") or {}
    after = run["post_validation"].get("stdout_json") or {}
    delta = run["progress_delta"]
    after_portfolio = portfolio_metrics(after)
    after_paper = paper_metrics(after)
    after_plan = validation_plan(after)
    before_capacity = validation_capacity(before)
    after_capacity = validation_capacity(after)
    recovery = after.get("validation_recovery_plan") or {}
    monthly_target = recovery.get("monthly_target") or {}
    exit_summary = after_paper.get("open_position_exit_summary") or {}
    exit_calendar = after_paper.get("open_exit_calendar") or []
    recovery_optimizer_child = next((item for item in run["child_runs"] if item.get("label") == "strategy_recovery_optimizer"), {})
    recovery_optimizer_payload = recovery_optimizer_child.get("stdout_json") or {}
    recovery_queue = recovery_optimizer_payload.get("eligible_retest_queue") or []
    recovery_blocked = recovery_optimizer_payload.get("blocked_candidates") or []
    recovery_shadow_prefetch_child = next((item for item in run["child_runs"] if item.get("label") == "recovery_shadow_kline_prefetch"), {})
    recovery_shadow_prefetch_payload = recovery_shadow_prefetch_child.get("stdout_json") or {}
    recovery_shadow_child = next((item for item in run["child_runs"] if item.get("label") == "fast_crypto_recovery_shadow_scan"), {})
    recovery_shadow_payload = recovery_shadow_child.get("stdout_json") or {}
    recovery_shadow_scan_summary = recovery_shadow_payload.get("scan_summary") or {}
    recovery_shadow_candidates = recovery_shadow_payload.get("candidate_attribution_summary") or []
    strategy_backlog_child = next((item for item in run["child_runs"] if item.get("label") == "strategy_iteration_backlog"), {})
    strategy_backlog_payload = strategy_backlog_child.get("stdout_json") or {}
    auto_evolver_child = next((item for item in run["child_runs"] if item.get("label") == "paper_strategy_auto_evolver"), {})
    auto_evolver_payload = auto_evolver_child.get("stdout_json") or {}
    pause_resume_guard = run.get("pause_resume_guard") or {}
    pause_resume_reason = pause_resume_guard.get("reason") or {}
    dynamic_scan = run.get("dynamic_scan_universe") or {}
    selected_dynamic_candidates = dynamic_scan.get("selected_dynamic_candidates") or dynamic_scan.get("top_dynamic_candidates") or []
    no_entry_summary = run.get("no_entry_summary") or build_no_entry_summary(run)
    lines = [
        f"# Validation Progress Runner | {run['run_id']}",
        "",
        "This is paper-only validation orchestration. No live orders were placed.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{run['status']}` |",
        f"| Dry run | `{run['dry_run']}` |",
        f"| Offline fixture | `{run['offline_fixture']}` |",
        f"| Include daily deep scan | `{run['include_daily']}` |",
        f"| Include dynamic kline cache | `{run.get('include_dynamic_kline_cache')}` |",
        f"| Include walk-forward refresh | `{run['include_walkforward_refresh']}` |",
        f"| Include near-miss sampler | `{run.get('include_near_miss_sampler')}` |",
        f"| Include strategy recovery optimizer | `{run.get('include_strategy_recovery_optimizer')}` |",
        f"| Aggressive paper compatibility | `{run.get('aggressive_paper_compat')}` |",
        f"| Dynamic scan pool | `{run.get('dynamic_scan_pool_enabled')}` |",
        f"| Dynamic market regime | `{dynamic_scan.get('market_regime')}` |",
        f"| Market atmosphere | `{dynamic_scan.get('market_atmosphere')}` |",
        f"| Short-term state | `{dynamic_scan.get('short_term_state')}` |",
        f"| Short-term overlay | `{dynamic_scan.get('short_term_overlay')}` |",
        f"| Shared paper lock startup | `{(run.get('shared_paper_lock_startup') or {}).get('status')}` |",
        f"| Stale lock cleanup | `{(run.get('stale_lock_cleanup') or {}).get('status')}` |",
        f"| Effective symbol count | `{len(split_symbols(run.get('effective_symbols') or ''))}` |",
        f"| Auto current-signal refresh | `{run.get('auto_current_signal_refresh_enabled')}` |",
        f"| Auto current-signal Kline prefetch | `{run.get('auto_current_signal_kline_prefetch_enabled')}` |",
        f"| Auto strategy recovery optimizer | `{run.get('auto_strategy_recovery_optimizer_enabled')}` |",
        f"| Auto quality scout sampler | `{run.get('auto_quality_scout_sampler_enabled')}` |",
        f"| Paper auto-learning sync | `{run.get('paper_auto_learning_sync_enabled')}` |",
        f"| Pause/resume guard | `{pause_resume_guard.get('triggered')}` |",
        f"| Auto recovery shadow scan | `{run.get('auto_recovery_shadow_scan_enabled')}` |",
        f"| Recovery shadow prefetch | `{run.get('recovery_shadow_prefetch_enabled')}` |",
        f"| Current-signal freshness | `{(run.get('current_signal_refresh_state') or {}).get('status')}` |",
        f"| Strategy recovery queue | `{len(recovery_queue)}` |",
        f"| Recovery status | `{recovery.get('status')}` |",
        f"| New sample policy | `{recovery.get('new_sample_policy')}` |",
        f"| Child failures | `{len(run['child_failures'])}` |",
        f"| Safety errors | `{len(run['safety_errors'])}` |",
        f"| Max allowed action after | `{delta.get('max_allowed_action_after')}` |",
        "",
        "## Paper Auto Learning Sync",
        "",
        "This panel is the post-validation bridge from attribution/backlog into the next paper-only overlay. It never enables live trading.",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Backlog child status | `{strategy_backlog_child.get('status')}` |",
        f"| Active backlog changes | `{strategy_backlog_payload.get('active_proposed_changes')}` |",
        f"| Pending human review | `{strategy_backlog_payload.get('pending_human_review')}` |",
        f"| Backlog report | `{strategy_backlog_payload.get('report') or '-'}` |",
        f"| Evolver child status | `{auto_evolver_child.get('status')}` |",
        f"| Evolver run id | `{auto_evolver_payload.get('run_id') or '-'}` |",
        f"| Strategy version | `{auto_evolver_payload.get('strategy_version') or '-'}` |",
        f"| Applied / A-B testing | `{auto_evolver_payload.get('applied_or_ab_testing')}` |",
        f"| Reverted | `{auto_evolver_payload.get('reverted')}` |",
        f"| Overlay | `{auto_evolver_payload.get('overlay') or '-'}` |",
        f"| Evolver report | `{auto_evolver_payload.get('report') or '-'}` |",
        f"| live_orders_enabled | `false` |",
        f"| private_api_used | `false` |",
        "",
        "## Evidence Delta",
        "",
        "| Metric | Delta |",
        "|---|---:|",
        f"| Equity | {delta['equity_usd_delta']:+.6f} |",
        f"| Net return pct | {delta['net_return_pct_delta']:+.6f} |",
        f"| Closed paper trades | {delta['closed_count_delta']:+d} |",
        f"| Open paper trades | {delta['open_count_delta']:+d} |",
        f"| Paper order count | {delta['paper_order_count_delta']:+d} |",
        f"| Closed paper trades needed | {delta['closed_paper_trades_needed_delta']:+d} |",
        f"| Calibration resolved needed | {delta['calibration_resolved_needed_delta']:+d} |",
        f"| Walk-forward target pass needed | {delta['walkforward_target_research_pass_needed_delta']:+d} |",
        "",
        "## No New Entry Diagnosis",
        "",
        "This panel explains why the runner did or did not add paper exposure. It is diagnosis only; live trading remains disabled.",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{no_entry_summary.get('status')}` |",
        f"| Fast child status | `{no_entry_summary.get('fast_child_status')}` |",
        f"| Near-miss child status | `{no_entry_summary.get('near_miss_child_status')}` |",
        f"| Fast data status | `{no_entry_summary.get('fast_data_status')}` |",
        f"| Fast deduped candidates | `{no_entry_summary.get('fast_candidate_count')}` |",
        f"| Fast paper entries | `{len(no_entry_summary.get('fast_new_paper_trades') or [])}` |",
        f"| Near-miss paper entries | `{len(no_entry_summary.get('near_miss_opened_positions') or [])}` |",
        f"| Near-miss skipped | `{no_entry_summary.get('near_miss_skipped_count')}` |",
        f"| Block reason counts | `{no_entry_summary.get('reason_counts') or {}}` |",
        f"| Operator note | {no_entry_summary.get('operator_note') or '-'} |",
        "",
        "| Rank | Symbol | Stage | Interval | Family | Entry estimate | Info | OOS Win | OOS Return | Block |",
        "|---:|---|---|---|---|---|---:|---:|---:|---|",
    ]
    blocked_rows = no_entry_summary.get("top_blocked_candidates") or []
    if blocked_rows:
        for idx, item in enumerate(blocked_rows[:10], 1):
            lines.append(
                f"| {idx} | `{item.get('symbol')}` | `{item.get('stage')}` | `{item.get('interval')}` | "
                f"`{item.get('strategy_family')}` | `{item.get('entry_mode_estimate')}` | "
                f"`{item.get('info_pressure_score')}` | `{item.get('oos_win_rate_pct')}` | "
                f"`{item.get('oos_net_return_pct')}` | `{item.get('primary_block_reason')}` |"
            )
    else:
        lines.append("| - | none | - | - | - | - | - | - | - | - |")
    near_miss_skipped = no_entry_summary.get("near_miss_skipped") or []
    if near_miss_skipped:
        lines.extend(
            [
                "",
                "| Near-miss symbol | Interval | Reason | Entry mode |",
                "|---|---|---|---|",
            ]
        )
        for item in near_miss_skipped[:6]:
            lines.append(
                f"| `{item.get('symbol')}` | `{item.get('interval')}` | "
                f"`{item.get('reason')}` | `{item.get('paper_entry_mode')}` |"
            )
    lines.append("")
    if run.get("dynamic_scan_pool_enabled"):
        social_refresh = dynamic_scan.get("dynamic_social_refresh") or {}
        kline_filter = dynamic_scan.get("kline_prefetch_filter") or {}
        pool_shape = dynamic_scan.get("pool_shape_policy") or {}
        pool_width = dynamic_scan.get("pool_width_policy") or {}
        social_before = social_refresh.get("handoff_state_before") or {}
        social_after = social_refresh.get("handoff_state_after") or {}
        refresh_result = social_refresh.get("refresh_result") or {}
        lines.extend(
            [
                "## Dynamic Scan Pool",
                "",
                "The scan pool preserves open/explicit symbols first, then fills the remaining slots from live Binance market breadth, liquidity, 24h movement, participation and fresh social handoff scores.",
                "",
                "| Field | Value |",
                "|---|---:|",
                f"| Status | `{dynamic_scan.get('status')}` |",
                f"| Selection policy | {dynamic_scan.get('selection_policy') or '-'} |",
                f"| Configured max symbols | `{pool_width.get('configured_limit', dynamic_scan.get('configured_max_symbols', dynamic_scan.get('max_symbols')) )}` |",
                f"| Effective max symbols | `{pool_width.get('effective_limit', dynamic_scan.get('max_symbols'))}` |",
                f"| Width policy | `{pool_width.get('reason') or '-'}` |",
                f"| Width multiplier | `{pool_width.get('multiplier') or '-'}` |",
                f"| Pool shape reason | `{pool_shape.get('reason') or '-'}` |",
                f"| Pool quota status | `{pool_shape.get('quota_status') or '-'}` |",
                f"| Pool min slots | `{pool_shape.get('min_slots') or {}}` |",
                f"| Pool max slots | `{pool_shape.get('max_slots') or {}}` |",
                f"| Pool selected counts | `{pool_shape.get('selected_counts') or {}}` |",
                f"| Pool min slot gaps | `{pool_shape.get('min_slot_gaps') or {}}` |",
                f"| Pool max slot excess | `{pool_shape.get('max_slot_excess') or {}}` |",
                f"| Market regime | `{dynamic_scan.get('market_regime')}` |",
                f"| Market atmosphere | `{dynamic_scan.get('market_atmosphere')}` |",
                f"| Pool bias | `{dynamic_scan.get('pool_bias')}` |",
                f"| Sentiment state | `{dynamic_scan.get('sentiment_state')}` |",
                f"| Sentiment overlay | `{dynamic_scan.get('sentiment_overlay')}` |",
                f"| Short-term state | `{dynamic_scan.get('short_term_state')}` |",
                f"| Short-term overlay | `{dynamic_scan.get('short_term_overlay')}` |",
                f"| Why pool changed from static baseline | {dynamic_scan.get('why_pool_changed_from_static_baseline') or '-'} |",
                f"| Short-term anchor 1h avg | `{dynamic_scan.get('short_term_avg_1h_anchor_change_pct')}` |",
                f"| Short-term anchor 4h avg | `{dynamic_scan.get('short_term_avg_4h_anchor_change_pct')}` |",
                f"| Positive breadth | `{dynamic_scan.get('positive_breadth_pct')}` |",
                f"| Avg 24h change | `{dynamic_scan.get('average_change_24h_pct')}` |",
                f"| Strong gainers pct | `{dynamic_scan.get('strong_gainers_pct')}` |",
                f"| Hard sellers pct | `{dynamic_scan.get('hard_sellers_pct')}` |",
                f"| Top20 avg 24h | `{dynamic_scan.get('top20_average_change_24h_pct')}` |",
                f"| BTC 24h | `{dynamic_scan.get('btc_change_24h_pct')}` |",
                f"| ETH 24h | `{dynamic_scan.get('eth_change_24h_pct')}` |",
                f"| Liquid USDT symbols | `{dynamic_scan.get('liquid_usdt_symbol_count')}` |",
                f"| Social hits | `{dynamic_scan.get('social_hit_count')}` |",
                f"| Social long total | `{dynamic_scan.get('social_long_total')}` |",
                f"| Social risk total | `{dynamic_scan.get('social_risk_total')}` |",
                f"| Social refresh | `{social_refresh.get('status')}` |",
                f"| Social handoff before | `{social_before.get('status')}` / `{social_before.get('age_hours')}`h |",
                f"| Social handoff after | `{social_after.get('status')}` / `{social_after.get('age_hours')}`h |",
                f"| Social refresh output | `{(refresh_result.get('stdout_json') or {}).get('handoff') or refresh_result.get('operator_note') or '-'}` |",
                f"| Kline prefetch filter | `{kline_filter.get('status')}` |",
                f"| Kline filter cache | `{kline_filter.get('cache_dir') or '-'}` |",
                f"| Kline removed symbols | `{', '.join(kline_filter.get('removed_symbols') or []) or '-'}` |",
                f"| Open symbols preserved | `{', '.join(dynamic_scan.get('open_symbols') or []) or '-'}` |",
                f"| Explicit symbols preserved | `{', '.join(dynamic_scan.get('explicit_symbols') or []) or '-'}` |",
                f"| Effective symbols | `{', '.join(split_symbols(run.get('effective_symbols') or ''))}` |",
                f"| Effective after Kline filter | `{', '.join(kline_filter.get('effective_symbols_after_filter') or split_symbols(run.get('effective_symbols') or ''))}` |",
                "",
                "| Rank | Symbol | Bucket | Score | 24h | Quote Vol | Trades | Long Social | Risk Social | Notes |",
                "|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        if selected_dynamic_candidates:
            for idx, item in enumerate(selected_dynamic_candidates[:12], 1):
                notes = "; ".join(item.get("social_notes") or []) or "-"
                lines.append(
                    f"| {idx} | `{item.get('symbol')}` | `{item.get('bucket')}` | `{item.get('score')}` | "
                    f"`{item.get('price_change_24h_pct')}` | `{item.get('quote_volume_24h_usd')}` | "
                    f"`{item.get('trade_count_24h')}` | `{item.get('social_long_score')}` | "
                    f"`{item.get('social_risk_score')}` | {notes} |"
                )
        else:
            lines.append("| - | none | - | - | - | - | - | - | - | - |")
        lines.append("")
    if pause_resume_guard.get("triggered"):
        lines.extend(
            [
                "## Pause/Resume Guard",
                "",
                "New paper sampling is blocked for this runner pass until exit review refreshes stale or expired open-position evidence.",
                "",
                "| Field | Value |",
                "|---|---:|",
                f"| Reason | `{pause_resume_reason.get('reason')}` |",
                f"| Ledger updated at | `{pause_resume_reason.get('ledger_updated_at')}` |",
                f"| Ledger age hours | `{pause_resume_reason.get('ledger_age_hours')}` |",
                f"| Max ledger age hours | `{pause_resume_reason.get('max_ledger_age_hours')}` |",
                f"| Open count | `{pause_resume_reason.get('open_count')}` |",
                f"| Expired positions | `{len(pause_resume_reason.get('expired_positions') or [])}` |",
                f"| Near-expiry positions | `{len(pause_resume_reason.get('near_expiry_positions') or [])}` |",
                f"| Operator note | {pause_resume_reason.get('operator_note') or '-'} |",
                "",
            ]
        )
    lines.extend([
        "## Current Validation State",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Equity | {after_portfolio.get('equity_usd')} |",
        f"| Net return | {after_portfolio.get('net_return_pct')}% |",
        f"| Max drawdown | {after_portfolio.get('max_drawdown_pct')}% |",
        f"| Open count | {after_portfolio.get('open_count')} |",
        f"| Closed count | {after_paper.get('closed_count')} |",
        f"| Win rate | {after_paper.get('win_rate_pct')}% |",
        f"| Closed net return | {after_paper.get('realized_net_return_on_closed_notional_pct')}% |",
        "",
        "## Validation Capacity",
        "",
        "| Field | Before | After |",
        "|---|---:|---:|",
        f"| Sample action | `{before_capacity.get('sample_action')}` | `{after_capacity.get('sample_action')}` |",
        f"| Runner mode | `{before_capacity.get('recommended_runner_mode')}` | `{after_capacity.get('recommended_runner_mode')}` |",
        f"| Open count | `{before_capacity.get('open_count')}` | `{after_capacity.get('open_count')}` |",
        f"| Effective max open | `{before_capacity.get('effective_max_open_positions')}` | `{after_capacity.get('effective_max_open_positions')}` |",
        f"| Open slots remaining | `{before_capacity.get('open_slots_remaining')}` | `{after_capacity.get('open_slots_remaining')}` |",
        f"| Open exposure | `{before_capacity.get('open_exposure_pct')}` | `{after_capacity.get('open_exposure_pct')}` |",
        "",
        "## Open Paper Review Calendar",
        "",
        "This table shows when open paper positions are likely to become reviewable samples. It is evidence scheduling only, not a live exit instruction.",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Open positions | `{exit_summary.get('open_count')}` |",
        f"| Expiring in 24h | `{exit_summary.get('expiring_24h_count')}` |",
        f"| Expiring in 72h | `{exit_summary.get('expiring_72h_count')}` |",
        f"| Nearest expiry | `{exit_summary.get('nearest_expiry_at')}` |",
        f"| Avg unrealized PnL | `{exit_summary.get('avg_unrealized_pnl_pct')}` |",
        "",
        "| Trade | Symbol | Entry mode | Expires | Hours to expiry | Unrealized | Protection | Trail floor | Floor dist | Source |",
        "|---|---|---|---|---:|---:|---|---:|---:|---|",
    ])
    if exit_calendar:
        for item in exit_calendar[:8]:
            protection = "armed" if item.get("profit_protection_armed") else "watch"
            lines.append(
                f"| `{item.get('paper_trade_id')}` | `{item.get('symbol')}` | `{item.get('paper_entry_mode')}` | "
                f"`{item.get('expires_at')}` | `{item.get('hours_to_expiry')}` | "
                f"`{item.get('unrealized_pnl_pct')}` | `{protection}` | "
                f"`{item.get('trailing_floor_pct')}` | `{item.get('distance_to_trailing_floor_pct')}` | "
                f"`{item.get('source_path')}` |"
            )
    else:
        lines.append("| none | - | - | - | - | - | - | - | - | - |")
    lines.extend([
        "",
        "## Failed Gates After",
        "",
    ])
    failed_gates = after_plan.get("failed_gates") or []
    if failed_gates:
        lines.extend([f"- `{gate}`" for gate in failed_gates])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Monthly Target Recovery",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| Target model | `{monthly_target.get('target_model')}` |",
            f"| Month | `{monthly_target.get('month_id')}` |",
            f"| Baseline source | `{monthly_target.get('baseline_source')}` |",
            f"| Month-start equity | `{monthly_target.get('month_start_equity_usd')}` |",
            f"| Lifetime initial capital | `{monthly_target.get('lifetime_initial_capital_usd')}` |",
            f"| Target equity | `{monthly_target.get('target_equity_usd')}` |",
            f"| Current equity | `{monthly_target.get('current_equity_usd')}` |",
            f"| Target gap | `{monthly_target.get('target_gap_usd')}` |",
            f"| Progress | `{monthly_target.get('progress_pct')}` |",
            f"| Required return from current equity | `{monthly_target.get('required_return_pct_from_current_equity')}` |",
            "",
            "## Recovery Actions",
            "",
        ]
    )
    actions = recovery.get("next_actions") or []
    if actions:
        lines.extend([f"- {item}" for item in actions])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Strategy Quality Triage",
            "",
            "| Group | Name | Status | Closed | Win Rate | Net Return |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    triage_rows = []
    for group_key, group_label in (
        ("retired_entry_modes", "entry_mode"),
        ("cooldown_entry_modes", "entry_mode"),
        ("eligible_entry_modes", "entry_mode"),
        ("retired_strategy_families", "strategy_family"),
        ("cooldown_strategy_families", "strategy_family"),
        ("eligible_strategy_families", "strategy_family"),
    ):
        for item in recovery.get(group_key) or []:
            triage_rows.append((group_label, item))
    if triage_rows:
        for group_label, item in triage_rows[:16]:
            lines.append(
                f"| `{group_label}` | `{item.get('name')}` | `{item.get('status')}` | "
                f"`{item.get('closed_count')}` | `{item.get('win_rate_pct')}` | `{item.get('realized_net_return_pct')}` |"
            )
    else:
        lines.append("| none | - | - | 0 | - | - |")
    lines.extend(
        [
            "",
            "## Strategy Recovery Optimizer",
            "",
            "Research-only queue for future small paper retests. These rows do not open paper positions and do not authorize live trading.",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| Status | `{recovery_optimizer_payload.get('status')}` |",
            f"| Persisted JSON | `{recovery_optimizer_child.get('persisted_json')}` |",
            f"| Eligible retest candidates | `{len(recovery_queue)}` |",
            f"| Blocked candidates | `{len(recovery_blocked)}` |",
            "",
            "| Rank | Symbol | Interval | Family | Entry mode | Stage | Current signal | Score |",
            "|---:|---|---|---|---|---|---:|---:|",
        ]
    )
    if recovery_queue:
        for idx, item in enumerate(recovery_queue[:8], 1):
            lines.append(
                f"| {idx} | `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('strategy_family')}` | "
                f"`{item.get('paper_entry_mode')}` | `{item.get('stage')}` | `{item.get('current_signal')}` | `{item.get('score')}` |"
            )
    else:
        lines.append("| - | none | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "| Blocked Symbol | Interval | Family | Entry mode | Block | Reason |",
            "|---|---|---|---|---|---|",
        ]
    )
    if recovery_blocked:
        for item in recovery_blocked[:8]:
            block = item.get("recovery_block") or {}
            lines.append(
                f"| `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('strategy_family')}` | "
                f"`{item.get('paper_entry_mode')}` | `{block.get('matched_group')}` | `{block.get('reason')}` |"
            )
    else:
        lines.append("| none | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Recovery Shadow Kline Prefetch",
            "",
            "Public Binance Kline cache prefetch for recovery queue symbols before the dry-run shadow scan. This is market-data only and does not touch account or order endpoints.",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| Status | `{recovery_shadow_prefetch_child.get('status')}` |",
            f"| Child stdout status | `{recovery_shadow_prefetch_payload.get('status')}` |",
            f"| Persisted JSON | `{recovery_shadow_prefetch_child.get('persisted_json')}` |",
            f"| Cache dir | `{recovery_shadow_prefetch_payload.get('cache_dir') or (recovery_shadow_prefetch_payload.get('recovery_shadow_prefetch') or {}).get('cache_dir')}` |",
            f"| Selected symbols | `{', '.join(recovery_shadow_prefetch_payload.get('selected_symbols') or [])}` |",
            f"| Requested intervals | `{', '.join(recovery_shadow_prefetch_payload.get('requested_intervals') or [])}` |",
            f"| Files written | `{recovery_shadow_prefetch_payload.get('file_count')}` |",
            f"| Failures | `{recovery_shadow_prefetch_payload.get('failure_count')}` |",
            "",
            "## Recovery Shadow Scan",
            "",
            "This is a dry-run only scan used while new paper sampling is paused. It may ignore the validation capacity gate for observation, but it cannot open paper positions.",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| Status | `{recovery_shadow_child.get('status')}` |",
            f"| Child stdout status | `{recovery_shadow_payload.get('status')}` |",
            f"| Dry run | `{recovery_shadow_payload.get('dry_run')}` |",
            f"| Data status | `{recovery_shadow_payload.get('data_status')}` |",
            f"| Frames loaded | `{recovery_shadow_scan_summary.get('frames_loaded')}` |",
            f"| Strategies scanned | `{recovery_shadow_scan_summary.get('strategies_scanned')}` |",
            f"| Current-signal strategies | `{recovery_shadow_scan_summary.get('current_signal_strategies')}` |",
            f"| Deduplicated candidates | `{recovery_shadow_scan_summary.get('deduplicated_candidate_count')}` |",
            f"| Hypothetical paper trades | `{recovery_shadow_payload.get('new_paper_trades')}` |",
            "",
            "| Symbol | Stage | Interval | Family | Queue bonus | Queue decision | Score |",
            "|---|---|---|---|---:|---|---:|",
        ]
    )
    if recovery_shadow_candidates:
        for item in recovery_shadow_candidates[:8]:
            lines.append(
                f"| `{item.get('symbol')}` | `{item.get('stage')}` | `{item.get('interval')}` | "
                f"`{item.get('strategy_family')}` | `{item.get('strategy_recovery_queue_bonus_points')}` | "
                f"`{item.get('strategy_recovery_queue_decision')}` | `{item.get('selection_score')}` |"
            )
    else:
        lines.append("| none | - | - | - | - | - | - |")
    lines.extend(["", "## Child Runs", "", "| Step | Status | Return | Run ID | Output |", "|---|---|---:|---|---|"])
    for child in run["child_runs"]:
        payload = child.get("stdout_json") or {}
        outputs = payload.get("outputs") or {}
        output = child.get("persisted_json") or outputs.get("report") or outputs.get("experiment") or child.get("persist_skipped_reason") or "-"
        lines.append(
            f"| {child['label']} | `{child['status']}` | {child['returncode']} | "
            f"`{payload.get('run_id') or '-'}` | `{output}` |"
        )
    if run["child_failures"]:
        lines.extend(["", "## Child Failures", ""])
        for failure in run["child_failures"]:
            lines.append(f"- `{failure['label']}`: {failure.get('stdout_parse_error') or failure.get('stderr_tail') or 'failed'}")
    if run["safety_errors"]:
        lines.extend(["", "## Safety Errors", ""])
        for error in run["safety_errors"]:
            lines.append(f"- {error}")
    lines.extend(
        [
            "",
            "## Next Validation Queue",
            "",
            "| Symbol | Interval | Stage | Why not live ready |",
            "|---|---|---|---|",
        ]
    )
    queue = after_plan.get("next_validation_queue") or []
    if queue:
        for item in queue[:8]:
            lines.append(
                f"| `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('stage')}` | "
                f"{item.get('why_not_live_ready') or '-'} |"
            )
    else:
        lines.append("| none | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run paper validation steps and compare validation sample progress")
    parser.add_argument("--cycles", type=int, default=1, help="Number of exit/fast/daily cycles to run")
    parser.add_argument("--include-daily", action="store_true", help="Also run the heavier daily deep paper scan each cycle")
    parser.add_argument("--include-walkforward-refresh", action="store_true", help="Persist a fresh weekly-goal walk-forward evidence file before post-audit")
    parser.add_argument("--include-current-signal-probe", action="store_true", help="Persist a fresh current-signal probe before post-audit")
    parser.add_argument("--include-near-miss-sampler", action="store_true", help="Open tiny paper samples for current-signal near misses")
    parser.add_argument("--include-strategy-recovery-optimizer", action="store_true", help="Persist a paper-only recovery strategy queue after validation evidence")
    parser.add_argument("--include-dynamic-kline-cache", action="store_true", help="Fetch a fresh Binance public kline cache before walk-forward/current-signal refresh")
    parser.add_argument(
        "--disable-auto-current-signal-refresh",
        action="store_true",
        help="Disable the default paused-capacity stale current-signal refresh check",
    )
    parser.add_argument(
        "--disable-auto-strategy-recovery-optimizer",
        action="store_true",
        help="Disable the default paused-capacity research-only recovery optimizer",
    )
    parser.add_argument(
        "--disable-paper-auto-learning-sync",
        action="store_true",
        help="Skip post-validation strategy backlog merge and paper-only auto evolver overlay sync",
    )
    parser.add_argument(
        "--disable-auto-quality-scout-sampler",
        action="store_true",
        help="Disable the default paused-capacity strict current-signal quality scout sampler",
    )
    parser.add_argument(
        "--include-recovery-shadow-scan",
        action="store_true",
        help="Run a dry-run recovery shadow scan even if the validation capacity gate is not paused",
    )
    parser.add_argument(
        "--disable-auto-recovery-shadow-scan",
        action="store_true",
        help="Disable the default paused-capacity dry-run recovery shadow scan",
    )
    parser.add_argument(
        "--disable-recovery-shadow-kline-prefetch",
        action="store_true",
        help="Disable public Binance Kline prefetch before recovery shadow scan",
    )
    parser.add_argument(
        "--current-signal-stale-hours",
        type=float,
        default=6.0,
        help="Refresh current-signal probe evidence when latest evidence is older than this many hours",
    )
    parser.add_argument(
        "--disable-pause-resume-guard",
        action="store_true",
        help="Disable the default guard that blocks new paper sampling when the ledger is stale or open positions are expired",
    )
    parser.add_argument(
        "--resume-guard-max-ledger-age-hours",
        type=float,
        default=2.0,
        help="With open paper positions, skip new sampling if ledger updated_at is older than this many hours",
    )
    parser.add_argument(
        "--resume-guard-expiry-lookahead-hours",
        type=float,
        default=0.0,
        help="With open paper positions, skip new sampling when a position expires within this many hours; 0 only blocks already expired positions",
    )
    parser.add_argument("--skip-exit", action="store_true")
    parser.add_argument("--skip-fast", action="store_true")
    parser.add_argument("--skip-validation-after", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Pass dry-run to child scripts and skip runner artifact writes")
    parser.add_argument("--offline-fixture", action="store_true", help="Pass offline-fixture to child scripts")
    parser.add_argument("--no-lock", action="store_true")
    parser.add_argument("--external-agent-outputs-json", default="")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--aggressive-paper", action="store_true", help="Compatibility mode: build a dynamic high-movement scan universe for paper-only validation.")
    parser.add_argument("--aggressive-notional", type=float, default=75.0, help="Compatibility field for older automation prompts; sizing remains controlled by child paper scanners.")
    parser.add_argument("--aggressive-max-new-positions", type=int, default=2, help="Compatibility field for older automation prompts; child scanners still enforce capacity gates.")
    parser.add_argument(
        "--dynamic-scan-pool",
        action="store_true",
        help="Compatibility flag; dynamic scan pool is enabled by default unless disabled or running an offline fixture.",
    )
    parser.add_argument("--disable-dynamic-scan-pool", action="store_true")
    parser.add_argument("--dynamic-max-symbols", type=int, default=DEFAULT_VALIDATION_DYNAMIC_MAX_SYMBOLS)
    parser.add_argument(
        "--disable-dynamic-social-refresh",
        action="store_true",
        help="Do not refresh the crypto social/key-person handoff before building the dynamic scan pool.",
    )
    parser.add_argument("--dynamic-social-stale-hours", type=float, default=12.0)
    parser.add_argument("--dynamic-social-max-network-queries", type=int, default=10)
    parser.add_argument("--dynamic-social-timeout-seconds", type=int, default=90)
    parser.add_argument("--fast-intervals", default="")
    parser.add_argument("--recovery-shadow-intervals", default="1m,5m,15m,1h,4h,1d")
    parser.add_argument(
        "--recovery-shadow-timeout-seconds",
        type=int,
        default=180,
        help="Dedicated timeout for the observation-only recovery shadow scan; keeps dry-run shadow work from blocking the main paper loop.",
    )
    parser.add_argument("--recovery-shadow-cache-dir", default="")
    parser.add_argument("--recovery-shadow-prefetch-max-symbols", type=int, default=6)
    parser.add_argument("--recovery-shadow-prefetch-min-bars", type=int, default=250)
    parser.add_argument("--recovery-shadow-prefetch-target-bars", type=int, default=600)
    parser.add_argument("--daily-intervals", default="")
    parser.add_argument("--walkforward-intervals", default="1h,4h,1d")
    parser.add_argument(
        "--walkforward-cache-dirs",
        default="",
        help="Comma-separated kline cache dirs for weekly_goal_strategy_lab/current_signal_probe. Defaults to populated known caches.",
    )
    parser.add_argument(
        "--dynamic-cache-dir",
        default="",
        help="Cache dir for --include-dynamic-kline-cache. Defaults to active-alpha-paper-monitor/cache/binance_klines/dynamic_YYYYMMDD.",
    )
    parser.add_argument("--dynamic-top-symbols", type=int, default=30)
    parser.add_argument("--dynamic-cache-intervals", default="1m,5m,15m,1h,4h,1d")
    parser.add_argument(
        "--dynamic-cache-max-kline-requests",
        type=int,
        default=525,
        help="Hard request budget for the durable Binance public Kline cache builder.",
    )
    parser.add_argument("--paper-dir", default=str(ACTIVE_ROOT / "paper_trades"))
    parser.add_argument("--experiment-dir", default=str(ACTIVE_ROOT / "experiments"))
    parser.add_argument(
        "--recommendation-ledger",
        default=str(WORKSPACE_ROOT / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"),
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--exit-timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--fast-timeout-seconds",
        type=int,
        default=240,
        help="Timeout for the fast paper scan. Keep below the hourly automation interval so a slow market-data fetch degrades to report-only instead of blocking the loop.",
    )
    parser.add_argument("--market-fetch-wall-clock-seconds", type=int, default=75)
    parser.add_argument("--kline-fetch-wall-clock-seconds", type=int, default=75)
    parser.add_argument("--info-fetch-wall-clock-seconds", type=int, default=45)
    parser.add_argument("--daily-timeout-seconds", type=int, default=2400)
    parser.add_argument("--validation-timeout-seconds", type=int, default=120)
    parser.add_argument("--walkforward-timeout-seconds", type=int, default=900)
    parser.add_argument("--current-signal-timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--current-signal-max-symbols",
        type=int,
        default=24,
        help="Maximum effective symbols passed to current_signal_probe; caps research refresh width separately from the live fast scan.",
    )
    parser.add_argument("--dynamic-cache-timeout-seconds", type=int, default=1200)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    now = local_now()
    stamp = artifact_stamp(now)
    run_id = f"{stamp}-validation-progress-runner"
    startup_lock_state: dict[str, Any] = {"status": "not_checked", "path": str(SHARED_PAPER_LOCK_PATH)}
    stale_lock_cleanup: dict[str, Any] = {"status": "not_needed"}
    if not args.no_lock:
        startup_lock_state = shared_paper_lock_state()
        if startup_lock_state.get("status") == "held":
            return emit_lock_held_run(args, now, run_id)
        if startup_lock_state.get("status") == "stale":
            stale_lock_cleanup = clear_stale_shared_paper_lock(startup_lock_state)
            if stale_lock_cleanup.get("status") == "failed":
                return emit_lock_held_run(args, now, run_id)
    explicit_symbols_initial = split_symbols(args.symbols)
    use_dynamic_scan_pool = not args.disable_dynamic_scan_pool and not args.offline_fixture
    dynamic_social_refresh: dict[str, Any] = {
        "enabled": False,
        "status": "disabled",
        "reason": "dynamic_scan_pool_disabled_or_social_refresh_disabled",
    }
    dynamic_scan_universe: dict[str, Any] = {
        "status": "disabled",
        "reason": "dynamic_scan_pool_disabled_or_offline_fixture",
        "selected_symbols": split_symbols(args.symbols),
    }
    if use_dynamic_scan_pool:
        social_state = social_handoff_state(args.dynamic_social_stale_hours)
        dynamic_social_refresh = {
            "enabled": not args.disable_dynamic_social_refresh,
            "handoff_state_before": social_state,
            "status": "skipped_fresh_handoff" if not social_state.get("should_refresh") else "pending",
        }
        if not args.disable_dynamic_social_refresh and social_state.get("should_refresh"):
            seed_symbols = [
                *load_open_symbols(),
                *split_symbols(args.symbols),
                *CORE_LIQUIDITY_SYMBOLS,
                *DEFAULT_DYNAMIC_SCAN_SYMBOLS,
            ]
            dynamic_social_refresh["refresh_result"] = refresh_social_handoff(
                seed_symbols,
                max_network_queries=args.dynamic_social_max_network_queries,
                timeout_seconds=args.dynamic_social_timeout_seconds,
            )
            dynamic_social_refresh["handoff_state_after"] = social_handoff_state(args.dynamic_social_stale_hours)
            dynamic_social_refresh["status"] = (dynamic_social_refresh.get("refresh_result") or {}).get("status")
        dynamic_symbols, dynamic_scan_universe = build_dynamic_scan_universe(
            args.symbols,
            max_symbols=max(3, int(args.dynamic_max_symbols or DEFAULT_VALIDATION_DYNAMIC_MAX_SYMBOLS)),
        )
        dynamic_scan_universe["dynamic_social_refresh"] = dynamic_social_refresh
        args.symbols = dynamic_symbols
    args.dynamic_scan_universe = dynamic_scan_universe
    child_runs: list[dict[str, Any]] = []

    pre_validation = validation_audit("pre_validation_sample_audit", args)
    paper_risk_control_run = paper_testnet_risk_control_audit(args)
    paper_risk_control_run["cycle"] = "pre"
    child_runs.append(paper_risk_control_run)
    paper_risk_payload = (
        paper_risk_control_run.get("stdout_json")
        if isinstance(paper_risk_control_run.get("stdout_json"), dict)
        else {}
    )
    paper_risk_state = (
        paper_risk_payload.get("risk_control")
        if isinstance(paper_risk_payload.get("risk_control"), dict)
        else {}
    )
    paper_risk_skip_reason = None
    if paper_risk_control_run.get("status") != "ok":
        paper_risk_skip_reason = {
            "status": "paper_testnet_risk_control_audit_failed",
            "reason": "paper_testnet_risk_control_audit_failed",
        }
    elif paper_risk_state.get("risk_decision") == "block_new_entries":
        paper_risk_skip_reason = {
            "status": "paper_testnet_risk_control_block",
            "reason": "paper_testnet_risk_control_block_new_entries",
            "triggers": paper_risk_state.get("triggers") or [],
            "safety_errors": paper_risk_state.get("safety_errors") or [],
            "integrity_errors": paper_risk_state.get("integrity_errors") or [],
        }
    pre_payload = pre_validation.get("stdout_json") if isinstance(pre_validation.get("stdout_json"), dict) else {}
    capacity_skip_reason = near_miss_capacity_skip(pre_payload)
    pause_resume_skip_reason = pause_resume_guard_skip(args, now)
    new_sample_skip_reason = merge_skip_reasons(
        merge_skip_reasons(capacity_skip_reason, pause_resume_skip_reason),
        paper_risk_skip_reason,
    )
    near_miss_skip_reason = new_sample_skip_reason
    auto_current_signal_refresh_enabled = (
        not args.include_current_signal_probe
        and not args.disable_auto_current_signal_refresh
    )
    auto_strategy_recovery_optimizer_enabled = (
        bool(new_sample_skip_reason)
        and not args.include_strategy_recovery_optimizer
        and not args.disable_auto_strategy_recovery_optimizer
    )
    auto_quality_scout_sampler_enabled = (
        bool(new_sample_skip_reason)
        and not args.include_near_miss_sampler
        and not args.disable_auto_quality_scout_sampler
    )
    auto_recovery_shadow_scan_enabled = (
        bool(new_sample_skip_reason)
        and not args.disable_auto_recovery_shadow_scan
        and not args.skip_fast
    )
    recovery_shadow_plan = recovery_shadow_prefetch_plan(args)
    current_signal_refresh_state = latest_current_signal_probe_state(args, now)
    auto_current_signal_refresh_required = (
        auto_current_signal_refresh_enabled
        and bool(current_signal_refresh_state.get("should_refresh"))
    )
    auto_current_signal_kline_prefetch_enabled = (
        auto_current_signal_refresh_required
        and not args.include_dynamic_kline_cache
    )
    auto_current_signal_refresh_consumed = False
    if args.include_dynamic_kline_cache or auto_current_signal_kline_prefetch_enabled:
        child = dynamic_kline_cache_builder(args)
        child["auto_current_signal_kline_prefetch"] = auto_current_signal_kline_prefetch_enabled
        apply_dynamic_kline_prefetch_filter(
            args,
            child,
            protected_symbols=[*load_open_symbols(), *explicit_symbols_initial],
            dynamic_scan_universe=dynamic_scan_universe,
        )
        child_runs.append(child)
    cycles = max(1, args.cycles)
    for index in range(cycles):
        if not args.skip_exit:
            child = paper_exit_monitor(args)
            child["cycle"] = index + 1
            child_runs.append(child)
        if not args.skip_fast:
            if new_sample_skip_reason:
                child = skipped_child(
                    "fast_crypto_paper_auto_trader",
                    new_sample_skip_reason,
                    script_cmd("fast_crypto_paper_auto_trader.py") + ["--compact-output", "--allow-outside-window"],
                )
            else:
                child = fast_paper_scan(args)
            child["cycle"] = index + 1
            child_runs.append(child)
        if args.include_recovery_shadow_scan or auto_recovery_shadow_scan_enabled:
            if not args.disable_recovery_shadow_kline_prefetch:
                child = recovery_shadow_kline_prefetch(args, recovery_shadow_plan)
                child["cycle"] = index + 1
                child["auto_recovery_shadow_prefetch"] = auto_recovery_shadow_scan_enabled
                child_runs.append(child)
            child = recovery_shadow_scan(args, new_sample_skip_reason, recovery_shadow_plan)
            child["cycle"] = index + 1
            child["auto_recovery_shadow_scan"] = auto_recovery_shadow_scan_enabled
            child_runs.append(child)
        if args.include_daily:
            if new_sample_skip_reason:
                child = skipped_child(
                    "daily_crypto_paper_auto_trader",
                    new_sample_skip_reason,
                    script_cmd("daily_crypto_paper_auto_trader.py") + ["--compact-output", "--allow-outside-window"],
                )
            else:
                child = daily_paper_scan(args)
            child["cycle"] = index + 1
            child_runs.append(child)
        if args.include_walkforward_refresh:
            child = walkforward_refresh(args)
            child["cycle"] = index + 1
            child_runs.append(child)
        if args.include_current_signal_probe:
            child = current_signal_probe(args)
            child["cycle"] = index + 1
            child_runs.append(child)
        elif auto_current_signal_refresh_enabled:
            if auto_current_signal_refresh_required and not auto_current_signal_refresh_consumed:
                child = current_signal_probe(args)
                auto_current_signal_refresh_consumed = True
            else:
                if auto_current_signal_refresh_consumed:
                    refresh_skip_reason = {
                        **current_signal_refresh_state,
                        "status": "already_refreshed_this_runner",
                        "operator_note": "New paper sampling is paused; current-signal research was already refreshed once in this runner invocation.",
                    }
                    skipped_status = "skipped_already_refreshed"
                else:
                    refresh_skip_reason = {
                        **current_signal_refresh_state,
                        "operator_note": "New paper sampling is paused; current-signal research refresh skipped because the latest evidence is still fresh.",
                    }
                    skipped_status = "skipped_fresh_evidence"
                child = skipped_child(
                    "current_signal_probe",
                    refresh_skip_reason,
                    script_cmd("current_signal_probe.py"),
                    skipped_status=skipped_status,
                )
            child["cycle"] = index + 1
            child["auto_current_signal_refresh"] = True
            child_runs.append(child)
        if auto_quality_scout_sampler_enabled:
            child = current_signal_robustness_audit(args)
            child["cycle"] = index + 1
            child["required_before_three_segment_retest"] = True
            child_runs.append(child)
            child = current_signal_quality_scout_sampler(args)
            child["cycle"] = index + 1
            child["auto_quality_scout_sampler"] = True
            child["capacity_gate_reason"] = new_sample_skip_reason
            child_runs.append(child)
        if args.include_near_miss_sampler:
            if not auto_quality_scout_sampler_enabled:
                child = current_signal_robustness_audit(args)
                child["cycle"] = index + 1
                child["required_before_three_segment_retest"] = True
                child_runs.append(child)
            if near_miss_skip_reason:
                child = skipped_child(
                    "current_signal_near_miss_sampler",
                    near_miss_skip_reason,
                    script_cmd("current_signal_near_miss_sampler.py") + ["--format", "json"],
                )
            else:
                child = current_signal_near_miss_sampler(args)
            child["cycle"] = index + 1
            child_runs.append(child)

    if args.include_strategy_recovery_optimizer or auto_strategy_recovery_optimizer_enabled:
        child = strategy_recovery_optimizer(args)
        child["cycle"] = "post"
        child["auto_strategy_recovery_optimizer"] = auto_strategy_recovery_optimizer_enabled
        child_runs.append(child)

    post_validation = pre_validation if args.skip_validation_after else validation_audit("post_validation_sample_audit", args)

    if not args.disable_paper_auto_learning_sync:
        child = strategy_iteration_backlog_sync(args)
        child["cycle"] = "post"
        child["paper_auto_learning_sync"] = True
        child_runs.append(child)
        child = paper_strategy_auto_evolver(args)
        child["cycle"] = "post"
        child["paper_auto_learning_sync"] = True
        child_runs.append(child)

    child_failures = [item for item in child_runs if item["status"] not in {"ok", "skipped"}]
    safety_errors = child_live_order_errors(child_runs)
    status = "ok"
    if child_failures:
        status = "degraded_child_failure"
    if safety_errors:
        status = "blocked_safety_error"

    run = {
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "local_time": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "runner_version": "validation-progress-runner-v1",
        "live_orders_enabled": False,
        "private_api_used": False,
        "private_api_keys_used": False,
        "allow_real_orders": False,
        "dry_run": args.dry_run,
        "offline_fixture": args.offline_fixture,
        "cycles": cycles,
        "include_daily": args.include_daily,
        "include_dynamic_kline_cache": args.include_dynamic_kline_cache,
        "include_walkforward_refresh": args.include_walkforward_refresh,
        "include_current_signal_probe": args.include_current_signal_probe,
        "include_near_miss_sampler": args.include_near_miss_sampler,
        "include_strategy_recovery_optimizer": args.include_strategy_recovery_optimizer,
        "aggressive_paper_compat": bool(args.aggressive_paper),
        "aggressive_notional_compat": args.aggressive_notional if args.aggressive_paper else None,
        "aggressive_max_new_positions_compat": args.aggressive_max_new_positions if args.aggressive_paper else None,
        "dynamic_scan_pool_enabled": bool(use_dynamic_scan_pool),
        "dynamic_scan_universe": dynamic_scan_universe,
        "shared_paper_lock_startup": startup_lock_state,
        "stale_lock_cleanup": stale_lock_cleanup,
        "effective_symbols": args.symbols,
        "auto_current_signal_refresh_enabled": auto_current_signal_refresh_enabled,
        "auto_current_signal_refresh_required": auto_current_signal_refresh_required,
        "auto_current_signal_kline_prefetch_enabled": auto_current_signal_kline_prefetch_enabled,
        "auto_strategy_recovery_optimizer_enabled": auto_strategy_recovery_optimizer_enabled,
        "auto_quality_scout_sampler_enabled": auto_quality_scout_sampler_enabled,
        "pause_resume_guard": {
            "enabled": not args.disable_pause_resume_guard,
            "triggered": bool(pause_resume_skip_reason),
            "reason": pause_resume_skip_reason,
            "max_ledger_age_hours": args.resume_guard_max_ledger_age_hours,
            "expiry_lookahead_hours": args.resume_guard_expiry_lookahead_hours,
        },
        "auto_recovery_shadow_scan_enabled": auto_recovery_shadow_scan_enabled,
        "paper_auto_learning_sync_enabled": not args.disable_paper_auto_learning_sync,
        "include_recovery_shadow_scan": args.include_recovery_shadow_scan,
        "recovery_shadow_prefetch_enabled": not args.disable_recovery_shadow_kline_prefetch,
        "recovery_shadow_prefetch_plan": recovery_shadow_plan,
        "current_signal_refresh_state": current_signal_refresh_state,
        "near_miss_sampler_capacity_gate": near_miss_skip_reason,
        "status": status,
        "pre_validation": pre_validation,
        "post_validation": post_validation,
        "paper_testnet_risk_control": paper_risk_state,
        "paper_testnet_risk_control_run": paper_risk_control_run,
        "child_runs": child_runs,
        "child_failures": child_failures,
        "safety_errors": safety_errors,
        "progress_delta": progress_delta(pre_validation.get("stdout_json"), post_validation.get("stdout_json")),
        "outputs": {},
    }
    run["no_entry_summary"] = build_no_entry_summary(run)

    date = now.strftime("%Y-%m-%d")
    report_path = ACTIVE_ROOT / "reports" / f"{date}-validation-progress-runner-{stamp}.md"
    experiment_path = ACTIVE_ROOT / "experiments" / f"{stamp}-validation-progress-runner.json"
    run["outputs"] = {
        "report": str(report_path.relative_to(WORKSPACE_ROOT)),
        "experiment": str(experiment_path.relative_to(WORKSPACE_ROOT)),
    }

    post_payload = post_validation.get("stdout_json") if isinstance(post_validation.get("stdout_json"), dict) else {}
    post_paper = paper_metrics(post_payload)
    strategy_recovery_child = next((item for item in child_runs if item.get("label") == "strategy_recovery_optimizer"), {})
    strategy_recovery_payload = strategy_recovery_child.get("stdout_json") or {}
    strategy_recovery_queue = strategy_recovery_payload.get("eligible_retest_queue") or []
    strategy_backlog_child = next((item for item in child_runs if item.get("label") == "strategy_iteration_backlog"), {})
    strategy_backlog_payload = strategy_backlog_child.get("stdout_json") or {}
    auto_evolver_child = next((item for item in child_runs if item.get("label") == "paper_strategy_auto_evolver"), {})
    auto_evolver_payload = auto_evolver_child.get("stdout_json") or {}
    recovery_shadow_prefetch_child = next((item for item in child_runs if item.get("label") == "recovery_shadow_kline_prefetch"), {})
    recovery_shadow_prefetch_payload = recovery_shadow_prefetch_child.get("stdout_json") or {}
    recovery_shadow_child = next((item for item in child_runs if item.get("label") == "fast_crypto_recovery_shadow_scan"), {})
    recovery_shadow_payload = recovery_shadow_child.get("stdout_json") or {}
    recovery_shadow_scan_summary = recovery_shadow_payload.get("scan_summary") or {}
    recovery_shadow_candidates = recovery_shadow_payload.get("candidate_attribution_summary") or []
    output_payload = {
        "run_id": run["run_id"],
        "status": run["status"],
        "live_orders_enabled": False,
        "private_api_used": False,
        "private_api_keys_used": False,
        "allow_real_orders": False,
        "dry_run": run["dry_run"],
        "offline_fixture": run["offline_fixture"],
        "cycles": run["cycles"],
        "include_daily": run["include_daily"],
        "include_dynamic_kline_cache": run["include_dynamic_kline_cache"],
        "include_walkforward_refresh": run["include_walkforward_refresh"],
        "include_current_signal_probe": run["include_current_signal_probe"],
        "include_near_miss_sampler": run["include_near_miss_sampler"],
        "include_strategy_recovery_optimizer": run["include_strategy_recovery_optimizer"],
        "aggressive_paper_compat": run.get("aggressive_paper_compat"),
        "dynamic_scan_pool_enabled": run.get("dynamic_scan_pool_enabled"),
        "dynamic_scan_universe": run.get("dynamic_scan_universe"),
        "effective_symbols": run.get("effective_symbols"),
        "auto_current_signal_refresh_enabled": run["auto_current_signal_refresh_enabled"],
        "auto_current_signal_refresh_required": run["auto_current_signal_refresh_required"],
        "auto_strategy_recovery_optimizer_enabled": run["auto_strategy_recovery_optimizer_enabled"],
        "auto_quality_scout_sampler_enabled": run.get("auto_quality_scout_sampler_enabled"),
        "pause_resume_guard": run.get("pause_resume_guard"),
        "auto_recovery_shadow_scan_enabled": run["auto_recovery_shadow_scan_enabled"],
        "paper_auto_learning_sync_enabled": run.get("paper_auto_learning_sync_enabled"),
        "include_recovery_shadow_scan": run["include_recovery_shadow_scan"],
        "recovery_shadow_prefetch_enabled": run["recovery_shadow_prefetch_enabled"],
        "recovery_shadow_prefetch": {
            "status": recovery_shadow_prefetch_child.get("status"),
            "stdout_status": recovery_shadow_prefetch_payload.get("status"),
            "persisted_json": recovery_shadow_prefetch_child.get("persisted_json"),
            "cache_dir": recovery_shadow_prefetch_payload.get("cache_dir") or recovery_shadow_prefetch_payload.get("recovery_shadow_prefetch", {}).get("cache_dir"),
            "selected_symbols": recovery_shadow_prefetch_payload.get("selected_symbols"),
            "requested_intervals": recovery_shadow_prefetch_payload.get("requested_intervals"),
            "file_count": recovery_shadow_prefetch_payload.get("file_count"),
            "failure_count": recovery_shadow_prefetch_payload.get("failure_count"),
        },
        "current_signal_refresh_state": run["current_signal_refresh_state"],
        "progress_delta": run["progress_delta"],
        "no_entry_summary": run.get("no_entry_summary"),
        "validation_recovery_plan": (post_validation.get("stdout_json") or {}).get("validation_recovery_plan"),
        "strategy_recovery_optimizer": {
            "status": strategy_recovery_payload.get("status"),
            "auto_strategy_recovery_optimizer": strategy_recovery_child.get("auto_strategy_recovery_optimizer", False),
            "eligible_retest_count": len(strategy_recovery_queue),
            "blocked_candidate_count": len(strategy_recovery_payload.get("blocked_candidates") or []),
            "top_retest_queue": [
                {
                    "symbol": item.get("symbol"),
                    "interval": item.get("interval"),
                    "strategy_family": item.get("strategy_family"),
                    "paper_entry_mode": item.get("paper_entry_mode"),
                    "stage": item.get("stage"),
                    "current_signal": item.get("current_signal"),
                    "score": item.get("score"),
                }
                for item in strategy_recovery_queue[:5]
            ],
            "persisted_json": strategy_recovery_child.get("persisted_json"),
        },
        "paper_auto_learning_sync": {
            "enabled": run.get("paper_auto_learning_sync_enabled"),
            "backlog_status": strategy_backlog_child.get("status"),
            "backlog_active_proposed_changes": strategy_backlog_payload.get("active_proposed_changes"),
            "backlog_pending_human_review": strategy_backlog_payload.get("pending_human_review"),
            "backlog_report": strategy_backlog_payload.get("report"),
            "evolver_status": auto_evolver_payload.get("status") or auto_evolver_child.get("status"),
            "evolver_run_id": auto_evolver_payload.get("run_id"),
            "strategy_version": auto_evolver_payload.get("strategy_version"),
            "applied_or_ab_testing": auto_evolver_payload.get("applied_or_ab_testing"),
            "reverted": auto_evolver_payload.get("reverted"),
            "overlay": auto_evolver_payload.get("overlay"),
            "report": auto_evolver_payload.get("report"),
        },
        "recovery_shadow_scan": {
            "status": recovery_shadow_child.get("status"),
            "stdout_status": recovery_shadow_payload.get("status"),
            "auto_recovery_shadow_scan": recovery_shadow_child.get("auto_recovery_shadow_scan", False),
            "dry_run": recovery_shadow_payload.get("dry_run"),
            "data_status": recovery_shadow_payload.get("data_status"),
            "frames_loaded": recovery_shadow_scan_summary.get("frames_loaded"),
            "strategies_scanned": recovery_shadow_scan_summary.get("strategies_scanned"),
            "current_signal_strategies": recovery_shadow_scan_summary.get("current_signal_strategies"),
            "deduplicated_candidate_count": recovery_shadow_scan_summary.get("deduplicated_candidate_count"),
            "new_paper_trades": recovery_shadow_payload.get("new_paper_trades"),
            "hypothetical_paper_trades": recovery_shadow_payload.get("new_paper_trades"),
            "operator_note": (
                "Recovery shadow scan runs in dry-run mode. These IDs are hypothetical execution artifacts "
                "from the child scanner and are not open paper positions unless they appear in the ledger."
            ),
            "top_candidates": recovery_shadow_candidates[:5],
        },
        "open_position_exit_summary": post_paper.get("open_position_exit_summary"),
        "next_open_position_reviews": (post_paper.get("open_exit_calendar") or [])[:5],
        "child_runs": [
            {
                "label": item.get("label"),
                "status": item.get("status"),
                "stdout_status": (item.get("stdout_json") or {}).get("status"),
                "auto_current_signal_refresh": item.get("auto_current_signal_refresh", False),
                "auto_current_signal_kline_prefetch": item.get("auto_current_signal_kline_prefetch", False),
                "auto_strategy_recovery_optimizer": item.get("auto_strategy_recovery_optimizer", False),
                "auto_quality_scout_sampler": item.get("auto_quality_scout_sampler", False),
                "auto_recovery_shadow_scan": item.get("auto_recovery_shadow_scan", False),
                "auto_recovery_shadow_prefetch": item.get("auto_recovery_shadow_prefetch", False),
                "persisted_json": item.get("persisted_json"),
                "persist_skipped_reason": item.get("persist_skipped_reason"),
            }
            for item in child_runs
        ],
        "child_failures": [
            {"label": item["label"], "returncode": item["returncode"], "parse_error": item.get("stdout_parse_error")}
            for item in child_failures
        ],
        "safety_errors": safety_errors,
        "outputs": run["outputs"],
    }
    run["paper_auto_learning_sync"] = output_payload["paper_auto_learning_sync"]
    run["strategy_recovery_optimizer_summary"] = output_payload["strategy_recovery_optimizer"]
    run["recovery_shadow_scan_summary"] = output_payload["recovery_shadow_scan"]
    if not args.dry_run:
        write_json(experiment_path, run)
        write_text(report_path, render_report(run))
    if args.format == "markdown":
        print(render_report(run))
    else:
        print(json.dumps(output_payload if args.compact_output else run, ensure_ascii=False, indent=2))
    return 2 if safety_errors else (1 if child_failures else 0)


if __name__ == "__main__":
    raise SystemExit(main())
