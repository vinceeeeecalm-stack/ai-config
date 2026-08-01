#!/usr/bin/env python3
"""
Sunday crypto realistic paper loop for active-alpha-paper-monitor.

Research-only. No private keys. No live orders. The loop reviews open paper
positions, refreshes public market data, scans current long-only spot signals,
records strategy evolution, and may open simulated positions with spread,
slippage, depth and commission assumptions.
"""

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ROOT.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(WORKSPACE_ROOT / "unified-longterm-alpha-investor" / "scripts" / "crypto"))

from weekly_goal_strategy_lab import (  # noqa: E402
    Strategy,
    backtest,
    classify,
    load_cached_frames,
    param_grid,
    selection_score,
    signal_array,
)
from research_panel_bridge import active_research_panel_overlay  # noqa: E402
import paper_strategy_overlay as pso  # noqa: E402
import paper_testnet_risk_control_auditor as ptrc  # noqa: E402
from kline_cache_storage import MANIFEST_NAME, build_kline_cache_manifest  # noqa: E402

try:
    from binance_walkforward_backtest import fetch_klines  # noqa: E402
except Exception:  # pragma: no cover - fallback is only for relocated installs.
    fetch_klines = None

try:
    from binance_kline_cache_builder import fetch_klines as fetch_klines_by_target_bars  # noqa: E402
    from binance_kline_cache_builder import write_cache as write_kline_cache  # noqa: E402
except Exception:  # pragma: no cover - fallback is only for relocated installs.
    fetch_klines_by_target_bars = None
    write_kline_cache = None


UA = "Mozilla/5.0 (compatible; active-alpha-paper-monitor/sunday-paper; research-only)"
TZ = ZoneInfo("Asia/Shanghai")
LOCK_PATH = Path("/private/tmp/active_alpha_sunday_crypto_realistic_paper.lock")
DEFAULT_CONFIG = ROOT / "config" / "active_alpha_monitor_config.json"
DEFAULT_LEDGER = ROOT / "paper_trades" / "paper_portfolio_ledger.json"
DURABLE_KLINE_CACHE_ROOT = ROOT / "cache" / "binance_klines"
BINANCE_SPOT_PUBLIC_HOSTS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
DEFAULT_SYMBOLS = (
    "BTCUSDT,ETHUSDT,SOLUSDT,ADAUSDT,TRXUSDT,RENDERUSDT,NEARUSDT,LINKUSDT,"
    "SUIUSDT,INJUSDT,SEIUSDT,WLDUSDT,FETUSDT,AVAXUSDT,AAVEUSDT,BNBUSDT,"
    "XRPUSDT,DOGEUSDT,PEPEUSDT,NIGHTUSDT"
)
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
INTERVAL_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "1d": 1440,
}
DEFAULT_MAX_KLINE_LAG_MINUTES = {
    "15m": 90,
    "30m": 150,
    "1h": 240,
    "2h": 360,
    "4h": 720,
    "1d": 2880,
}
COINGECKO_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "ADAUSDT": "cardano",
    "TRXUSDT": "tron",
    "RENDERUSDT": "render-token",
    "NEARUSDT": "near",
    "LINKUSDT": "chainlink",
    "SUIUSDT": "sui",
    "INJUSDT": "injective-protocol",
    "SEIUSDT": "sei-network",
    "WLDUSDT": "worldcoin-wld",
    "FETUSDT": "artificial-superintelligence-alliance",
    "AVAXUSDT": "avalanche-2",
    "AAVEUSDT": "aave",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",
    "DOGEUSDT": "dogecoin",
    "PEPEUSDT": "pepe",
}
ASSET_TO_SYMBOL = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "ADA": "ADAUSDT",
    "TRX": "TRXUSDT",
    "RENDER": "RENDERUSDT",
    "NEAR": "NEARUSDT",
    "LINK": "LINKUSDT",
    "SUI": "SUIUSDT",
    "INJ": "INJUSDT",
    "SEI": "SEIUSDT",
    "WLD": "WLDUSDT",
    "FET": "FETUSDT",
    "ASI": "FETUSDT",
    "AVAX": "AVAXUSDT",
    "AAVE": "AAVEUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "PEPE": "PEPEUSDT",
    "NIGHT": "NIGHTUSDT",
}

CAPACITY_SKIP_ACTIONS = {"pause_new_samples", "hold_new_samples_temporarily"}
CAPACITY_SKIP_MODES = {"exit_only_until_slots_free", "exit_first_then_reassess", "exit_monitor_only"}


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def local_dt(dt=None):
    return (dt or utc_now()).astimezone(TZ)


def parse_now(text):
    if not text:
        return utc_now()
    value = text
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(timezone.utc)


def read_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text())


def load_json_text(text):
    try:
        payload = json.loads((text or "").strip())
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


def write_json(path, data, dry_run=False):
    if dry_run:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def write_text(path, text, dry_run=False):
    if dry_run:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def validation_capacity_gate(enabled=True):
    if not enabled:
        return {"enabled": False, "decision": "disabled"}
    cmd = [sys.executable, str(SCRIPT_DIR / "validation_sample_auditor.py"), "--format", "json"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "enabled": True,
            "decision": "skip_new_samples",
            "status": "audit_timeout",
            "reason": "validation capacity audit timed out; conservative gate blocks new paper sampling",
            "stdout_tail": (exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        }
    payload = load_json_text(result.stdout)
    if result.returncode != 0 or not payload:
        return {
            "enabled": True,
            "decision": "skip_new_samples",
            "status": "audit_failed",
            "reason": "validation capacity audit failed; conservative gate blocks new paper sampling",
            "returncode": result.returncode,
            "stderr_tail": result.stderr[-1000:] if result.stderr else "",
            "stdout_tail": result.stdout[-1000:] if result.stdout else "",
        }
    capacity = payload.get("validation_capacity_state") or {}
    paper = payload.get("paper_sample_metrics") or {}
    portfolio = payload.get("current_portfolio_metrics") or {}
    plan = payload.get("validation_sample_plan") or {}
    recovery_plan = payload.get("validation_recovery_plan") or {}
    sample_action = str(capacity.get("sample_action") or "")
    runner_mode = str(capacity.get("recommended_runner_mode") or "")
    should_skip = sample_action in CAPACITY_SKIP_ACTIONS or runner_mode in CAPACITY_SKIP_MODES
    return {
        "enabled": True,
        "decision": "skip_new_samples" if should_skip else "allow_new_samples",
        "status": payload.get("status"),
        "sample_action": sample_action,
        "recommended_runner_mode": runner_mode,
        "reason": capacity.get("reason"),
        "next_runner_hint": capacity.get("next_runner_hint"),
        "open_count": capacity.get("open_count"),
        "open_slots_remaining": capacity.get("open_slots_remaining"),
        "effective_max_open_positions": capacity.get("effective_max_open_positions"),
        "paper_win_rate_pct": paper.get("win_rate_pct"),
        "realized_pnl_usd": paper.get("realized_pnl_usd"),
        "closed_net_return_pct": paper.get("realized_net_return_on_closed_notional_pct"),
        "equity_usd": portfolio.get("equity_usd"),
        "failed_gates": plan.get("failed_gates") or [],
        "validation_recovery_plan": recovery_plan,
        "validation_recovery_plan_summary": summarize_recovery_plan(recovery_plan),
        "operator_note": "This gate controls paper sampling only. It never authorizes or executes live trades.",
    }


def summarize_recovery_plan(plan):
    if not isinstance(plan, dict) or not plan:
        return {"status": "missing"}
    monthly = plan.get("monthly_target") or {}
    return {
        "status": plan.get("status"),
        "new_sample_policy": plan.get("new_sample_policy"),
        "target_gap_usd": monthly.get("target_gap_usd"),
        "required_return_pct_from_current_equity": monthly.get("required_return_pct_from_current_equity"),
        "retired_entry_modes": [item.get("name") for item in (plan.get("retired_entry_modes") or [])],
        "cooldown_entry_modes": [item.get("name") for item in (plan.get("cooldown_entry_modes") or [])],
        "eligible_entry_modes": [item.get("name") for item in (plan.get("eligible_entry_modes") or [])],
        "retired_strategy_families": [item.get("name") for item in (plan.get("retired_strategy_families") or [])],
        "cooldown_strategy_families": [item.get("name") for item in (plan.get("cooldown_strategy_families") or [])],
        "eligible_strategy_families": [item.get("name") for item in (plan.get("eligible_strategy_families") or [])],
    }


def skipped_capacity_scan(capacity_gate):
    return {
        "frames_loaded": 0,
        "strategies_scanned": 0,
        "current_signal_strategies": 0,
        "candidate_count": 0,
        "deduplicated_candidate_count": 0,
        "dedup_summary": {"performance_duplicate_count": 0},
        "stage_counts": {},
        "deduplicated_stage_counts": {},
        "top_candidates": [],
        "scan_budget": {
            "enabled": False,
            "decision": "skipped_capacity_gate",
            "reason": capacity_gate.get("reason"),
        },
        "skip_reason": "validation_capacity_gate",
        "validation_capacity_gate": capacity_gate,
    }


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def binance_spot_get(path, params=None, timeout=25):
    query = urllib.parse.urlencode(params or {})
    failures = []
    for host in BINANCE_SPOT_PUBLIC_HOSTS:
        url = f"{host}{path}" + (f"?{query}" if query else "")
        try:
            return get_json(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "host": host,
                    "path": path,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:160],
                }
            )
    raise RuntimeError(
        "Binance public spot fetch failed on all hosts: "
        + "; ".join(f"{item['host']}:{item['error_type']}" for item in failures)
    )


def safe_fetch(name, fn, attempts=1, pause_seconds=0.5):
    last_error = None
    attempts = max(1, int(attempts or 1))
    for attempt in range(1, attempts + 1):
        try:
            item = {"name": name, "status": "ok", "data": fn(), "error": None}
            if attempt > 1:
                item["attempts"] = attempt
            return item
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(pause_seconds)
    return {"name": name, "status": "error", "data": None, "error": str(last_error), "attempts": attempts}


def is_transient_fetch_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return 500 <= int(getattr(exc, "code", 0) or 0) < 600
    text = str(exc).lower()
    transient_markers = (
        "remote end closed connection",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
    )
    return any(marker in text for marker in transient_markers)


def budget_exhausted(started, budget_seconds, min_request_start_budget_seconds):
    if not budget_seconds or budget_seconds <= 0:
        return False
    elapsed = time.monotonic() - started
    return elapsed >= budget_seconds or budget_seconds - elapsed < min_request_start_budget_seconds


def budget_error(name, budget_seconds, started, total_items, processed_items, skipped_items, min_request_start_budget_seconds):
    return {
        "name": name,
        "status": "budget_exhausted",
        "error": f"{name} stopped after wall-clock budget; remaining items are treated as missing/degraded",
        "budget_seconds": budget_seconds,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "min_request_start_budget_seconds": min_request_start_budget_seconds,
        "total_items": total_items,
        "processed_items": processed_items,
        "skipped_items": skipped_items,
    }


def binance_get(path, params, timeout=25):
    return binance_spot_get(path, params, timeout=timeout)


def binance_futures_get(path, params, timeout=25):
    query = urllib.parse.urlencode(params)
    return get_json(f"https://fapi.binance.com{path}?{query}", timeout=timeout)


def fetch_24hr(symbol, timeout=25):
    return binance_get("/api/v3/ticker/24hr", {"symbol": symbol}, timeout=timeout)


def fetch_book_ticker(symbol, timeout=25):
    return binance_get("/api/v3/ticker/bookTicker", {"symbol": symbol}, timeout=timeout)


def fetch_depth(symbol, limit=100, timeout=25):
    return binance_get("/api/v3/depth", {"symbol": symbol, "limit": limit}, timeout=timeout)


KLINE_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
}


def fetch_spot_klines_range(symbol, interval, start_at, end_at, timeout=8, max_pages=100):
    """Fetch public spot klines for barrier replay without account/private APIs."""

    interval_ms = KLINE_INTERVAL_MS.get(str(interval))
    if not interval_ms:
        raise ValueError(f"unsupported barrier replay interval: {interval}")
    if end_at <= start_at:
        return []
    start_ms = max(0, int(start_at.timestamp() * 1000) - interval_ms)
    end_ms = int(end_at.timestamp() * 1000)
    cursor = start_ms
    rows = []
    for _ in range(max(1, int(max_pages or 1))):
        batch = binance_spot_get(
            "/api/v3/klines",
            {
                "symbol": str(symbol).upper(),
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=timeout,
        )
        if not isinstance(batch, list) or not batch:
            break
        for raw in batch:
            if not isinstance(raw, list) or len(raw) < 7:
                continue
            rows.append(
                {
                    "open_time_ms": int(raw[0]),
                    "open": float(raw[1]),
                    "high": float(raw[2]),
                    "low": float(raw[3]),
                    "close": float(raw[4]),
                    "close_time_ms": int(raw[6]),
                    "quote_volume": float(raw[7]) if len(raw) > 7 else None,
                    "trade_count": int(raw[8]) if len(raw) > 8 else None,
                }
            )
        next_cursor = int(batch[-1][6]) + 1
        if next_cursor <= cursor or next_cursor > end_ms or len(batch) < 1000:
            break
        cursor = next_cursor
    rows.sort(key=lambda item: item["open_time_ms"])
    return rows


def detect_historical_barrier_replay(pos, rows, checked_from, checked_until):
    """Find the first stop/take touch from public OHLC bars.

    When both barriers appear inside the same bar, a long paper position uses
    the conservative stop-first assumption because intrabar ordering is not
    recoverable from OHLC alone.
    """

    if checked_until <= checked_from:
        return None
    start_ms = int(checked_from.timestamp() * 1000)
    end_ms = int(checked_until.timestamp() * 1000)
    stop_price = float(pos["stop_price"])
    take_price = float(pos["take_profit_price"])
    relevant = [
        row
        for row in rows
        if int(row.get("close_time_ms", 0)) >= start_ms
        and int(row.get("open_time_ms", 0)) <= end_ms
    ]
    if not relevant:
        return None
    period_high = None
    period_low = None
    for row in relevant:
        period_high = max(period_high, float(row["high"])) if period_high is not None else float(row["high"])
        period_low = min(period_low, float(row["low"])) if period_low is not None else float(row["low"])
        stop_hit = float(row["low"]) <= stop_price
        take_hit = float(row["high"]) >= take_price
        if not stop_hit and not take_hit:
            continue
        ambiguous = stop_hit and take_hit
        exit_reason = "stop" if stop_hit else "take_profit"
        if exit_reason == "stop":
            trigger_mark_price = min(stop_price, float(row["open"]))
        else:
            trigger_mark_price = take_price
        triggered_at = datetime.fromtimestamp(int(row["open_time_ms"]) / 1000, tz=timezone.utc)
        return {
            "status": "barrier_triggered",
            "exit_reason": exit_reason,
            "triggered_at": iso(max(triggered_at, checked_from)),
            "trigger_mark_price": round(trigger_mark_price, 12),
            "bar": dict(row),
            "period_high_until_trigger": period_high,
            "period_low_until_trigger": period_low,
            "ambiguous_same_bar": ambiguous,
            "intrabar_policy": "conservative_stop_first" if ambiguous else "single_barrier_touch",
        }
    return None


def historical_expiry_replay(pos, rows, expiry_at):
    """Create an expiry mark without using market data from after expiry."""

    expiry_ms = int(expiry_at.timestamp() * 1000)
    eligible = [row for row in rows if int(row.get("open_time_ms", 0)) <= expiry_ms]
    if not eligible:
        return None
    row = max(eligible, key=lambda item: int(item["open_time_ms"]))
    if int(row.get("close_time_ms", 0)) <= expiry_ms:
        mark_price = float(row["close"])
        mark_source = "last_closed_1m_close_before_expiry"
    else:
        mark_price = float(row["open"])
        mark_source = "containing_1m_open_no_post_expiry_lookahead"
    return {
        "status": "expiry_reconciled",
        "exit_reason": "time_expired",
        "triggered_at": iso(expiry_at),
        "trigger_mark_price": round(mark_price, 12),
        "bar": dict(row),
        "period_high_until_trigger": None,
        "period_low_until_trigger": None,
        "ambiguous_same_bar": False,
        "intrabar_policy": mark_source,
    }


def historical_replay_execution_quote(pos, replay, settings):
    """Apply conservative configured friction when historical book depth is unavailable."""

    mark_price = float(replay["trigger_mark_price"])
    quantity = float(pos["quantity"])
    commission_bps = float(settings.get("commission_bps", 10.0))
    base_slippage_bps = float(settings.get("base_slippage_bps", 8.0))
    extra_slippage_bps = float(settings.get("historical_barrier_extra_slippage_bps", 12.0))
    total_slippage_bps = base_slippage_bps + extra_slippage_bps
    execution_price = mark_price * (1.0 - total_slippage_bps / 10000.0)
    gross_quote = execution_price * quantity
    commission_usd = gross_quote * commission_bps / 10000.0
    return {
        "symbol": pos["symbol"],
        "side": "sell",
        "status": "historical_public_kline_estimate",
        "allow": True,
        "reasons": [
            "barrier_or_expiry_verified_from_binance_public_spot_kline",
            "historical_order_book_unavailable_extra_slippage_applied",
        ],
        "bid": None,
        "ask": None,
        "mid": round(mark_price, 12),
        "spread_bps": None,
        "depth_1pct_usd": None,
        "quote_volume_24h_usd": None,
        "execution_price": round(execution_price, 12),
        "commission_bps": commission_bps,
        "commission_usd": round(commission_usd, 6),
        "base_slippage_bps": base_slippage_bps,
        "historical_extra_slippage_bps": extra_slippage_bps,
        "dynamic_impact_bps": None,
        "total_slippage_bps": round(total_slippage_bps, 4),
        "gross_quote_usd": round(gross_quote, 6),
        "net_quote_after_commission_usd": round(gross_quote - commission_usd, 6),
        "base_quantity": None,
        "data_quality_status": "verified_barrier_degraded_execution_estimate",
    }


def build_historical_barrier_replays(ledger, settings, now, offline_fixture=False):
    """Reconcile stop/take/expiry events that may have happened between polls."""

    if offline_fixture or not settings.get("historical_barrier_replay_enabled", True):
        return {}, []
    interval = str(settings.get("historical_barrier_interval", "5m"))
    timeout = float(settings.get("historical_barrier_request_timeout_seconds", 8.0))
    max_pages = int(settings.get("historical_barrier_max_pages", 100))
    replays = {}
    errors = []
    for pos in ledger.get("open_positions", []):
        trade_id = pos.get("paper_trade_id")
        symbol = pos.get("symbol")
        try:
            opened_at = parse_iso(pos["opened_at"])
            checked_from = parse_iso(pos.get("barrier_replay_checked_until") or pos["opened_at"])
            checked_from = max(opened_at, checked_from)
            expiry_at = parse_iso(pos["expires_at"])
            checked_until = min(now, expiry_at)
            rows = fetch_spot_klines_range(
                symbol,
                interval,
                checked_from,
                checked_until,
                timeout=timeout,
                max_pages=max_pages,
            )
            replay = detect_historical_barrier_replay(pos, rows, checked_from, checked_until)
            if replay:
                coarse_bar = replay.get("bar") or {}
                coarse_start = datetime.fromtimestamp(int(coarse_bar["open_time_ms"]) / 1000, tz=timezone.utc)
                coarse_end = datetime.fromtimestamp(int(coarse_bar["close_time_ms"]) / 1000, tz=timezone.utc)
                minute_rows = fetch_spot_klines_range(
                    symbol,
                    "1m",
                    max(opened_at, coarse_start),
                    min(checked_until, coarse_end),
                    timeout=timeout,
                    max_pages=2,
                )
                refined = detect_historical_barrier_replay(
                    pos,
                    minute_rows,
                    max(opened_at, coarse_start),
                    min(checked_until, coarse_end),
                )
                if refined:
                    replay = refined
                    replay["detection_interval"] = "1m"
                else:
                    replay["detection_interval"] = interval
                replay["source"] = "binance_public_spot_klines"
                replay["checked_from"] = iso(checked_from)
                replay["checked_until"] = iso(checked_until)
                replay["execution_quote"] = historical_replay_execution_quote(pos, replay, settings)
                replays[trade_id] = replay
                continue
            if now >= expiry_at:
                minute_rows = fetch_spot_klines_range(
                    symbol,
                    "1m",
                    expiry_at - timedelta(minutes=2),
                    expiry_at + timedelta(minutes=1),
                    timeout=timeout,
                    max_pages=2,
                )
                replay = historical_expiry_replay(pos, minute_rows, expiry_at)
                if not replay:
                    raise RuntimeError("expiry_replay_missing_1m_kline")
                replay["source"] = "binance_public_spot_klines"
                replay["detection_interval"] = "1m"
                replay["checked_from"] = iso(checked_from)
                replay["checked_until"] = iso(expiry_at)
                replay["execution_quote"] = historical_replay_execution_quote(pos, replay, settings)
                replays[trade_id] = replay
                continue
            completed = [
                row for row in rows if int(row.get("close_time_ms", 0)) <= int(now.timestamp() * 1000)
            ]
            if completed:
                pos["barrier_replay_checked_until"] = iso(
                    datetime.fromtimestamp(max(row["close_time_ms"] for row in completed) / 1000, tz=timezone.utc)
                )
                pos["barrier_replay_status"] = "checked_no_trigger"
        except Exception as exc:  # noqa: BLE001
            pos["barrier_replay_status"] = "failed"
            errors.append(
                {
                    "name": f"historical_barrier_replay_{symbol}",
                    "status": "error",
                    "paper_trade_id": trade_id,
                    "error": str(exc)[:300],
                }
            )
    return replays, errors


def fetch_coingecko_prices(symbols, timeout=20):
    ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
    if not ids:
        return {}
    params = urllib.parse.urlencode({"ids": ",".join(ids), "vs_currencies": "usd"})
    data = get_json(f"https://api.coingecko.com/api/v3/simple/price?{params}", timeout=timeout)
    id_to_symbol = {v: k for k, v in COINGECKO_IDS.items()}
    out = {}
    for cg_id, item in data.items():
        symbol = id_to_symbol.get(cg_id)
        if symbol and "usd" in item:
            out[symbol] = float(item["usd"])
    return out


def fetch_coingecko_trending_symbols(timeout=20):
    data = get_json("https://api.coingecko.com/api/v3/search/trending", timeout=timeout)
    symbols = set()
    ids = set()
    for coin in data.get("coins", []):
        item = coin.get("item", {})
        if item.get("symbol"):
            symbols.add(item["symbol"].upper())
        if item.get("id"):
            ids.add(item["id"])
    return {"symbols": sorted(symbols), "ids": sorted(ids)}


def fetch_reddit_keyword_counts(symbols, timeout=20):
    keys = [s.replace("USDT", "") for s in symbols]
    query = " OR ".join(keys[:20])
    params = {
        "q": query,
        "restrict_sr": "false",
        "sort": "new",
        "t": "day",
        "limit": 25,
    }
    data = get_json("https://www.reddit.com/search.json?" + urllib.parse.urlencode(params), timeout=timeout)
    counts = {key: 0 for key in keys}
    headlines = []
    for child in data.get("data", {}).get("children", []):
        item = child.get("data", {})
        title = item.get("title", "")
        title_upper = title.upper()
        headlines.append(
            {
                "subreddit": item.get("subreddit", ""),
                "title": title[:240],
                "created_utc": item.get("created_utc"),
            }
        )
        for key in counts:
            if key.upper() in title_upper:
                counts[key] += 1
    return {"keyword_counts": counts, "headline_sample": headlines[:15]}


def fetch_futures_context(symbol, timeout=25):
    premium = binance_futures_get("/fapi/v1/premiumIndex", {"symbol": symbol}, timeout=timeout)
    oi = binance_futures_get("/fapi/v1/openInterest", {"symbol": symbol}, timeout=timeout)
    return {
        "lastFundingRate": premium.get("lastFundingRate"),
        "markPrice": premium.get("markPrice"),
        "openInterest": oi.get("openInterest"),
    }


def latest_social_intel_handoff(max_age_hours=12.0):
    handoff_dir = ROOT / "handoffs"
    paths = sorted(handoff_dir.glob("*-social-key-person-intel-handoff.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        return {
            "status": "missing",
            "freshness_status": "missing",
            "is_stale": True,
            "path": None,
            "data": None,
            "reason": "no_social_key_person_handoff_found",
        }
    try:
        payload = read_json(paths[0], {})
        created_at = payload.get("created_at")
        try:
            created = parse_iso(str(created_at))
            age_hours = (utc_now() - created).total_seconds() / 3600.0
        except Exception:  # noqa: BLE001
            created = None
            age_hours = max_age_hours + 1
        is_stale = age_hours > float(max_age_hours or 0)
        return {
            "status": "ok",
            "freshness_status": "stale" if is_stale else "fresh",
            "is_stale": is_stale,
            "path": str(paths[0].relative_to(WORKSPACE_ROOT)),
            "data": payload,
            "created_at": created_at,
            "age_hours": round(age_hours, 4),
            "max_age_hours": max_age_hours,
            "reason": "social_handoff_stale" if is_stale else "social_handoff_fresh",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "freshness_status": "error",
            "is_stale": True,
            "path": str(paths[0]),
            "error": str(exc),
            "data": None,
            "reason": "social_handoff_unreadable",
        }


def social_intel_by_symbol(symbols):
    handoff = latest_social_intel_handoff()
    if handoff["status"] != "ok":
        return {}, handoff
    if handoff.get("is_stale"):
        return {}, handoff
    allowed_symbols = set(symbols)
    now = utc_now()
    by_symbol = {}
    for item in (handoff.get("data") or {}).get("social_key_person_intel", []):
        action = item.get("recommended_max_action") or "watch"
        if action == "watch":
            continue
        captured = item.get("captured_at") or item.get("posted_at")
        captured_dt = parse_iso(captured) if captured else now
        if (now - captured_dt).total_seconds() > 72 * 3600:
            continue
        event_type = item.get("event_type") or "general_commentary"
        if action == "risk_alert":
            base = 6
        elif action == "conditional_action":
            base = 12
        elif action == "paper_only":
            base = 10
        else:
            base = 4
        if event_type in {"roadmap_upgrade", "ecosystem_partnership", "fund_flow", "staking_policy"}:
            base += 4
        elif event_type in {"security_risk", "tokenomics_unlock", "listing_delisting", "regulatory_policy"}:
            base = min(base, 8)
        score = min(base, 18)
        for asset in item.get("asset_tags") or []:
            symbol = ASSET_TO_SYMBOL.get(str(asset).upper())
            if not symbol or symbol not in allowed_symbols:
                continue
            row = by_symbol.setdefault(symbol, {"score": 0, "count": 0, "top_items": []})
            row["score"] = min(24, row["score"] + score)
            row["count"] += 1
            if len(row["top_items"]) < 3:
                row["top_items"].append(
                    {
                        "action": action,
                        "event_type": event_type,
                        "score": item.get("social_intel_score_points"),
                        "summary": item.get("summary"),
                    }
                )
    return by_symbol, handoff


def split_symbol_string(value):
    symbols = []
    seen = set()
    for raw in (value or "").split(","):
        symbol = raw.strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


def append_symbol_unique(target, symbol, seen, limit):
    symbol = str(symbol or "").upper()
    if not symbol or symbol in seen:
        return
    if len(target) >= int(limit or 0):
        return
    target.append(symbol)
    seen.add(symbol)


def symbol_bucket(symbol):
    return SYMBOL_BUCKETS.get(str(symbol or "").upper(), "market_mover")


def dynamic_symbol_bucket(symbol, change_pct, quote_volume, trade_count):
    static_bucket = symbol_bucket(symbol)
    if static_bucket != "market_mover":
        return static_bucket
    if quote_volume >= 5_000_000 and trade_count >= 35_000 and change_pct >= 8.0:
        return "high_beta_dynamic"
    if quote_volume >= 20_000_000 and abs(change_pct) >= 5.0:
        return "large_cap_or_infra"
    return static_bucket


def dynamic_pool_family(bucket):
    if bucket == "meme_liquid":
        return "meme_liquid"
    if bucket in CORE_DEFENSIVE_BUCKETS:
        return "core_defensive"
    if bucket in HIGH_BETA_BUCKETS or str(bucket or "").startswith("high_beta_"):
        return "high_beta"
    if bucket in {"large_cap_l1", "large_cap_payment", "infrastructure", "defi_bluechip"}:
        return "large_cap_or_infra"
    return "market_mover"


def dynamic_pool_shape_policy(mood, max_symbols):
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


def dynamic_pool_effective_limit(mood, configured_limit, protected_count=0):
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


def explain_pool_change_from_static_baseline(mood, pool_width_policy, social_handoff=None):
    regime = str((mood or {}).get("market_regime") or "mixed_selective")
    short_state = str((mood or {}).get("short_term_state") or "unavailable")
    sentiment = str((mood or {}).get("sentiment_state") or "neutral_or_missing_social")
    width_reason = str((pool_width_policy or {}).get("reason") or "configured_width")
    freshness = str((social_handoff or {}).get("freshness_status") or "")
    if sentiment == "neutral_or_missing_social" and freshness in {"missing", "stale"}:
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


def select_symbols_by_dynamic_pool_policy(ranked, protected_symbols, base_symbols, explicit_symbols, max_symbols, mood):
    policy = dynamic_pool_shape_policy(mood, max_symbols)
    selected = []
    seen = set()
    counts = {"core_defensive": 0, "high_beta": 0, "meme_liquid": 0, "large_cap_or_infra": 0, "market_mover": 0, "social_catalyst": 0}
    item_by_symbol = {item.get("symbol"): item for item in ranked}

    def item_family(item):
        return dynamic_pool_family((item or {}).get("bucket"))

    def is_social_catalyst(item):
        return (
            (safe_float((item or {}).get("social_long_score"), 0.0) or 0.0) > 0
            and (safe_float((item or {}).get("social_long_score"), 0.0) or 0.0)
            >= (safe_float((item or {}).get("social_risk_score"), 0.0) or 0.0)
        )

    def add_symbol(symbol):
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


def is_dynamic_pool_eligible_usdt_symbol(symbol):
    symbol = str(symbol or "").upper()
    if not symbol.endswith("USDT"):
        return False
    if any(marker in symbol for marker in LEVERAGED_TOKEN_MARKERS):
        return False
    base = symbol.removesuffix("USDT")
    if not base or base in STABLE_OR_FIAT_BASES:
        return False
    return True


def fetch_all_24h_tickers():
    return binance_spot_get("/api/v3/ticker/24hr")


def fetch_dynamic_anchor_change(symbol, interval, limit=5):
    try:
        rows = binance_get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    except Exception as exc:
        return {"symbol": symbol, "interval": interval, "status": "error", "error": str(exc)}
    if not isinstance(rows, list) or len(rows) < 2:
        return {"symbol": symbol, "interval": interval, "status": "missing"}
    try:
        first_open = safe_float(rows[0][1])
        last_open = safe_float(rows[-1][1])
        last_close = safe_float(rows[-1][4])
    except (IndexError, TypeError):
        return {"symbol": symbol, "interval": interval, "status": "malformed"}
    if not first_open or not last_open or not last_close:
        return {"symbol": symbol, "interval": interval, "status": "malformed_price"}
    return {
        "symbol": symbol,
        "interval": interval,
        "status": "ok",
        "window_change_pct": round((last_close / first_open - 1.0) * 100.0, 6),
        "current_candle_change_pct": round((last_close / last_open - 1.0) * 100.0, 6),
        "bars": len(rows),
    }


def build_short_term_anchor_profile():
    rows = []
    for symbol in MARKET_MOOD_ANCHOR_SYMBOLS:
        for interval, limit in (("1h", 5), ("4h", 4)):
            rows.append(fetch_dynamic_anchor_change(symbol, interval, limit=limit))
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    one_hour = [safe_float(row.get("window_change_pct"), 0.0) or 0.0 for row in ok_rows if row.get("interval") == "1h"]
    four_hour = [safe_float(row.get("window_change_pct"), 0.0) or 0.0 for row in ok_rows if row.get("interval") == "4h"]
    avg_1h = sum(one_hour) / len(one_hour) if one_hour else 0.0
    avg_4h = sum(four_hour) / len(four_hour) if four_hour else 0.0
    positive_1h_pct = sum(1 for value in one_hour if value > 0) / len(one_hour) * 100.0 if one_hour else 0.0
    positive_4h_pct = sum(1 for value in four_hour if value > 0) / len(four_hour) * 100.0 if four_hour else 0.0
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


def social_sentiment_scores_for_pool(allowed_symbols, max_age_hours=12.0):
    handoff = latest_social_intel_handoff(max_age_hours=max_age_hours)
    if handoff["status"] != "ok":
        return {}, handoff
    if handoff.get("is_stale"):
        return {}, handoff
    allowed = set(allowed_symbols or [])
    now = utc_now()
    by_symbol = {}
    for item in (handoff.get("data") or {}).get("social_key_person_intel", []):
        captured = item.get("captured_at") or item.get("posted_at")
        captured_dt = parse_iso(captured) if captured else now
        if (now - captured_dt).total_seconds() > 72 * 3600:
            continue
        action = item.get("recommended_max_action") or "watch"
        event_type = item.get("event_type") or "general_commentary"
        long_score = 0
        risk_score = 0
        if action == "risk_alert":
            risk_score = 8
        elif action == "conditional_action":
            long_score = 12
        elif action == "paper_only":
            long_score = 10
        elif action == "watch":
            long_score = 2
        if event_type in {"roadmap_upgrade", "ecosystem_partnership", "fund_flow", "staking_policy"}:
            long_score += 4
        elif event_type in {"security_risk", "tokenomics_unlock", "listing_delisting", "regulatory_policy"}:
            risk_score += 5
            long_score = min(long_score, 3)
        for asset in item.get("asset_tags") or []:
            symbol = ASSET_TO_SYMBOL.get(str(asset).upper())
            if not symbol or (allowed and symbol not in allowed):
                continue
            row = by_symbol.setdefault(symbol, {"long_score": 0, "risk_score": 0, "count": 0, "notes": []})
            row["long_score"] = min(30, row["long_score"] + long_score)
            row["risk_score"] = min(30, row["risk_score"] + risk_score)
            row["count"] += 1
            if len(row["notes"]) < 3:
                row["notes"].append(
                    {
                        "action": action,
                        "event_type": event_type,
                        "summary": item.get("summary"),
                    }
                )
    return by_symbol, handoff


def dynamic_market_mood_profile(liquid_rows, by_symbol, social_scores, short_term_profile=None):
    changes = [safe_float(row.get("priceChangePercent"), 0.0) or 0.0 for row in liquid_rows]
    positive_count = sum(1 for value in changes if value > 0)
    breadth = positive_count / len(changes) * 100.0 if changes else 0.0
    strong_gainers_pct = sum(1 for value in changes if value >= 5.0) / len(changes) * 100.0 if changes else 0.0
    hard_sellers_pct = sum(1 for value in changes if value <= -5.0) / len(changes) * 100.0 if changes else 0.0
    top20 = sorted(changes, reverse=True)[:20]
    top20_avg = sum(top20) / len(top20) if top20 else 0.0
    average_change = sum(changes) / len(changes) if changes else 0.0
    btc_change = safe_float((by_symbol.get("BTCUSDT") or {}).get("priceChangePercent"), 0.0) or 0.0
    eth_change = safe_float((by_symbol.get("ETHUSDT") or {}).get("priceChangePercent"), 0.0) or 0.0
    social_long_total = sum(safe_float(item.get("long_score"), 0.0) or 0.0 for item in social_scores.values())
    social_risk_total = sum(safe_float(item.get("risk_score"), 0.0) or 0.0 for item in social_scores.values())
    short_state = str((short_term_profile or {}).get("state") or "unavailable")
    short_avg_1h = safe_float((short_term_profile or {}).get("avg_1h_anchor_change_pct"), 0.0) or 0.0
    short_avg_4h = safe_float((short_term_profile or {}).get("avg_4h_anchor_change_pct"), 0.0) or 0.0

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


def build_dynamic_scan_pool(base_symbols, explicit_symbols, ledger, settings, strategy_recovery_state, offline_fixture=False):
    queue_symbols = strategy_recovery_queue_symbols(
        strategy_recovery_state or {},
        max_symbols=int(settings.get("strategy_recovery_queue_max_symbols", 8) or 8),
    )
    open_symbols = [p.get("symbol") for p in ledger.get("open_positions", []) if p.get("symbol")]
    base_symbols = [str(symbol).upper() for symbol in base_symbols if symbol]
    explicit_symbols = [str(symbol).upper() for symbol in explicit_symbols if symbol]
    if offline_fixture or not settings.get("dynamic_scan_pool_enabled", True):
        selected = []
        seen = set()
        for symbol in [*open_symbols, *explicit_symbols, *queue_symbols, *base_symbols]:
            append_symbol_unique(selected, symbol, seen, max(len(base_symbols), len(open_symbols) + len(explicit_symbols) + len(queue_symbols), 1))
        return {
            "enabled": bool(settings.get("dynamic_scan_pool_enabled", True)),
            "status": "skipped_offline_fixture" if offline_fixture else "disabled",
            "selected_symbols": selected,
            "open_symbols": open_symbols,
            "explicit_symbols": explicit_symbols,
            "queued_symbols": queue_symbols,
            "selection_policy": "fallback_preserve_open_explicit_queue_and_config_symbols",
        }

    tickers = safe_fetch("binance_all_24h_dynamic_scan_pool", fetch_all_24h_tickers, attempts=2, pause_seconds=0.75)
    if tickers.get("status") != "ok" or not isinstance(tickers.get("data"), list):
        selected = []
        seen = set()
        fallback_limit = max(int(settings.get("dynamic_scan_pool_max_symbols", 24) or 24), len(open_symbols) + len(explicit_symbols) + len(queue_symbols), 1)
        for symbol in [*open_symbols, *explicit_symbols, *queue_symbols, *CORE_LIQUIDITY_SYMBOLS, *base_symbols]:
            append_symbol_unique(selected, symbol, seen, fallback_limit)
        return {
            "enabled": True,
            "status": "fallback_static",
            "reason": "binance_all_24h_unavailable",
            "why_pool_changed_from_static_baseline": (
                "Binance market breadth unavailable; using open/explicit/recovery/core/static survival fallback only"
            ),
            "ticker_status": tickers.get("status"),
            "ticker_error": tickers.get("error"),
            "selected_symbols": selected,
            "open_symbols": open_symbols,
            "explicit_symbols": explicit_symbols,
            "queued_symbols": queue_symbols,
            "selection_policy": "preserve_open_explicit_queue_core_then_config_fallback",
        }

    rows = [
        row for row in tickers["data"]
        if isinstance(row, dict) and is_dynamic_pool_eligible_usdt_symbol(row.get("symbol"))
    ]
    min_quote_volume = float(settings.get("dynamic_scan_pool_min_quote_volume_usd", settings.get("min_quote_volume_usd", 5_000_000)) or 5_000_000)
    liquid_rows = [row for row in rows if (safe_float(row.get("quoteVolume"), 0.0) or 0.0) >= min_quote_volume]
    by_symbol = {str(row.get("symbol")): row for row in rows if row.get("symbol")}
    social_scores, social_handoff = social_sentiment_scores_for_pool(
        {*(row.get("symbol") for row in liquid_rows), *base_symbols, *open_symbols, *queue_symbols},
        max_age_hours=float(settings.get("dynamic_scan_pool_social_max_age_hours", 12.0) or 12.0),
    )
    short_term_profile = build_short_term_anchor_profile()
    mood = dynamic_market_mood_profile(liquid_rows, by_symbol, social_scores, short_term_profile=short_term_profile)
    regime = mood.get("market_regime")
    weights = mood.get("scoring_weights") or {}
    configured_max_symbols = max(
        int(settings.get("dynamic_scan_pool_max_symbols", 24) or 24),
        len(open_symbols) + len(explicit_symbols) + len(queue_symbols) + 2,
    )
    effective_max_symbols, pool_width_policy = dynamic_pool_effective_limit(
        mood,
        configured_max_symbols,
        protected_count=len(open_symbols) + len(explicit_symbols) + len(queue_symbols),
    )
    ranked = []
    for row in liquid_rows:
        symbol = str(row.get("symbol") or "").upper()
        change = safe_float(row.get("priceChangePercent"), 0.0) or 0.0
        quote_volume = safe_float(row.get("quoteVolume"), 0.0) or 0.0
        trade_count = safe_float(row.get("count"), 0.0) or 0.0
        bucket = dynamic_symbol_bucket(symbol, change, quote_volume, trade_count)
        volume_score = min(24.0, max(0.0, math.log10(max(quote_volume, 1.0)) - 6.0) * 8.0)
        participation_score = min(8.0, max(0.0, math.log10(max(trade_count, 1.0)) - 4.0) * 3.0)
        move_score = max(0.0, min(change, 14.0)) * float(weights.get("momentum_multiplier", 1.6) or 1.6)
        move_score += max(0.0, min(abs(change), 18.0) - 4.0) * float(weights.get("absolute_move_multiplier", 0.9) or 0.9)
        if regime == "risk_off_rebound_watch" and -8.0 <= change <= 2.5:
            move_score += 5.0
        social = social_scores.get(symbol) or {}
        social_long_score = safe_float(social.get("long_score"), 0.0) or 0.0
        social_risk_score = safe_float(social.get("risk_score"), 0.0) or 0.0
        social_score = social_long_score * float(weights.get("long_social_multiplier", 1.0) or 1.0)
        social_penalty = social_risk_score * float(weights.get("risk_social_penalty", 1.0) or 1.0)
        score = (
            volume_score
            + participation_score
            + move_score
            + social_score
            + (1.5 if symbol in base_symbols else 0.0)
            + (float(weights.get("core_defensive_bonus", 0.0) or 0.0) if bucket in CORE_DEFENSIVE_BUCKETS else 0.0)
            + (float(weights.get("high_beta_bonus", 0.0) or 0.0) if bucket in HIGH_BETA_BUCKETS else 0.0)
            + (12.0 if symbol in queue_symbols else 0.0)
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
                "social_long_score": round(social_long_score, 6),
                "social_risk_score": round(social_risk_score, 6),
                "social_penalty": round(social_penalty, 6),
                "social_notes": (social.get("notes") or [])[:3],
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    protected = [*open_symbols, *explicit_symbols, *queue_symbols]
    selected, pool_shape_policy = select_symbols_by_dynamic_pool_policy(
        ranked,
        protected,
        base_symbols,
        explicit_symbols,
        effective_max_symbols,
        mood,
    )
    return {
        "enabled": True,
        "status": "ok",
        "selection_policy": (
            "open/explicit/recovery symbols preserved first; remaining slots are selected from live Binance USDT spot "
            "market breadth, liquidity, 24h movement, participation, asset bucket bias, latest social sentiment and "
            "market-mood bucket quotas"
        ),
        "pool_shape_policy": pool_shape_policy,
        "market_mood_profile": mood,
        "market_regime": mood.get("market_regime"),
        "market_atmosphere": mood.get("market_atmosphere"),
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
        "social_handoff_status": social_handoff.get("status"),
        "social_handoff_freshness": social_handoff.get("freshness_status"),
        "social_handoff_age_hours": social_handoff.get("age_hours"),
        "social_handoff_max_age_hours": social_handoff.get("max_age_hours"),
        "social_handoff_is_stale": social_handoff.get("is_stale"),
        "social_handoff_reason": social_handoff.get("reason"),
        "social_handoff_path": social_handoff.get("path"),
        "liquid_usdt_symbol_count": len(liquid_rows),
        "base_symbol_count": len(base_symbols),
        "configured_max_symbols": configured_max_symbols,
        "max_symbols": effective_max_symbols,
        "pool_width_policy": pool_width_policy,
        "why_pool_changed_from_static_baseline": explain_pool_change_from_static_baseline(
            mood,
            pool_width_policy,
            social_handoff,
        ),
        "selected_symbol_count": len(selected),
        "open_symbols": open_symbols,
        "explicit_symbols": explicit_symbols,
        "queued_symbols": queue_symbols,
        "selected_symbols": selected,
        "top_dynamic_candidates": ranked[:12],
    }


def build_info_signals(symbols, market, settings):
    started = time.monotonic()
    budget_seconds = float(settings.get("info_fetch_wall_clock_seconds", 0) or 0)
    min_start = float(settings.get("info_fetch_min_request_start_seconds", 5) or 5)
    timeout = float(settings.get("info_request_timeout_seconds", 8) or 8)
    processed_sources = 0
    skipped_sources = 0
    budget_items = 2 + len(symbols)
    if budget_exhausted(started, budget_seconds, min_start):
        trending = {"name": "coingecko_trending", "status": "skipped_budget", "data": None, "error": "info_fetch_budget_exhausted"}
        skipped_sources += 1
    else:
        trending = safe_fetch("coingecko_trending", lambda: fetch_coingecko_trending_symbols(timeout=timeout))
        processed_sources += 1
    if budget_exhausted(started, budget_seconds, min_start):
        reddit = {"name": "reddit_24h_keyword_counts", "status": "skipped_budget", "data": None, "error": "info_fetch_budget_exhausted"}
        skipped_sources += 1
    else:
        reddit = safe_fetch("reddit_24h_keyword_counts", lambda: fetch_reddit_keyword_counts(symbols, timeout=timeout))
        processed_sources += 1
    social_by_symbol, social_handoff = social_intel_by_symbol(symbols)
    futures = {}
    errors = []
    optional_warnings = []
    symbol_errors = []
    if trending["status"] != "ok":
        optional_warnings.append(trending)
    if reddit["status"] != "ok":
        optional_warnings.append(reddit)
    for symbol in symbols:
        if budget_exhausted(started, budget_seconds, min_start):
            item = {
                "name": f"binance_futures_context_{symbol}",
                "status": "skipped_budget",
                "data": None,
                "error": "info_fetch_budget_exhausted",
            }
            skipped_sources += 1
        else:
            item = safe_fetch(f"binance_futures_context_{symbol}", lambda s=symbol: fetch_futures_context(s, timeout=timeout))
            processed_sources += 1
        if item["status"] == "ok":
            futures[symbol] = item["data"]
        else:
            symbol_errors.append(item)
        time.sleep(float(settings.get("request_pause_seconds", 0.03)))

    trending_symbols = set((trending.get("data") or {}).get("symbols", []))
    trending_ids = set((trending.get("data") or {}).get("ids", []))
    reddit_counts = (reddit.get("data") or {}).get("keyword_counts", {})
    rows = []
    for symbol in symbols:
        stats = market.get(symbol, {}).get("stats", {})
        key = symbol.replace("USDT", "")
        cg_id = COINGECKO_IDS.get(symbol)
        try:
            pct_24h = float(stats.get("priceChangePercent", 0.0))
        except Exception:
            pct_24h = 0.0
        try:
            quote_volume = float(stats.get("quoteVolume", 0.0))
        except Exception:
            quote_volume = 0.0
        try:
            funding = float((futures.get(symbol) or {}).get("lastFundingRate") or 0.0)
        except Exception:
            funding = 0.0
        reddit_hits = int(reddit_counts.get(key, 0))
        is_trending = key.upper() in trending_symbols or (cg_id in trending_ids if cg_id else False)

        score = 0
        reasons = []
        abs_move = abs(pct_24h)
        if abs_move >= 8:
            score += 22
            reasons.append("very_high_24h_move")
        elif abs_move >= 4:
            score += 14
            reasons.append("high_24h_move")
        elif abs_move >= 2:
            score += 7
            reasons.append("moderate_24h_move")
        if pct_24h >= 3:
            score += 8
            reasons.append("positive_momentum")
        if quote_volume >= 250_000_000:
            score += 20
            reasons.append("large_quote_volume")
        elif quote_volume >= 50_000_000:
            score += 14
            reasons.append("medium_quote_volume")
        elif quote_volume >= 5_000_000:
            score += 7
            reasons.append("tradable_quote_volume")
        if funding <= -0.00005:
            score += 8
            reasons.append("negative_funding_squeeze_potential")
        elif funding >= 0.0003:
            score -= 8
            reasons.append("crowded_positive_funding")
        if reddit_hits:
            score += min(reddit_hits * 6, 18)
            reasons.append("reddit_24h_mentions")
        if is_trending:
            score += 15
            reasons.append("coingecko_trending")
        social = social_by_symbol.get(symbol, {})
        social_score = int(social.get("score", 0) or 0)
        if social_score:
            score += social_score
            reasons.append("social_key_person_intel")
        rows.append(
            {
                "symbol": symbol,
                "info_pressure_score": score,
                "tier": "hot" if score >= 45 else "active" if score >= 30 else "quiet",
                "price_change_24h_pct": round(pct_24h, 4),
                "quote_volume_24h_usd": round(quote_volume, 2),
                "funding_rate": funding,
                "reddit_hits_24h": reddit_hits,
                "coingecko_trending": is_trending,
                "social_intel_score": social_score,
                "social_intel_count": int(social.get("count", 0) or 0),
                "social_intel_top_items": social.get("top_items", []),
                "reasons": reasons,
            }
        )
    rows.sort(key=lambda x: x["info_pressure_score"], reverse=True)
    return {
        "generated_at": iso(utc_now()),
        "sources": {
            "coingecko_trending": trending["status"],
            "reddit_24h_keyword_counts": reddit["status"],
            "binance_futures_context": "ok_with_symbol_level_errors" if symbol_errors else "ok",
            "social_key_person_intel": social_handoff["status"],
            "social_key_person_intel_path": social_handoff.get("path"),
        },
        "errors": errors,
        "optional_warnings": optional_warnings,
        "symbol_errors": symbol_errors[:10],
        "budget_status": (
            budget_error(
                "info_fetch_wall_clock_budget",
                budget_seconds,
                started,
                budget_items,
                processed_sources,
                skipped_sources,
                min_start,
            )
            if skipped_sources
            else {
                "name": "info_fetch_wall_clock_budget",
                "status": "not_exhausted",
                "budget_seconds": budget_seconds,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "processed_items": processed_sources,
                "skipped_items": skipped_sources,
            }
        ),
        "rows": rows,
        "by_symbol": {row["symbol"]: row for row in rows},
        "headline_sample": (reddit.get("data") or {}).get("headline_sample", []),
    }


def load_config(path):
    cfg = read_json(path, {})
    cfg.setdefault("paper_portfolio", {})
    cfg.setdefault("paper_exit_management", {})
    cfg.setdefault("sunday_crypto_realistic_paper_loop", {})
    cfg.setdefault("daily_crypto_paper_auto_trader", {})
    return cfg


def load_ledger(path, initial_capital):
    now = iso(utc_now())
    ledger = read_json(
        path,
        {
            "ledger_version": "paper-portfolio-v1",
            "created_at": now,
            "updated_at": now,
            "live_orders_enabled": False,
            "initial_capital_usd": float(initial_capital),
            "cash_usd": float(initial_capital),
            "open_positions": [],
            "closed_trades": [],
            "paper_orders": [],
            "events": [],
        },
    )
    current_month = month_id_for(parse_now(now))
    baselines = normalize_monthly_goal_baselines(ledger)
    baseline_preexisting = current_month in baselines
    ensure_monthly_goal_baseline(ledger, now=parse_now(now), reason="load_ledger")
    ledger["_runtime_monthly_baseline_created_on_load"] = not baseline_preexisting
    return ledger


def save_ledger(path, ledger, dry_run=False):
    ensure_monthly_goal_baseline(ledger, reason="save_ledger")
    ledger.pop("_runtime_monthly_baseline_created_on_load", None)
    ledger["updated_at"] = iso(utc_now())
    ledger["live_orders_enabled"] = False
    write_json(path, ledger, dry_run=dry_run)


def max_drawdown_from_events(ledger):
    initial = float(ledger.get("initial_capital_usd", 500.0))
    values = [initial]
    for event in ledger.get("events", []):
        if event.get("event_type") == "paper_equity_snapshot":
            values.append(float(event.get("equity_usd", values[-1])))
    if "equity_usd" in ledger:
        values.append(float(ledger["equity_usd"]))
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, (value / peak - 1.0) * 100.0)
    return round(max_dd, 4)


def depth_usd(levels, mid, side):
    total = 0.0
    if side == "buy":
        cutoff = mid * 1.01
        for price_s, qty_s in levels:
            price = float(price_s)
            if price > cutoff:
                break
            total += price * float(qty_s)
    else:
        cutoff = mid * 0.99
        for price_s, qty_s in levels:
            price = float(price_s)
            if price < cutoff:
                break
            total += price * float(qty_s)
    return total


def execution_quote(symbol, side, notional_usd, market, settings):
    book = market.get(symbol, {}).get("book", {})
    depth = market.get(symbol, {}).get("depth", {})
    stats = market.get(symbol, {}).get("stats", {})
    if not book or not depth:
        return {
            "symbol": symbol,
            "side": side,
            "status": "missing_book",
            "allow": False,
            "reasons": ["order book missing"],
        }
    bid = float(book["bidPrice"])
    ask = float(book["askPrice"])
    mid = (bid + ask) / 2.0
    spread_bps = (ask / bid - 1.0) * 10000.0 if bid else 999999.0
    levels = depth.get("asks" if side == "buy" else "bids", [])
    depth_1pct = depth_usd(levels, mid, side)
    quote_volume = float(stats.get("quoteVolume", 0.0))
    commission_bps = float(settings["commission_bps"])
    base_slippage_bps = float(settings["base_slippage_bps"])
    depth_floor = max(float(settings["min_depth_1pct_usd"]), notional_usd * float(settings["min_depth_to_notional"]))
    impact_bps = min(float(settings["max_dynamic_impact_bps"]), notional_usd / max(depth_1pct, 1.0) * 100.0)
    total_slippage_bps = base_slippage_bps + impact_bps
    if side == "buy":
        execution_price = ask * (1.0 + total_slippage_bps / 10000.0)
        gross_quote = notional_usd
        commission_usd = gross_quote * commission_bps / 10000.0
        base_quantity = max(gross_quote - commission_usd, 0.0) / execution_price
        net_quote_after_commission = gross_quote - commission_usd
    else:
        execution_price = bid * (1.0 - total_slippage_bps / 10000.0)
        base_quantity = None
        gross_quote = notional_usd
        commission_usd = gross_quote * commission_bps / 10000.0
        net_quote_after_commission = gross_quote - commission_usd

    reasons = []
    if quote_volume < float(settings["min_quote_volume_usd"]):
        reasons.append("low_24h_quote_volume")
    if spread_bps > float(settings["max_spread_bps"]):
        reasons.append("wide_spread")
    if depth_1pct < depth_floor:
        reasons.append("thin_1pct_depth")
    return {
        "symbol": symbol,
        "side": side,
        "status": "verified" if not reasons else "blocked",
        "allow": not reasons,
        "reasons": reasons,
        "bid": round(bid, 12),
        "ask": round(ask, 12),
        "mid": round(mid, 12),
        "spread_bps": round(spread_bps, 4),
        "depth_1pct_usd": round(depth_1pct, 2),
        "quote_volume_24h_usd": round(quote_volume, 2),
        "execution_price": round(execution_price, 12),
        "commission_bps": commission_bps,
        "commission_usd": round(commission_usd, 6),
        "base_slippage_bps": base_slippage_bps,
        "dynamic_impact_bps": round(impact_bps, 4),
        "total_slippage_bps": round(total_slippage_bps, 4),
        "gross_quote_usd": round(gross_quote, 6),
        "net_quote_after_commission_usd": round(net_quote_after_commission, 6),
        "base_quantity": round(base_quantity, 12) if base_quantity is not None else None,
    }


def cg_validation(symbol, binance_mid, coingecko_prices, max_deviation_bps):
    cg_price = coingecko_prices.get(symbol)
    if not cg_price:
        return {"status": "missing", "coingecko_price": None, "deviation_bps": None}
    deviation_bps = abs(binance_mid / cg_price - 1.0) * 10000.0
    return {
        "status": "verified" if deviation_bps <= max_deviation_bps else "disputed",
        "coingecko_price": round(cg_price, 12),
        "deviation_bps": round(deviation_bps, 4),
    }


def fetch_market(symbols, settings, offline_fixture=False):
    if offline_fixture:
        market = {}
        for symbol in symbols:
            base = 2.0 if symbol == "RENDERUSDT" else 0.35 if symbol == "TRXUSDT" else 100.0
            market[symbol] = {
                "stats": {"quoteVolume": str(10_000_000), "priceChangePercent": "3.5"},
                "book": {"bidPrice": str(base * 0.999), "askPrice": str(base * 1.001)},
                "depth": {
                    "bids": [[str(base * (1.0 - i * 0.0001)), "1000"] for i in range(1, 50)],
                    "asks": [[str(base * (1.0 + i * 0.0001)), "1000"] for i in range(1, 50)],
                },
            }
        return market, {}, []

    market = {}
    errors = []
    started = time.monotonic()
    budget_seconds = float(settings.get("market_fetch_wall_clock_seconds", 0) or 0)
    min_start = float(settings.get("market_fetch_min_request_start_seconds", 5) or 5)
    timeout = float(settings.get("market_request_timeout_seconds", 8) or 8)
    attempts = int(settings.get("market_fetch_attempts", 1) or 1)
    processed_symbols = 0
    skipped_symbols = 0
    for symbol in symbols:
        if budget_exhausted(started, budget_seconds, min_start):
            market[symbol] = {"stats": {}, "book": {}, "depth": {}}
            skipped_symbols += 1
            continue
        stats = safe_fetch(f"binance_24hr_{symbol}", lambda s=symbol: fetch_24hr(s, timeout=timeout), attempts=attempts)
        book = safe_fetch(f"binance_book_{symbol}", lambda s=symbol: fetch_book_ticker(s, timeout=timeout), attempts=attempts)
        depth = safe_fetch(f"binance_depth_{symbol}", lambda s=symbol: fetch_depth(s, timeout=timeout), attempts=attempts)
        processed_symbols += 1
        if stats["status"] == "ok" and book["status"] == "ok" and depth["status"] == "ok":
            market[symbol] = {"stats": stats["data"], "book": book["data"], "depth": depth["data"]}
        else:
            market[symbol] = {"stats": {}, "book": {}, "depth": {}}
            errors.extend([item for item in [stats, book, depth] if item["status"] != "ok"])
        time.sleep(float(settings.get("request_pause_seconds", 0.03)))
    if budget_exhausted(started, budget_seconds, min_start):
        cg = {"name": "coingecko_simple_price", "status": "skipped_budget", "data": None, "error": "market_fetch_budget_exhausted"}
    else:
        cg = safe_fetch("coingecko_simple_price", lambda: fetch_coingecko_prices(symbols, timeout=timeout), attempts=1)
    if cg["status"] != "ok":
        errors.append(cg)
        cg_prices = {}
    else:
        cg_prices = cg["data"]
    if skipped_symbols or cg.get("status") == "skipped_budget":
        cg_skipped = 1 if cg.get("status") == "skipped_budget" else 0
        errors.append(
            budget_error(
                "market_fetch_wall_clock_budget",
                budget_seconds,
                started,
                len(symbols) + 1,
                processed_symbols + (0 if cg_skipped else 1),
                skipped_symbols + cg_skipped,
                min_start,
            )
        )
    return market, cg_prices, errors


def refresh_kline_cache_manifest(cache_dir, symbols, intervals):
    cache_path = Path(cache_dir)
    manifest_path = cache_path / MANIFEST_NAME
    existing = {}
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = {}
    selected_symbols = sorted(
        {
            *[str(item).upper() for item in (existing.get("selected_symbols") or []) if str(item).strip()],
            *[str(item).upper() for item in symbols if str(item).strip()],
        }
    )
    required_intervals = []
    for item in [*(existing.get("required_intervals") or []), *intervals]:
        value = str(item).strip()
        if value and value not in required_intervals:
            required_intervals.append(value)
    return build_kline_cache_manifest(
        cache_path,
        required_intervals=required_intervals,
        selected_symbols=selected_symbols,
    )


def fetch_or_reuse_klines(symbols, intervals, cache_dir, days_by_interval, offline_fixture=False, wall_clock_seconds=0):
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    if offline_fixture:
        write_fixture_klines(symbols, intervals, cache_dir)
        try:
            refresh_kline_cache_manifest(cache_dir, symbols, intervals)
            return cache_dir, []
        except Exception as exc:  # noqa: BLE001
            return cache_dir, [{"name": "kline_cache_manifest", "status": "error", "error": str(exc)}]
    if fetch_klines is None and (fetch_klines_by_target_bars is None or write_kline_cache is None):
        return cache_dir, [{"name": "binance_klines", "status": "error", "error": "fetch_klines unavailable"}]

    now = utc_now()
    errors = []
    started = time.monotonic()
    budget_seconds = float(os.environ.get("ACTIVE_ALPHA_KLINE_FETCH_WALL_CLOCK_SECONDS") or wall_clock_seconds or 0)
    total_pairs = len(symbols) * len(intervals)
    fetched_pairs = 0
    skipped_pairs = 0
    budget_exhausted = False
    min_request_start_budget_seconds = 8.0
    for symbol in symbols:
        for interval in intervals:
            elapsed = time.monotonic() - started
            if budget_seconds > 0 and (elapsed >= budget_seconds or budget_seconds - elapsed < min_request_start_budget_seconds):
                budget_exhausted = True
                skipped_pairs += 1
                continue
            days = int(days_by_interval.get(interval, 120))
            start = now - timedelta(days=days)
            interval_minutes = max(1, int(INTERVAL_MINUTES.get(interval, 60)))
            target_bars = max(120, min(3000, int(days * 24 * 60 / interval_minutes)))
            attempts = 3
            last_exc = None
            for attempt in range(1, attempts + 1):
                try:
                    if fetch_klines is not None:
                        fetch_klines(
                            symbol,
                            interval,
                            int(start.timestamp() * 1000),
                            int(now.timestamp() * 1000),
                            pause=0.04,
                            cache_dir=cache_dir,
                        )
                    else:
                        rows = fetch_klines_by_target_bars(symbol, interval, target_bars, sleep_sec=0.04)
                        write_kline_cache(Path(cache_dir), symbol, interval, rows)
                    last_exc = None
                    fetched_pairs += 1
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt >= attempts or not is_transient_fetch_error(exc):
                        break
                    time.sleep(0.6 * attempt)
            if last_exc is not None:
                errors.append(
                    {
                        "name": f"klines_{symbol}_{interval}",
                        "status": "error",
                        "error": str(last_exc),
                        "attempts": attempt,
                        "transient_retryable": is_transient_fetch_error(last_exc),
                    }
                )
    if budget_exhausted:
        errors.append(
            {
                "name": "kline_fetch_wall_clock_budget",
                "status": "budget_exhausted",
                "error": "kline prefetch stopped after wall-clock budget; remaining pairs are treated as missing/degraded",
                "budget_seconds": budget_seconds,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "min_request_start_budget_seconds": min_request_start_budget_seconds,
                "total_pairs": total_pairs,
                "fetched_pairs": fetched_pairs,
                "skipped_pairs": skipped_pairs,
            }
        )
    try:
        refresh_kline_cache_manifest(cache_dir, symbols, intervals)
    except Exception as exc:  # noqa: BLE001
        errors.append(
            {
                "name": "kline_cache_manifest",
                "status": "error",
                "error": str(exc),
                "action": "cache_nonreplayable_until_manifest_refresh_succeeds",
            }
        )
    return cache_dir, errors


def kline_close_time_ms(row, interval):
    interval_ms = int(INTERVAL_MINUTES.get(interval, 60) * 60_000)
    if isinstance(row, dict):
        if row.get("ct") is not None:
            return int(row["ct"])
        if row.get("t") is not None:
            return int(row["t"]) + interval_ms - 1
    if isinstance(row, (list, tuple)):
        if len(row) > 6:
            return int(row[6])
        if row:
            return int(row[0]) + interval_ms - 1
    return None


def audit_kline_cache(cache_dir, symbols, intervals, now, max_lag_by_interval=None, sample_limit=20):
    path = Path(cache_dir)
    max_lag_by_interval = max_lag_by_interval or DEFAULT_MAX_KLINE_LAG_MINUTES
    now_ms = int(now.timestamp() * 1000)
    rows = []
    latest_global = None
    checked = 0
    stale = 0
    missing = 0
    for symbol in symbols:
        for interval in intervals:
            checked += 1
            latest_close_ms = None
            file_count = 0
            newest_file = None
            for file_path in path.glob(f"{symbol}_{interval}_*.json"):
                if not file_path.is_file():
                    continue
                file_count += 1
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(data, list) or not data:
                    continue
                close_ms = kline_close_time_ms(data[-1], interval)
                if close_ms is None:
                    continue
                if latest_close_ms is None or close_ms > latest_close_ms:
                    latest_close_ms = close_ms
                    newest_file = file_path.name
            if latest_close_ms is None:
                missing += 1
                rows.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "status": "missing",
                        "file_count": file_count,
                        "latest_close_at": None,
                        "lag_minutes": None,
                        "max_allowed_lag_minutes": max_lag_by_interval.get(interval),
                        "newest_file": newest_file,
                    }
                )
                continue
            is_open_bar = latest_close_ms > now_ms
            effective_latest_ms = min(latest_close_ms, now_ms)
            latest_global = effective_latest_ms if latest_global is None else max(latest_global, effective_latest_ms)
            lag_minutes = max(0.0, (now_ms - effective_latest_ms) / 60_000.0)
            max_allowed = float(max_lag_by_interval.get(interval, INTERVAL_MINUTES.get(interval, 60) * 4))
            status = "verified" if lag_minutes <= max_allowed else "stale"
            if status == "stale":
                stale += 1
            rows.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "status": status,
                    "file_count": file_count,
                    "latest_close_at": datetime.fromtimestamp(effective_latest_ms / 1000, tz=timezone.utc).isoformat(),
                    "raw_close_at": datetime.fromtimestamp(latest_close_ms / 1000, tz=timezone.utc).isoformat(),
                    "is_open_bar": is_open_bar,
                    "lag_minutes": round(lag_minutes, 4),
                    "max_allowed_lag_minutes": max_allowed,
                    "newest_file": newest_file,
                }
            )
    if missing:
        status = "missing"
    elif stale:
        status = "stale"
    else:
        status = "verified"
    max_lag = max((row.get("lag_minutes") or 0.0 for row in rows), default=None)
    return {
        "enabled": True,
        "status": status,
        "cache_dir": str(path),
        "checked_pairs": checked,
        "verified_count": sum(1 for row in rows if row["status"] == "verified"),
        "stale_count": stale,
        "missing_count": missing,
        "open_bar_count": sum(1 for row in rows if row.get("is_open_bar")),
        "max_lag_minutes": round(max_lag, 4) if max_lag is not None else None,
        "latest_close_at": datetime.fromtimestamp(latest_global / 1000, tz=timezone.utc).isoformat() if latest_global else None,
        "rows": rows[:sample_limit],
        "problem_rows": [row for row in rows if row["status"] != "verified"][:sample_limit],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def filter_scan_symbols_by_kline_audit(scan_symbols, audit):
    """Remove stale/missing K-line symbols before current-signal scanning."""
    problem_symbols = []
    for row in audit.get("problem_rows") or []:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and row.get("status") in {"stale", "missing"} and symbol not in problem_symbols:
            problem_symbols.append(symbol)
    if not problem_symbols:
        return list(scan_symbols or []), {
            "status": "not_needed",
            "problem_symbols": [],
            "removed_symbols": [],
            "operator_note": "No stale or missing K-line symbols were found.",
        }
    problem_set = set(problem_symbols)
    filtered = [symbol for symbol in (scan_symbols or []) if str(symbol).upper() not in problem_set]
    removed = [symbol for symbol in (scan_symbols or []) if str(symbol).upper() in problem_set]
    return filtered, {
        "status": "filtered" if removed else "no_matching_scan_symbols",
        "problem_symbols": problem_symbols,
        "removed_symbols": removed,
        "effective_symbols_after_filter": filtered,
        "operator_note": "Symbols with stale/missing K-line cache were removed from new signal scanning; open-position review still uses live market data.",
    }


def write_fixture_klines(symbols, intervals, cache_dir):
    now_ms = int(utc_now().timestamp() * 1000)
    interval_ms = {"15m": 900_000, "1h": 3600_000, "4h": 14_400_000, "1d": 86_400_000}
    for symbol in symbols:
        for interval in intervals:
            rows = []
            price = 2.0 if symbol == "RENDERUSDT" else 0.35 if symbol == "TRXUSDT" else 100.0
            for i in range(720):
                t = now_ms - (720 - i) * interval_ms.get(interval, 3600_000)
                drift = 1.0 + i * 0.0008
                pulse = 1.0 + math.sin(i / 17.0) * 0.012
                close = price * drift * pulse
                open_ = close * 0.998
                high = close * 1.012
                low = close * 0.988
                qv = 2_000_000 + i * 1000
                rows.append({"t": t, "o": open_, "h": high, "l": low, "c": close, "v": 1000.0, "ct": t + interval_ms.get(interval, 3600_000) - 1, "qv": qv, "n": 100})
            path = Path(cache_dir) / f"{symbol}_{interval}_{rows[0]['t']}_{rows[-1]['t']}.json"
            path.write_text(json.dumps(rows))


def strategy_grid_for_scan(interval, max_strategies_per_frame=0):
    strategies = param_grid(interval)
    limit = int(max_strategies_per_frame or 0)
    if limit <= 0 or limit >= len(strategies):
        return strategies
    by_family = {}
    for strategy in strategies:
        by_family.setdefault(strategy.family, []).append(strategy)
    selected = []
    offsets = {family: 0 for family in by_family}
    families = list(by_family)
    while len(selected) < limit:
        progressed = False
        for family in families:
            idx = offsets[family]
            bucket = by_family[family]
            if idx < len(bucket):
                selected.append(bucket[idx])
                offsets[family] += 1
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
    return selected


def rounded_metric(value, digits=4):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


def candidate_performance_key(candidate):
    strategy = candidate.get("strategy") or {}
    oos = candidate.get("oos_summary") or {}
    return json.dumps(
        {
            "symbol": candidate.get("symbol"),
            "interval": candidate.get("interval"),
            "stage": candidate.get("stage"),
            "family": strategy.get("family"),
            "oos_trade_count": oos.get("trade_count"),
            "oos_win_rate_pct": rounded_metric(oos.get("win_rate_pct"), 2),
            "oos_final_capital": rounded_metric(oos.get("final_capital"), 2),
            "oos_net_return_pct": rounded_metric(oos.get("net_return_pct"), 2),
            "oos_max_drawdown_pct": rounded_metric(oos.get("max_drawdown_pct"), 2),
        },
        sort_keys=True,
    )


def summarize_candidate_distribution(candidates):
    stage_counts = {}
    symbol_counts = {}
    symbol_stage_counts = {}
    for candidate in candidates:
        stage = candidate.get("stage", "unknown")
        symbol = candidate.get("symbol", "unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        key = f"{symbol}:{stage}"
        symbol_stage_counts[key] = symbol_stage_counts.get(key, 0) + 1
    return {
        "stage_counts": dict(sorted(stage_counts.items(), key=lambda kv: kv[0])),
        "symbol_counts": dict(sorted(symbol_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "symbol_stage_counts": dict(sorted(symbol_stage_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def dedupe_candidate_equivalents(candidates):
    exact_seen = set()
    performance_seen = {}
    deduped = []
    exact_duplicates = 0
    performance_duplicates = 0
    for candidate in candidates:
        exact_key = candidate_key(candidate)
        if exact_key in exact_seen:
            exact_duplicates += 1
            continue
        exact_seen.add(exact_key)
        perf_key = candidate_performance_key(candidate)
        existing = performance_seen.get(perf_key)
        if existing is not None:
            performance_duplicates += 1
            existing["deduped_equivalent_strategy_count"] = existing.get("deduped_equivalent_strategy_count", 1) + 1
            examples = existing.setdefault("deduped_equivalent_strategy_examples", [])
            if len(examples) < 5:
                examples.append(candidate.get("strategy", {}))
            continue
        candidate["deduped_equivalent_strategy_count"] = 1
        performance_seen[perf_key] = candidate
        deduped.append(candidate)
    return deduped, {
        "input_count": len(candidates),
        "output_count": len(deduped),
        "exact_duplicate_count": exact_duplicates,
        "performance_duplicate_count": performance_duplicates,
        "dedup_key": "exact strategy first, then symbol/interval/stage/family plus rounded OOS performance",
    }


def resolve_scan_budget(active_frame_count, max_strategies_per_frame=0, target_max_strategies_per_run=0, min_strategies_per_frame=0):
    configured_per_frame = int(max_strategies_per_frame or 0)
    target_total = int(target_max_strategies_per_run or 0)
    min_per_frame = int(min_strategies_per_frame or 0)
    if active_frame_count <= 0:
        return {
            "enabled": bool(target_total),
            "active_frame_count": active_frame_count,
            "configured_max_strategies_per_frame": configured_per_frame,
            "target_max_strategies_per_run": target_total,
            "min_strategies_per_frame": min_per_frame,
            "effective_max_strategies_per_frame": configured_per_frame,
            "estimated_max_strategies": 0,
            "decision": "no_active_frames",
        }
    if target_total <= 0:
        return {
            "enabled": False,
            "active_frame_count": active_frame_count,
            "configured_max_strategies_per_frame": configured_per_frame,
            "target_max_strategies_per_run": target_total,
            "min_strategies_per_frame": min_per_frame,
            "effective_max_strategies_per_frame": configured_per_frame,
            "estimated_max_strategies": None if configured_per_frame <= 0 else configured_per_frame * active_frame_count,
            "decision": "static_per_frame_limit",
        }
    budget_cap = max(1, target_total // active_frame_count)
    if min_per_frame > 0:
        budget_cap = max(min_per_frame, budget_cap)
    effective = min(configured_per_frame, budget_cap) if configured_per_frame > 0 else budget_cap
    return {
        "enabled": True,
        "active_frame_count": active_frame_count,
        "configured_max_strategies_per_frame": configured_per_frame,
        "target_max_strategies_per_run": target_total,
        "min_strategies_per_frame": min_per_frame,
        "budget_cap_per_frame": budget_cap,
        "effective_max_strategies_per_frame": effective,
        "estimated_max_strategies": effective * active_frame_count,
        "decision": "adaptive_budget_applied" if configured_per_frame == 0 or effective < configured_per_frame else "static_limit_within_budget",
    }


def diversify_candidates(candidates, max_total=50, max_per_symbol=8, max_per_symbol_interval=4):
    selected = []
    seen_keys = set()
    per_symbol = {}
    per_symbol_interval = {}
    for candidate in candidates:
        key = candidate_key(candidate)
        symbol = candidate.get("symbol")
        interval = candidate.get("interval")
        symbol_interval = (symbol, interval)
        if key in seen_keys:
            continue
        if per_symbol.get(symbol, 0) >= max_per_symbol:
            continue
        if per_symbol_interval.get(symbol_interval, 0) >= max_per_symbol_interval:
            continue
        selected.append(candidate)
        seen_keys.add(key)
        per_symbol[symbol] = per_symbol.get(symbol, 0) + 1
        per_symbol_interval[symbol_interval] = per_symbol_interval.get(symbol_interval, 0) + 1
        if len(selected) >= max_total:
            break
    return selected


def strategy_segment_summary(result):
    keys = [
        "trade_count",
        "win_rate_pct",
        "weekly_double_trade_count",
        "final_capital",
        "net_return_pct",
        "max_drawdown_pct",
        "expectancy_pct",
        "profit_factor",
        "largest_winner_share_pct",
        "avg_friction_pct",
        "capital_fraction_per_trade",
    ]
    return {key: result.get(key) for key in keys}


def scan_current_signals(
    cache_dir,
    symbols,
    intervals,
    initial,
    top_train,
    max_strategies_per_frame=0,
    candidate_max_per_symbol=8,
    candidate_max_per_symbol_interval=4,
    target_max_strategies_per_run=0,
    min_strategies_per_frame=0,
):
    frames = load_cached_frames([cache_dir], symbols=set(symbols), intervals=set(intervals))
    active_frame_count = sum(1 for df in frames.values() if len(df) >= 500)
    budget = resolve_scan_budget(
        active_frame_count,
        max_strategies_per_frame=max_strategies_per_frame,
        target_max_strategies_per_run=target_max_strategies_per_run,
        min_strategies_per_frame=min_strategies_per_frame,
    )
    effective_max_strategies_per_frame = budget["effective_max_strategies_per_frame"]
    candidates = []
    scanned = 0
    current_signals = 0
    for (symbol, interval), df in frames.items():
        if len(df) < 500:
            continue
        train_end = int(len(df) * 0.50)
        validation_end = int(len(df) * 0.75)
        btc_df = frames.get(("BTCUSDT", interval))
        preselected = []
        for s in strategy_grid_for_scan(interval, max_strategies_per_frame=effective_max_strategies_per_frame):
            if s.family == "relative_strength" and btc_df is None:
                continue
            scanned += 1
            sig = signal_array(df, s, btc_df=btc_df)
            if not bool(sig[len(df) - 1]):
                continue
            current_signals += 1
            train = backtest(df, s, 0, train_end, btc_df=btc_df, initial=initial)
            validation = backtest(df, s, train_end, validation_end, btc_df=btc_df, initial=initial)
            preselected.append(
                {
                    "strategy": s,
                    "train": train,
                    "validation": validation,
                    "selection_score": selection_score(train, validation),
                }
            )
        preselected.sort(key=lambda item: item["selection_score"], reverse=True)
        strategy_tests = []
        for selected in preselected[: max(1, int(top_train or 1))]:
            s = selected["strategy"]
            train = selected["train"]
            validation = selected["validation"]
            test = backtest(df, s, validation_end, len(df), btc_df=btc_df, initial=initial)
            stage = classify(train, test, True, initial=initial, validation=validation)
            strategy_tests.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "strategy": s.__dict__,
                    "stage": stage,
                    "validation_protocol": "train_50_validation_25_final_holdout_25",
                    "holdout_used_for_selection": False,
                    "selection_score": round(float(selected["selection_score"]), 4),
                    "train_summary": strategy_segment_summary(train),
                    "validation_summary": strategy_segment_summary(validation),
                    "oos_summary": strategy_segment_summary(test),
                    "sample_trades": test["trades"][-3:],
                }
            )
        strategy_tests.sort(
            key=lambda x: (
                x["stage"] == "target_research_pass_current_signal",
                x["stage"] == "paper_forward_candidate_current_signal",
                x.get("selection_score", -999999.0),
            ),
            reverse=True,
        )
        candidates.extend(strategy_tests[:top_train])
    candidates.sort(
        key=lambda x: (
            x["stage"] == "target_research_pass_current_signal",
            x["stage"] == "paper_forward_candidate_current_signal",
            x.get("selection_score", -999999.0),
        ),
        reverse=True,
    )
    candidate_summary = summarize_candidate_distribution(candidates)
    deduped_candidates, dedup_summary = dedupe_candidate_equivalents(candidates)
    deduped_summary = summarize_candidate_distribution(deduped_candidates)
    top_candidates = diversify_candidates(
        deduped_candidates,
        max_total=50,
        max_per_symbol=candidate_max_per_symbol,
        max_per_symbol_interval=candidate_max_per_symbol_interval,
    )
    return {
        "frames_loaded": len(frames),
        "strategies_scanned": scanned,
        "current_signal_strategies": current_signals,
        "holdout_candidates_evaluated": len(candidates),
        "validation_protocol": "rank_current_signals_on_train_50_validation_25_then_gate_on_final_25_holdout",
        "holdout_used_for_selection": False,
        "execution_assumptions": {
            "capital_fraction_per_trade": 0.25,
            "commission_bps_per_side": 10,
            "slippage_bps_per_side": 8,
            "round_trip_spread_bps": 10,
            "entry_timing": "next_bar_open",
            "same_bar_stop_take_policy": "conservative_stop_first",
        },
        "candidate_count": len(candidates),
        "deduplicated_candidate_count": len(deduped_candidates),
        "candidate_summary": candidate_summary,
        "deduplicated_candidate_summary": deduped_summary,
        "stage_counts": candidate_summary["stage_counts"],
        "dedup_summary": dedup_summary,
        "scan_budget": budget,
        "top_candidates": top_candidates,
    }


def candidate_key(candidate):
    raw = json.dumps(
        {
            "symbol": candidate["symbol"],
            "interval": candidate["interval"],
            "strategy": candidate["strategy"],
        },
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def candidate_notional(candidate, settings):
    if candidate and candidate.get("planned_notional_usd") is not None:
        return float(candidate["planned_notional_usd"])
    if candidate and candidate.get("paper_entry_mode") == "info_exploratory_probe":
        return float(settings["exploratory_notional_usd"])
    if candidate and candidate.get("paper_entry_mode") == "validation_probe":
        return float(settings.get("validation_probe_notional_usd", settings.get("min_trade_notional_usd", 25.0)))
    return float(settings["default_notional_usd"])


def open_exposure_usd(ledger):
    return sum(float(pos.get("notional_usd", 0.0) or 0.0) for pos in ledger.get("open_positions", []))


def trade_capacity_usd(ledger, settings):
    cash = float(ledger.get("cash_usd", 0.0) or 0.0)
    equity = float(ledger.get("equity_usd") or (cash + float(ledger.get("open_value_usd", 0.0) or 0.0)) or cash)
    max_single = equity * float(settings.get("max_single_position_pct_of_equity", 30.0)) / 100.0
    max_total_open = equity * float(settings.get("max_total_open_exposure_pct_of_equity", 75.0)) / 100.0
    available_exposure = max(0.0, max_total_open - open_exposure_usd(ledger))
    available_cash = max(0.0, cash - float(settings.get("min_cash_reserve_usd", 50.0)))
    return max(0.0, min(available_cash, available_exposure, max_single, float(settings.get("max_single_trade_notional_usd", 150.0))))


def target_pressure_context(ledger):
    target_state = monthly_double_progress(ledger)
    target = float(target_state.get("target_equity_usd") or 0.0)
    equity = float(target_state.get("current_equity_usd") or 0.0)
    gap = max(0.0, target - equity)
    return {
        "target_model": target_state.get("target_model"),
        "month_id": target_state.get("month_id"),
        "baseline_source": target_state.get("baseline_source"),
        "initial_capital_usd": target_state.get("initial_capital_usd"),
        "month_start_equity_usd": target_state.get("month_start_equity_usd"),
        "target_equity_usd": target_state.get("target_equity_usd"),
        "current_equity_usd": target_state.get("current_equity_usd"),
        "gap_to_target_usd": round(gap, 6),
        "gap_to_target_pct": round((gap / target * 100.0) if target else 0.0, 4),
    }


def target_pressure_sizing_ok(candidate, ledger, settings, sizing_evidence):
    if not settings.get("target_pressure_enabled", False):
        return False, {"enabled": False}
    context = target_pressure_context(ledger)
    guard = candidate.get("entry_mode_learning_gate") or {}
    guard_stats = guard.get("stats") or {}
    allowed_modes = set(settings.get("target_pressure_allowed_entry_modes", []))
    mode = candidate.get("paper_entry_mode")
    failed = []
    if context["gap_to_target_pct"] < float(settings.get("target_pressure_min_gap_to_target_pct", 40.0)):
        failed.append("gap_to_target_below_pressure_gate")
    if sizing_evidence.get("cash_ratio_pct", 0.0) < float(settings.get("target_pressure_min_cash_ratio_pct", 70.0)):
        failed.append("cash_ratio_below_pressure_gate")
    if mode not in allowed_modes:
        failed.append("entry_mode_not_allowed_for_target_pressure")
    if guard.get("decision") not in {"allow", "allow_insufficient_sample"}:
        failed.append("entry_mode_learning_guard_not_allow")
    if float(guard_stats.get("win_rate_pct") or 0.0) < float(settings.get("target_pressure_min_entry_mode_win_rate_pct", 60.0)):
        failed.append("entry_mode_win_rate_below_pressure_gate")
    if float(guard_stats.get("realized_return_on_notional_pct") or 0.0) < float(settings.get("target_pressure_min_entry_mode_return_pct", 5.0)):
        failed.append("entry_mode_return_below_pressure_gate")
    if sizing_evidence.get("info_pressure_score", 0.0) < float(settings.get("target_pressure_min_info_score", 50.0)):
        failed.append("info_score_below_pressure_gate")
    if sizing_evidence.get("oos_win_rate_pct", 0.0) < float(settings.get("target_pressure_min_oos_win_rate_pct", 50.0)):
        failed.append("oos_win_rate_below_pressure_gate")
    if sizing_evidence.get("oos_net_return_pct", 0.0) < float(settings.get("target_pressure_min_oos_net_return_pct", 80.0)):
        failed.append("oos_return_below_pressure_gate")
    if sizing_evidence.get("oos_max_drawdown_pct", -999.0) < float(settings.get("target_pressure_min_oos_drawdown_pct", -30.0)):
        failed.append("oos_drawdown_below_pressure_gate")
    if sizing_evidence.get("train_net_return_pct", -999.0) < float(settings.get("target_pressure_min_train_net_return_pct", 20.0)):
        failed.append("train_return_below_pressure_gate")
    return not failed, {
        "enabled": True,
        "decision": "allow" if not failed else "block",
        "failed_gates": failed,
        "target_pressure_context": context,
        "entry_mode_learning_gate": guard,
    }


def candidate_sizing_decision(candidate, ledger, settings):
    if not settings.get("dynamic_sizing_enabled", True):
        base = candidate_notional(candidate, settings)
        risk_multiplier = float(settings.get("paper_risk_size_multiplier", 1.0) or 0.0)
        risk_max = float(settings.get("paper_risk_max_new_entry_notional_usd", base) or 0.0)
        return {
            "enabled": False,
            "planned_notional_usd": round(min(base * risk_multiplier, risk_max, float(ledger.get("cash_usd", 0.0))), 6),
            "reason": "dynamic_sizing_disabled_risk_control_applied",
            "evidence": {},
        }

    info = candidate.get("info_signal") or {}
    oos = candidate.get("oos_summary") or {}
    train = candidate.get("train_summary") or {}
    validation = candidate.get("validation_summary") or {}
    cash = float(ledger.get("cash_usd", 0.0) or 0.0)
    equity = float(ledger.get("equity_usd") or cash or 0.0)
    cash_ratio = cash / equity * 100.0 if equity else 0.0
    capacity = trade_capacity_usd(ledger, settings)
    mode = candidate.get("paper_entry_mode")
    planned = float(settings.get("min_trade_notional_usd", settings.get("exploratory_notional_usd", 25.0)))
    tier = "min_probe"
    reasons = []

    info_score = float(info.get("info_pressure_score", 0.0) or 0.0)
    oos_win = float(oos.get("win_rate_pct", 0.0) or 0.0)
    oos_final = float(oos.get("final_capital", 0.0) or 0.0)
    oos_dd = float(oos.get("max_drawdown_pct", -999.0) or -999.0)
    oos_return = float(oos.get("net_return_pct", 0.0) or 0.0)
    train_return = float(train.get("net_return_pct", -999.0) or -999.0)
    cash_pressure = cash_ratio >= float(settings.get("high_cash_ratio_threshold_pct", 70.0))

    if mode == "validation_probe":
        planned = float(settings.get("validation_probe_notional_usd", settings.get("min_trade_notional_usd", 25.0)))
        tier = "validation_probe"
        reasons.append("paper_only_current_signal_validation_sample")
    elif mode == "strict_strategy_gate":
        planned = float(settings.get("strict_default_notional_usd", settings.get("default_notional_usd", 75.0)))
        tier = "strict_default"
        robust = (
            train_return >= float(settings.get("strict_full_size_min_train_net_return_pct", 20.0))
            and oos_win >= float(settings.get("strict_full_size_min_oos_win_rate_pct", 60.0))
            and oos_dd >= float(settings.get("strict_full_size_min_oos_drawdown_pct", -20.0))
        )
        if robust:
            planned = float(settings.get("strict_max_notional_usd", settings.get("max_single_trade_notional_usd", 150.0)))
            tier = "strict_full_size"
            reasons.append("strict_signal_and_train_oos_robust")
        else:
            reasons.append("strict_signal_but_not_full_size_robust")
    else:
        planned = float(settings.get("min_trade_notional_usd", settings.get("exploratory_notional_usd", 25.0)))
        tier = "min_probe"
        mid_ok = (
            info_score >= float(settings.get("mid_probe_min_info_score", 30.0))
            and oos_win >= float(settings.get("mid_probe_min_oos_win_rate_pct", 60.0))
            and oos_final >= float(settings.get("mid_probe_min_oos_final_capital", 650.0))
            and oos_dd >= float(settings.get("mid_probe_min_oos_drawdown_pct", -25.0))
        )
        accelerated_ok = (
            info_score >= float(settings.get("accelerated_probe_min_info_score", 40.0))
            and oos_win >= float(settings.get("accelerated_probe_min_oos_win_rate_pct", 70.0))
            and oos_final >= float(settings.get("accelerated_probe_min_oos_final_capital", 800.0))
            and oos_dd >= float(settings.get("accelerated_probe_min_oos_drawdown_pct", -15.0))
        )
        if accelerated_ok and cash_pressure:
            planned = float(settings.get("accelerated_probe_notional_usd", 75.0))
            tier = "accelerated_probe"
            reasons.append("exploratory_signal_with_high_info_oos_and_idle_cash")
        elif mid_ok:
            planned = float(settings.get("mid_probe_notional_usd", 50.0))
            tier = "mid_probe"
            reasons.append("exploratory_signal_with_sufficient_oos_evidence")
        else:
            reasons.append("exploratory_min_size_until_evidence_improves")

    sizing_evidence = {
        "info_pressure_score": info_score,
        "oos_win_rate_pct": oos_win,
        "oos_final_capital": oos_final,
        "oos_net_return_pct": oos_return,
        "oos_max_drawdown_pct": oos_dd,
        "train_net_return_pct": train_return,
        "cash_ratio_pct": round(cash_ratio, 4),
        "open_exposure_usd": round(open_exposure_usd(ledger), 6),
    }
    target_pressure_ok, target_pressure = target_pressure_sizing_ok(candidate, ledger, settings, sizing_evidence)
    if target_pressure_ok:
        pressure_notional = float(settings.get("target_pressure_notional_usd", planned))
        if pressure_notional > planned:
            planned = pressure_notional
            tier = f"target_pressure_{tier}"
            reasons.append("monthly_double_target_pressure_with_positive_entry_mode_evidence")

    risk_multiplier = float(settings.get("paper_risk_size_multiplier", 1.0) or 0.0)
    risk_max = float(settings.get("paper_risk_max_new_entry_notional_usd", planned) or 0.0)
    if risk_multiplier < 1.0:
        planned *= risk_multiplier
        reasons.append("portfolio_risk_control_size_reduction")
    capped = min(planned, capacity, risk_max)
    if capped < planned:
        reasons.append("capped_by_cash_or_exposure_limit")
    if capped < float(settings.get("min_trade_notional_usd", 25.0)):
        reasons.append("below_min_trade_capacity")
    return {
        "enabled": True,
        "tier": tier,
        "planned_notional_usd": round(max(0.0, capped), 6),
        "raw_notional_usd": round(planned, 6),
        "capacity_usd": round(capacity, 6),
        "cash_ratio_pct": round(cash_ratio, 4),
        "reason": ", ".join(reasons),
        "evidence": sizing_evidence,
        "target_pressure": target_pressure,
    }


def reserve_candidate_capacity(shadow_ledger, candidate):
    notional = float(candidate.get("planned_notional_usd", 0.0) or 0.0)
    shadow_ledger["cash_usd"] = round(max(0.0, float(shadow_ledger.get("cash_usd", 0.0) or 0.0) - notional), 6)
    shadow_ledger.setdefault("open_positions", []).append(
        {
            "symbol": candidate["symbol"],
            "notional_usd": notional,
            "paper_trade_id": f"reserved-{candidate['symbol']}-{candidate.get('candidate_key', 'candidate')}",
        }
    )


def open_positions_for_symbol(ledger, symbol):
    return [p for p in ledger.get("open_positions", []) if p.get("symbol") == symbol]


def symbol_open_notional_usd(ledger, symbol):
    return sum(float(pos.get("notional_usd", 0.0) or 0.0) for pos in open_positions_for_symbol(ledger, symbol))


def scale_in_eligibility(candidate, ledger, settings, info):
    if not settings.get("scale_in_enabled", False):
        return False, "scale_in_disabled", {}
    symbol = candidate["symbol"]
    positions = open_positions_for_symbol(ledger, symbol)
    if not positions:
        return False, "no_existing_position", {}
    info_score = float(info.get("info_pressure_score", 0.0) or 0.0)
    best_pnl = max(float(pos.get("unrealized_pnl_pct", 0.0) or 0.0) for pos in positions)
    best_max_pnl = max(float(pos.get("max_unrealized_pnl_pct", best_pnl) or best_pnl) for pos in positions)
    protection_armed = any((pos.get("risk_state") or {}).get("profit_protection_armed") for pos in positions)
    equity = float(ledger.get("equity_usd") or 0.0)
    cash = float(ledger.get("cash_usd", 0.0) or 0.0)
    cash_ratio = cash / equity * 100.0 if equity > 0 else 0.0
    base_max_positions = int(settings.get("scale_in_max_open_positions_per_symbol", 2))
    sprint_enabled = bool(settings.get("target_sprint_scale_in_enabled", False))
    sprint_max_positions = int(settings.get("target_sprint_max_open_positions_per_symbol", base_max_positions))
    sprint_min_cash_ratio = float(settings.get("target_sprint_min_cash_ratio_pct", 65.0))
    sprint_min_info_score = float(settings.get("target_sprint_min_info_score", settings.get("scale_in_min_info_score", 45.0)))
    sprint_min_best_pnl = float(settings.get("target_sprint_min_best_pnl_pct", settings.get("scale_in_min_existing_pnl_pct", 4.0)))
    target_sprint = (
        sprint_enabled
        and len(positions) >= base_max_positions
        and len(positions) < sprint_max_positions
        and cash_ratio >= sprint_min_cash_ratio
        and info_score >= sprint_min_info_score
        and (best_pnl >= sprint_min_best_pnl or protection_armed)
    )
    effective_max_positions = sprint_max_positions if target_sprint else base_max_positions
    if len(positions) >= effective_max_positions:
        return False, "scale_in_symbol_position_limit", {
            "existing_position_count": len(positions),
            "target_sprint_checked": sprint_enabled,
            "target_sprint_allowed": target_sprint,
            "effective_max_positions": effective_max_positions,
        }
    required_info_score = sprint_min_info_score if target_sprint else float(settings.get("scale_in_min_info_score", 45.0))
    if info_score < required_info_score:
        return False, "scale_in_info_score_too_low", {
            "info_pressure_score": info_score,
            "required_info_score": required_info_score,
            "target_sprint_checked": sprint_enabled,
        }
    min_pnl = float(settings.get("scale_in_min_existing_pnl_pct", 4.0))
    if best_pnl < min_pnl and not protection_armed:
        return False, "scale_in_existing_position_not_strong_enough", {"best_pnl_pct": round(best_pnl, 4), "protection_armed": protection_armed}
    min_max_pnl = float(settings.get("scale_in_min_existing_max_pnl_pct", min_pnl))
    if best_max_pnl < min_max_pnl:
        return False, "scale_in_existing_max_pnl_too_low", {"best_max_pnl_pct": round(best_max_pnl, 4)}
    symbol_exposure_pct = (
        float(settings.get("target_sprint_max_symbol_exposure_pct_of_equity", settings.get("scale_in_max_symbol_exposure_pct_of_equity", 20.0)))
        if target_sprint
        else float(settings.get("scale_in_max_symbol_exposure_pct_of_equity", 20.0))
    )
    symbol_cap = equity * symbol_exposure_pct / 100.0
    available_symbol_capacity = max(0.0, symbol_cap - symbol_open_notional_usd(ledger, symbol))
    if available_symbol_capacity < float(settings.get("min_trade_notional_usd", 25.0)):
        return False, "scale_in_symbol_exposure_cap_reached", {
            "symbol_open_notional_usd": round(symbol_open_notional_usd(ledger, symbol), 6),
            "symbol_cap_usd": round(symbol_cap, 6),
        }
    return True, "scale_in_allowed", {
        "existing_position_ids": [pos.get("paper_trade_id") for pos in positions],
        "best_pnl_pct": round(best_pnl, 4),
        "best_max_pnl_pct": round(best_max_pnl, 4),
        "protection_armed": protection_armed,
        "cash_ratio_pct": round(cash_ratio, 4),
        "target_sprint_scale_in": target_sprint,
        "symbol_exposure_cap_pct": symbol_exposure_pct,
        "available_symbol_capacity_usd": round(available_symbol_capacity, 6),
    }


def apply_scale_in_sizing_cap(candidate, ledger, settings, eligibility_details):
    sizing = candidate_sizing_decision(candidate, ledger, settings)
    planned = float(sizing.get("planned_notional_usd", 0.0) or 0.0)
    if eligibility_details.get("target_sprint_scale_in"):
        planned = max(planned, float(settings.get("target_sprint_scale_in_notional_usd", planned) or planned))
    planned = min(
        planned,
        float(settings.get("scale_in_max_notional_usd", settings.get("min_trade_notional_usd", 25.0))),
        float(eligibility_details.get("available_symbol_capacity_usd", planned) or planned),
        float(settings.get("paper_risk_max_new_entry_notional_usd", planned) or 0.0),
    )
    sizing = dict(sizing)
    sizing["tier"] = f"scale_in_{sizing.get('tier', 'probe')}"
    sizing["planned_notional_usd"] = round(max(0.0, planned), 6)
    sizing["raw_notional_usd"] = min(
        float(sizing.get("raw_notional_usd", planned) or planned),
        float(settings.get("scale_in_max_notional_usd", planned)),
    )
    sizing["reason"] = (sizing.get("reason", "") + ", controlled_winner_scale_in").strip(", ")
    sizing["scale_in_evidence"] = eligibility_details
    return sizing


def entry_mode_realized_stats(ledger, mode):
    closed = [
        trade
        for trade in ledger.get("closed_trades", [])
        if trade.get("paper_entry_mode") == mode and trade.get("status") == "closed"
    ]
    realized = sum(float(trade.get("realized_pnl_usd") or 0.0) for trade in closed)
    notional = sum(float(trade.get("notional_usd") or 0.0) for trade in closed)
    hit_count = sum(1 for trade in closed if trade.get("outcome") == "hit")
    count = len(closed)
    win_rate = (hit_count / count * 100.0) if count else None
    return_on_notional = (realized / notional * 100.0) if notional else None
    return {
        "mode": mode,
        "closed_count": count,
        "hit_count": hit_count,
        "win_rate_pct": round(win_rate, 4) if win_rate is not None else None,
        "realized_pnl_usd": round(realized, 6),
        "closed_notional_usd": round(notional, 6),
        "realized_return_on_notional_pct": round(return_on_notional, 4) if return_on_notional is not None else None,
    }


def entry_mode_learning_gate(mode, ledger, settings):
    guard = settings.get("paper_entry_mode_learning_guard") or {}
    if not guard.get("enabled", False):
        return True, {"enabled": False, "mode": mode}
    mode_policy = (guard.get("modes") or {}).get(mode, {})
    stats = entry_mode_realized_stats(ledger, mode)
    min_closed = int(mode_policy.get("min_closed_count", guard.get("global_min_closed_count", 3)))
    if stats["closed_count"] < min_closed:
        return True, {
            "enabled": True,
            "mode": mode,
            "decision": "allow_insufficient_sample",
            "stats": stats,
            "min_closed_count": min_closed,
        }
    min_win = float(mode_policy.get("min_win_rate_pct", guard.get("global_min_win_rate_pct", 45.0)))
    min_return = float(
        mode_policy.get(
            "min_realized_return_on_notional_pct",
            guard.get("global_min_realized_return_on_notional_pct", 0.0),
        )
    )
    failed = []
    if stats["win_rate_pct"] is not None and stats["win_rate_pct"] < min_win:
        failed.append("win_rate_below_mode_gate")
    if stats["realized_return_on_notional_pct"] is not None and stats["realized_return_on_notional_pct"] < min_return:
        failed.append("realized_return_below_mode_gate")
    allow = not failed or bool(guard.get("report_only", False))
    return allow, {
        "enabled": True,
        "mode": mode,
        "decision": "allow" if allow and not failed else ("report_only_allow" if allow else "block"),
        "failed_gates": failed,
        "stats": stats,
        "thresholds": {
            "min_closed_count": min_closed,
            "min_win_rate_pct": min_win,
            "min_realized_return_on_notional_pct": min_return,
        },
        "cooldown_reason": mode_policy.get("cooldown_reason"),
    }


def post_loss_reentry_cooldown(symbol, mode, ledger, settings, now):
    if not settings.get("post_loss_reentry_cooldown_enabled", True):
        return True, {"enabled": False}
    modes = set(settings.get("post_loss_reentry_cooldown_modes") or ["info_exploratory_probe"])
    if mode not in modes:
        return True, {"enabled": True, "decision": "mode_not_scoped", "mode": mode}
    exit_reasons = set(settings.get("post_loss_reentry_cooldown_exit_reasons") or ["exploratory_capital_protection_loss_cut"])
    cooldown_hours = float(settings.get("post_loss_reentry_cooldown_hours", 6.0))
    for trade in reversed(ledger.get("closed_trades", [])):
        if trade.get("symbol") != symbol or trade.get("paper_entry_mode") != mode:
            continue
        if trade.get("exit_reason") not in exit_reasons:
            continue
        closed_at = trade.get("closed_at")
        if not closed_at:
            continue
        try:
            age_hours = (now - parse_iso(closed_at)).total_seconds() / 3600.0
        except Exception:
            continue
        if age_hours < cooldown_hours:
            return False, {
                "enabled": True,
                "decision": "block",
                "symbol": symbol,
                "mode": mode,
                "exit_reason": trade.get("exit_reason"),
                "closed_at": closed_at,
                "age_hours": round(age_hours, 4),
                "cooldown_hours": cooldown_hours,
                "paper_trade_id": trade.get("paper_trade_id"),
            }
        return True, {
            "enabled": True,
            "decision": "cooldown_elapsed",
            "symbol": symbol,
            "mode": mode,
            "age_hours": round(age_hours, 4),
            "cooldown_hours": cooldown_hours,
            "paper_trade_id": trade.get("paper_trade_id"),
        }
    return True, {"enabled": True, "decision": "no_recent_loss_exit", "symbol": symbol, "mode": mode}


def recent_loss_quality_gate(candidate, mode, ledger, settings, info, now):
    if not settings.get("recent_loss_quality_gate_enabled", True):
        return True, {"enabled": False}
    modes = set(settings.get("recent_loss_quality_gate_modes") or ["info_exploratory_probe", "validation_probe"])
    if mode not in modes:
        return True, {"enabled": True, "decision": "mode_not_scoped", "mode": mode}
    symbol = candidate.get("symbol")
    if not symbol:
        return False, {"enabled": True, "decision": "block", "reason": "missing_symbol"}
    exit_reasons = set(
        settings.get("recent_loss_quality_gate_exit_reasons")
        or ["exploratory_capital_protection_loss_cut", "post_loss_reentry_cooldown_violation", "stop"]
    )
    lookback_hours = float(settings.get("recent_loss_quality_gate_lookback_hours", 48.0))
    for trade in reversed(ledger.get("closed_trades", [])):
        if trade.get("symbol") != symbol or trade.get("paper_entry_mode") != mode:
            continue
        if trade.get("exit_reason") not in exit_reasons:
            continue
        closed_at = trade.get("closed_at")
        if not closed_at:
            continue
        try:
            age_hours = (now - parse_iso(closed_at)).total_seconds() / 3600.0
        except Exception:
            continue
        if age_hours > lookback_hours:
            return True, {
                "enabled": True,
                "decision": "recent_loss_outside_lookback",
                "symbol": symbol,
                "mode": mode,
                "age_hours": round(age_hours, 4),
                "lookback_hours": lookback_hours,
                "paper_trade_id": trade.get("paper_trade_id"),
            }
        oos = candidate.get("oos_summary") or {}
        train = candidate.get("train_summary") or {}
        info_score = float((info or {}).get("info_pressure_score", 0) or 0)
        requirements = {
            "min_info_score": float(settings.get("recent_loss_quality_gate_min_info_score", 45.0)),
            "min_oos_trades": int(settings.get("recent_loss_quality_gate_min_oos_trades", 8)),
            "min_oos_win_rate_pct": float(settings.get("recent_loss_quality_gate_min_oos_win_rate_pct", 60.0)),
            "min_oos_net_return_pct": float(settings.get("recent_loss_quality_gate_min_oos_net_return_pct", 50.0)),
            "min_oos_max_drawdown_pct": float(settings.get("recent_loss_quality_gate_min_oos_drawdown_pct", -20.0)),
            "min_train_final_capital": float(settings.get("recent_loss_quality_gate_min_train_final_capital", settings.get("default_initial_capital_usd", 500.0))),
        }
        evidence = {
            "info_score": info_score,
            "oos_trades": oos.get("trade_count"),
            "oos_win_rate_pct": oos.get("win_rate_pct"),
            "oos_net_return_pct": oos.get("net_return_pct"),
            "oos_max_drawdown_pct": oos.get("max_drawdown_pct"),
            "train_final_capital": train.get("final_capital"),
        }
        failures = []
        if info_score < requirements["min_info_score"]:
            failures.append("info_score_below_recent_loss_quality_gate")
        if int(oos.get("trade_count") or 0) < requirements["min_oos_trades"]:
            failures.append("oos_trade_count_below_recent_loss_quality_gate")
        if float(oos.get("win_rate_pct") or 0.0) < requirements["min_oos_win_rate_pct"]:
            failures.append("oos_win_rate_below_recent_loss_quality_gate")
        if float(oos.get("net_return_pct") or -999.0) < requirements["min_oos_net_return_pct"]:
            failures.append("oos_net_return_below_recent_loss_quality_gate")
        if float(oos.get("max_drawdown_pct") or -999.0) < requirements["min_oos_max_drawdown_pct"]:
            failures.append("oos_drawdown_below_recent_loss_quality_gate")
        if float(train.get("final_capital") or 0.0) < requirements["min_train_final_capital"]:
            failures.append("train_final_capital_below_recent_loss_quality_gate")
        detail = {
            "enabled": True,
            "symbol": symbol,
            "mode": mode,
            "recent_loss_exit_reason": trade.get("exit_reason"),
            "recent_loss_closed_at": closed_at,
            "recent_loss_age_hours": round(age_hours, 4),
            "lookback_hours": lookback_hours,
            "paper_trade_id": trade.get("paper_trade_id"),
            "requirements": requirements,
            "evidence": evidence,
            "failures": failures,
        }
        if failures:
            detail["decision"] = "block_recent_loss_requires_quality_upgrade"
            return False, detail
        detail["decision"] = "allow_quality_upgraded_after_recent_loss"
        return True, detail
    return True, {"enabled": True, "decision": "no_recent_loss_in_scope", "symbol": symbol, "mode": mode}


def recent_symbol_loss_profile(symbol, ledger, settings, now):
    cfg_enabled = bool(settings.get("recent_loss_rotation_enabled", True))
    if not cfg_enabled:
        return {"enabled": False, "symbol": symbol, "loss_count": 0, "penalty_points": 0.0}
    lookback_hours = float(settings.get("recent_loss_rotation_lookback_hours", 72.0))
    penalty_per_loss = float(settings.get("recent_loss_rotation_penalty_points_per_loss", 35.0))
    max_penalty = float(settings.get("recent_loss_rotation_max_penalty_points", 120.0))
    exit_reasons = set(
        settings.get("recent_loss_rotation_exit_reasons")
        or [
            "stop",
            "exploratory_capital_protection_loss_cut",
            "post_loss_reentry_cooldown_violation",
            "target_sprint_loss_cut",
            "scale_in_loss_cut",
        ]
    )
    losses = []
    for trade in reversed(ledger.get("closed_trades", [])):
        if trade.get("symbol") != symbol:
            continue
        closed_at = trade.get("closed_at")
        if not closed_at:
            continue
        try:
            age_hours = (now - parse_iso(closed_at)).total_seconds() / 3600.0
        except Exception:
            continue
        if age_hours > lookback_hours:
            continue
        realized_pnl = float(trade.get("realized_pnl_usd") or 0.0)
        realized_pct = float(trade.get("realized_pnl_pct") or 0.0)
        failed = trade.get("outcome") == "failed" or realized_pnl < 0 or trade.get("exit_reason") in exit_reasons
        if not failed:
            continue
        losses.append(
            {
                "paper_trade_id": trade.get("paper_trade_id"),
                "exit_reason": trade.get("exit_reason"),
                "closed_at": closed_at,
                "age_hours": round(age_hours, 4),
                "realized_pnl_usd": round(realized_pnl, 6),
                "realized_pnl_pct": round(realized_pct, 4),
                "paper_entry_mode": trade.get("paper_entry_mode"),
            }
        )
    penalty = min(max_penalty, penalty_per_loss * len(losses))
    return {
        "enabled": True,
        "symbol": symbol,
        "lookback_hours": lookback_hours,
        "loss_count": len(losses),
        "penalty_points": round(penalty, 4),
        "losses": losses[:5],
    }


def parse_strategy_family_text(text):
    if not text:
        return None, None
    parts = str(text).split()
    if not parts:
        return None, None
    interval = parts[0] if parts[0] in {"15m", "1h", "4h", "1d"} else None
    family = parts[1] if interval and len(parts) > 1 else parts[0]
    return interval, family


def candidate_strategy_identity(candidate):
    strategy = candidate.get("strategy") or {}
    interval = candidate.get("interval")
    family = strategy.get("family")
    if not family:
        parsed_interval, parsed_family = parse_strategy_family_text(candidate.get("strategy_family"))
        interval = interval or parsed_interval
        family = parsed_family
    return interval, family


def trade_strategy_identity(trade):
    strategy_candidate = trade.get("strategy_candidate") or {}
    strategy = strategy_candidate.get("strategy") or {}
    interval = strategy_candidate.get("interval")
    family = strategy.get("family")
    if not family:
        parsed_interval, parsed_family = parse_strategy_family_text(trade.get("strategy_family"))
        interval = interval or parsed_interval
        family = parsed_family
    return interval, family, trade.get("paper_entry_mode")


def estimate_candidate_entry_mode(candidate, settings, info):
    stage = candidate.get("stage")
    validation = candidate.get("validation_summary") or {}
    selection_contract_ok = (
        candidate.get("holdout_used_for_selection") is False
        and bool(validation)
        and float(candidate.get("selection_score") or -999999.0) > -999999.0
    )
    if not selection_contract_ok:
        return "not_entry_eligible"
    allowed_stages = {"target_research_pass_current_signal", "paper_forward_candidate_current_signal"}
    if stage in allowed_stages:
        return "strict_strategy_gate"
    oos = candidate.get("oos_summary") or {}
    train = candidate.get("train_summary") or {}
    validation_probe_stages = set(settings.get("validation_probe_allowed_stages", []))
    validation_probe_ok = (
        bool(settings.get("validation_probe_enabled", False))
        and stage in validation_probe_stages
        and int(oos.get("trade_count") or 0) >= int(settings.get("validation_probe_min_oos_trades", 10))
        and float(oos.get("win_rate_pct") or 0.0) >= float(settings.get("validation_probe_min_oos_win_rate_pct", 50.0))
        and float(oos.get("final_capital") or 0.0) >= float(settings.get("validation_probe_min_oos_final_capital", settings.get("default_initial_capital_usd", 500.0)))
        and float(oos.get("net_return_pct") or -999.0) >= float(settings.get("validation_probe_min_oos_net_return_pct", 0.0))
        and float(oos.get("max_drawdown_pct") or -999.0) >= float(settings.get("validation_probe_min_oos_drawdown_pct", -35.0))
        and float(train.get("final_capital") or 0.0) >= float(settings.get("validation_probe_min_train_final_capital", 0.0))
    )
    if validation_probe_ok:
        return "validation_probe"
    exploratory_stages = set(settings.get("exploratory_allowed_stages", []))
    exploratory_ok = (
        bool(settings.get("exploratory_probe_enabled", True))
        and stage in exploratory_stages
        and float((info or {}).get("info_pressure_score", 0) or 0) >= float(settings.get("exploratory_min_info_score", 30))
        and int(oos.get("trade_count") or 0) >= int(settings.get("exploratory_min_oos_trades", 5))
        and float(oos.get("max_drawdown_pct") or -999.0) >= float(settings.get("exploratory_min_oos_drawdown_pct", -35))
    )
    if exploratory_ok:
        return "info_exploratory_probe"
    return "not_entry_eligible"


def recent_strategy_loss_profile(candidate, ledger, settings, now, info=None):
    if not settings.get("recent_strategy_loss_rotation_enabled", True):
        return {"enabled": False, "loss_count": 0, "penalty_points": 0.0}
    candidate_interval, candidate_family = candidate_strategy_identity(candidate)
    candidate_mode = estimate_candidate_entry_mode(candidate, settings, info or {})
    lookback_hours = float(settings.get("recent_strategy_loss_rotation_lookback_hours", 96.0))
    exact_penalty = float(settings.get("recent_strategy_loss_rotation_exact_penalty_points", 45.0))
    family_interval_penalty = float(settings.get("recent_strategy_loss_rotation_family_interval_penalty_points", 28.0))
    family_mode_penalty = float(settings.get("recent_strategy_loss_rotation_family_mode_penalty_points", 22.0))
    family_penalty = float(settings.get("recent_strategy_loss_rotation_family_penalty_points", 10.0))
    max_penalty = float(settings.get("recent_strategy_loss_rotation_max_penalty_points", 150.0))
    exit_reasons = set(
        settings.get("recent_strategy_loss_rotation_exit_reasons")
        or [
            "stop",
            "exploratory_capital_protection_loss_cut",
            "post_loss_reentry_cooldown_violation",
            "target_sprint_loss_cut",
            "scale_in_loss_cut",
        ]
    )
    losses = []
    penalty = 0.0
    for trade in reversed(ledger.get("closed_trades", [])):
        closed_at = trade.get("closed_at")
        if not closed_at:
            continue
        try:
            age_hours = (now - parse_iso(closed_at)).total_seconds() / 3600.0
        except Exception:
            continue
        if age_hours > lookback_hours:
            continue
        realized_pnl = float(trade.get("realized_pnl_usd") or 0.0)
        failed = trade.get("outcome") == "failed" or realized_pnl < 0 or trade.get("exit_reason") in exit_reasons
        if not failed:
            continue
        trade_interval, trade_family, trade_mode = trade_strategy_identity(trade)
        if not candidate_family or not trade_family or candidate_family != trade_family:
            continue
        match_type = "family"
        match_penalty = family_penalty
        if candidate_interval == trade_interval and candidate_mode == trade_mode:
            match_type = "family_interval_mode"
            match_penalty = exact_penalty
        elif candidate_interval == trade_interval:
            match_type = "family_interval"
            match_penalty = family_interval_penalty
        elif candidate_mode == trade_mode:
            match_type = "family_mode"
            match_penalty = family_mode_penalty
        penalty += match_penalty
        losses.append(
            {
                "paper_trade_id": trade.get("paper_trade_id"),
                "symbol": trade.get("symbol"),
                "match_type": match_type,
                "match_penalty_points": round(match_penalty, 4),
                "strategy_family": trade_family,
                "interval": trade_interval,
                "paper_entry_mode": trade_mode,
                "exit_reason": trade.get("exit_reason"),
                "closed_at": closed_at,
                "age_hours": round(age_hours, 4),
                "realized_pnl_usd": round(realized_pnl, 6),
                "realized_pnl_pct": round(float(trade.get("realized_pnl_pct") or 0.0), 4),
            }
        )
    penalty = min(max_penalty, penalty)
    return {
        "enabled": True,
        "candidate_strategy_family": candidate_family,
        "candidate_interval": candidate_interval,
        "candidate_entry_mode_estimate": candidate_mode,
        "lookback_hours": lookback_hours,
        "loss_count": len(losses),
        "penalty_points": round(penalty, 4),
        "losses": losses[:5],
    }


def normalized_label(value):
    return " ".join(str(value or "").strip().lower().split())


def recovery_plan_items(plan, keys):
    items = []
    if not isinstance(plan, dict):
        return items
    for key in keys:
        for item in plan.get(key) or []:
            if isinstance(item, dict):
                items.append({**item, "_source_key": key})
    return items


def find_recovery_match(plan, names, keys, block_statuses):
    name_set = {normalized_label(name) for name in names if name}
    if not name_set:
        return None
    for item in recovery_plan_items(plan, keys):
        item_name = normalized_label(item.get("name"))
        if item_name in name_set and str(item.get("status")) in block_statuses:
            return item
    return None


def eligible_recovery_matches(plan, names, keys):
    name_set = {normalized_label(name) for name in names if name}
    if not name_set:
        return []
    matches = []
    for item in recovery_plan_items(plan, keys):
        if normalized_label(item.get("name")) in name_set:
            matches.append(item)
    return matches


def latest_strategy_recovery_optimizer_payload(experiment_dir=None):
    experiment_dir = Path(experiment_dir or (ROOT / "experiments"))
    if not experiment_dir.exists():
        return {
            "enabled": True,
            "status": "missing",
            "path": None,
            "eligible_retest_queue": [],
            "operator_note": "No strategy recovery optimizer artifact found; candidate ranking uses normal evidence only.",
        }
    paths = sorted(
        experiment_dir.glob("*strategy-recovery-optimizer.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        safety_flags = [
            payload.get("live_orders_enabled") is True,
            payload.get("private_api_keys_used") is True,
            payload.get("private_api_used") is True,
            payload.get("allow_real_orders") is True,
        ]
        if any(safety_flags):
            return {
                "enabled": True,
                "status": "blocked_safety_flag",
                "path": str(path.relative_to(WORKSPACE_ROOT)) if WORKSPACE_ROOT in path.parents else str(path),
                "eligible_retest_queue": [],
                "operator_note": "Strategy recovery optimizer artifact contained a live/private API safety flag; ignored.",
            }
        queue = payload.get("eligible_retest_queue") or []
        return {
            "enabled": True,
            "status": payload.get("status") or "ok",
            "path": str(path.relative_to(WORKSPACE_ROOT)) if WORKSPACE_ROOT in path.parents else str(path),
            "created_at": payload.get("created_at"),
            "eligible_retest_queue": queue,
            "eligible_retest_count": len(queue),
            "blocked_candidate_count": len(payload.get("blocked_candidates") or []),
            "monthly_goal_state": payload.get("monthly_goal_state") or {},
            "operator_note": "Recovery queue is research-only; it can alter paper candidate ordering but cannot bypass gates.",
        }
    return {
        "enabled": True,
        "status": "missing_valid_artifact",
        "path": None,
        "eligible_retest_queue": [],
        "operator_note": "No valid strategy recovery optimizer artifact found.",
    }


def strategy_recovery_queue_symbols(state, max_symbols=8):
    symbols = []
    for item in (state or {}).get("eligible_retest_queue") or []:
        symbol = str(item.get("symbol") or "").upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if len(symbols) >= int(max_symbols or 0):
            break
    return symbols


def strategy_recovery_queue_profile(candidate, settings, info=None):
    state = settings.get("strategy_recovery_optimizer_state") or {}
    if not settings.get("strategy_recovery_queue_ranking_enabled", True):
        return {"enabled": False, "decision": "disabled", "bonus_points": 0.0}
    queue = state.get("eligible_retest_queue") or []
    if not queue:
        return {
            "enabled": True,
            "decision": "no_queue",
            "status": state.get("status"),
            "path": state.get("path"),
            "bonus_points": 0.0,
        }
    symbol = str(candidate.get("symbol") or "").upper()
    interval, family = candidate_strategy_identity(candidate)
    mode = estimate_candidate_entry_mode(candidate, settings, info or {})
    symbol_interval_family_bonus = float(settings.get("strategy_recovery_queue_symbol_interval_family_bonus_points", 28.0))
    exact_mode_bonus = float(settings.get("strategy_recovery_queue_exact_mode_bonus_points", 12.0))
    current_signal_bonus = float(settings.get("strategy_recovery_queue_current_signal_bonus_points", 8.0))
    score_scale = float(settings.get("strategy_recovery_queue_score_scale", 0.05))
    max_bonus = float(settings.get("strategy_recovery_queue_max_bonus_points", 55.0))
    for index, item in enumerate(queue):
        item_symbol = str(item.get("symbol") or "").upper()
        item_interval = str(item.get("interval") or "")
        item_family = normalized_label(item.get("strategy_family"))
        item_mode = str(item.get("paper_entry_mode") or "")
        if item_symbol != symbol or item_interval != str(interval or "") or item_family != normalized_label(family):
            continue
        entry_mode_match = item_mode == mode
        raw_score = float(item.get("score") or 0.0)
        bonus = symbol_interval_family_bonus + min(max(raw_score, 0.0) * score_scale, max_bonus)
        if entry_mode_match:
            bonus += exact_mode_bonus
        if candidate.get("stage") in {"target_research_pass_current_signal", "paper_forward_candidate_current_signal"} or item.get("current_signal") is True:
            bonus += current_signal_bonus
        bonus = min(max_bonus, bonus)
        return {
            "enabled": True,
            "decision": "matched_recovery_retest_queue",
            "path": state.get("path"),
            "queue_rank": index + 1,
            "symbol": symbol,
            "interval": interval,
            "strategy_family": family,
            "candidate_entry_mode_estimate": mode,
            "queued_entry_mode": item_mode,
            "entry_mode_match": entry_mode_match,
            "queued_stage": item.get("stage"),
            "queued_current_signal": item.get("current_signal"),
            "queued_score": item.get("score"),
            "bonus_points": round(bonus, 4),
            "operator_note": "Ranking bonus only; current signal, liquidity, friction, capacity, recovery and learning gates still decide paper entry.",
        }
    return {
        "enabled": True,
        "decision": "not_in_recovery_retest_queue",
        "path": state.get("path"),
        "candidate_symbol": symbol,
        "candidate_interval": interval,
        "candidate_strategy_family": family,
        "candidate_entry_mode_estimate": mode,
        "bonus_points": 0.0,
    }


def validation_recovery_plan_gate(candidate, mode, settings):
    if not settings.get("validation_recovery_plan_gate_enabled", True):
        return True, {"enabled": False, "decision": "disabled"}
    plan = settings.get("validation_recovery_plan") or {}
    if not isinstance(plan, dict) or not plan:
        return True, {"enabled": True, "decision": "allow_no_recovery_plan"}
    block_statuses = set(settings.get("validation_recovery_plan_block_statuses") or ["retire_from_new_samples", "cooldown_until_retested"])
    block_groups = set(settings.get("validation_recovery_plan_block_groups") or ["entry_mode", "strategy_family", "interval", "symbol"])
    interval, family = candidate_strategy_identity(candidate)
    symbol = candidate.get("symbol")
    strategy_names = []
    if interval and family:
        strategy_names.append(f"{interval} {family}")
    if family:
        strategy_names.append(family)
    checks = [
        ("entry_mode", [mode], ["retired_entry_modes", "cooldown_entry_modes"]),
        ("strategy_family", strategy_names, ["retired_strategy_families", "cooldown_strategy_families"]),
        ("interval", [interval], ["weak_intervals"]),
        ("symbol", [symbol], ["weak_symbols"]),
    ]
    blocking_matches = []
    for group, names, keys in checks:
        if group not in block_groups:
            continue
        match = find_recovery_match(plan, names, keys, block_statuses)
        if match:
            blocking_matches.append((group, match))
    interval_only_quality_scout_retest = (
        mode == "current_signal_quality_scout_probe"
        and settings.get("validation_recovery_plan_allow_quality_scout_weak_interval_retest", True)
        and blocking_matches
        and all(group == "interval" for group, _match in blocking_matches)
    )
    if interval_only_quality_scout_retest:
        group, match = blocking_matches[0]
        return True, {
            "enabled": True,
            "decision": "allow_quality_scout_weak_interval_retest",
            "matched_group": group,
            "matched_name": match.get("name"),
            "matched_status": match.get("status"),
            "matched_source_key": match.get("_source_key"),
            "entry_mode": mode,
            "candidate_symbol": symbol,
            "candidate_interval": interval,
            "candidate_strategy_family": family,
            "new_sample_policy": plan.get("new_sample_policy"),
            "operator_note": (
                "Quality scout may retest a weak interval with minimum paper size only; this does not bypass "
                "retired entry modes, weak symbols, liquidity, capacity, or live-trading safety."
            ),
        }
    if blocking_matches:
        priority = {"entry_mode": 0, "strategy_family": 1, "symbol": 2, "interval": 3}
        group, match = sorted(blocking_matches, key=lambda item: priority.get(item[0], 9))[0]
        return False, {
            "enabled": True,
            "decision": "block_validation_recovery_plan",
            "matched_group": group,
            "matched_name": match.get("name"),
            "matched_status": match.get("status"),
            "matched_source_key": match.get("_source_key"),
            "reason": match.get("reason"),
            "closed_count": match.get("closed_count"),
            "win_rate_pct": match.get("win_rate_pct"),
            "realized_net_return_pct": match.get("realized_net_return_pct"),
            "new_sample_policy": plan.get("new_sample_policy"),
            "operator_note": "Validation recovery plan blocks new paper samples from retired/cooldown groups; this is paper-only and never a live trade decision.",
        }
    eligible_matches = []
    if "entry_mode" in block_groups:
        eligible_matches.extend(eligible_recovery_matches(plan, [mode], ["eligible_entry_modes"]))
    if "strategy_family" in block_groups:
        eligible_matches.extend(eligible_recovery_matches(plan, strategy_names, ["eligible_strategy_families"]))
    return True, {
        "enabled": True,
        "decision": "allow_validation_recovery_plan",
        "entry_mode": mode,
        "candidate_symbol": symbol,
        "candidate_interval": interval,
        "candidate_strategy_family": family,
        "eligible_matches": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "closed_count": item.get("closed_count"),
                "win_rate_pct": item.get("win_rate_pct"),
                "realized_net_return_pct": item.get("realized_net_return_pct"),
                "source_key": item.get("_source_key"),
            }
            for item in eligible_matches[:4]
        ],
        "new_sample_policy": plan.get("new_sample_policy"),
    }


def candidate_selection_rank(candidate, ledger, info_by_symbol, settings, now):
    symbol = candidate.get("symbol")
    stage = candidate.get("stage")
    info = info_by_symbol.get(symbol, {}) if info_by_symbol else {}
    oos = candidate.get("oos_summary") or {}
    train = candidate.get("train_summary") or {}
    validation = candidate.get("validation_summary") or {}
    stage_scores = {
        "target_research_pass_current_signal": 300.0,
        "paper_forward_candidate_current_signal": 260.0,
        "paper_only": 160.0,
        "research_watch": 120.0,
        "target_research_pass_wait_signal": 80.0,
    }
    stage_score = stage_scores.get(stage, 0.0)
    info_score = float(info.get("info_pressure_score", 0) or 0)
    oos_win = float(oos.get("win_rate_pct") or 0.0)
    oos_return = float(oos.get("net_return_pct") or 0.0)
    oos_final = float(oos.get("final_capital") or 0.0)
    oos_drawdown = float(oos.get("max_drawdown_pct") or -100.0)
    train_return = float(train.get("net_return_pct") or 0.0)
    validation_return = float(validation.get("net_return_pct") or 0.0)
    validation_win = float(validation.get("win_rate_pct") or 0.0)
    pre_holdout_selection_score = float(candidate.get("selection_score") or -999999.0)
    holdout_concentration = float(oos.get("largest_winner_share_pct") or 100.0)
    symbol_loss_profile = recent_symbol_loss_profile(symbol, ledger, settings, now)
    strategy_loss_profile = recent_strategy_loss_profile(candidate, ledger, settings, now, info)
    strategy_recovery_profile = strategy_recovery_queue_profile(candidate, settings, info)
    strategy_recovery_bonus = float(strategy_recovery_profile.get("bonus_points") or 0.0)
    cash = float(ledger.get("cash_usd") or 0.0)
    equity = float(ledger.get("equity_usd") or ledger.get("initial_capital_usd") or 0.0)
    cash_ratio = (cash / equity * 100.0) if equity else 0.0
    idle_cash_bonus = 0.0
    if (
        settings.get("recent_loss_rotation_prefer_unfailed_symbols_when_cash_idle", True)
        and cash_ratio >= float(settings.get("recent_loss_rotation_idle_cash_ratio_pct", 70.0))
        and symbol_loss_profile.get("loss_count", 0) == 0
        and strategy_loss_profile.get("loss_count", 0) == 0
    ):
        idle_cash_bonus = float(settings.get("recent_loss_rotation_unfailed_symbol_bonus_points", 18.0))
    total_recent_loss_penalty = float(symbol_loss_profile.get("penalty_points") or 0.0) + float(strategy_loss_profile.get("penalty_points") or 0.0)
    score = (
        stage_score
        + info_score * 1.6
        + max(-100.0, min(pre_holdout_selection_score, 100.0)) * 0.5
        + validation_win * 0.2
        + validation_return * 0.2
        + max(oos_drawdown, -80.0) * 0.7
        + max(train_return, -50.0) * 0.2
        - max(0.0, holdout_concentration - 40.0) * 0.5
        + idle_cash_bonus
        + strategy_recovery_bonus
        - total_recent_loss_penalty
    )
    return {
        "score": round(score, 4),
        "stage_score": stage_score,
        "info_score": info_score,
        "oos_win_rate_pct": oos_win,
        "oos_net_return_pct": oos_return,
        "oos_final_capital": oos_final,
        "oos_max_drawdown_pct": oos_drawdown,
        "train_net_return_pct": train_return,
        "validation_win_rate_pct": validation_win,
        "validation_net_return_pct": validation_return,
        "pre_holdout_selection_score": pre_holdout_selection_score,
        "holdout_largest_winner_share_pct": holdout_concentration,
        "cash_ratio_pct": round(cash_ratio, 4),
        "idle_cash_rotation_bonus_points": idle_cash_bonus,
        "strategy_recovery_queue_bonus_points": round(strategy_recovery_bonus, 4),
        "strategy_recovery_queue_profile": strategy_recovery_profile,
        "recent_loss_penalty_points": round(total_recent_loss_penalty, 4),
        "recent_loss_profile": symbol_loss_profile,
        "recent_symbol_loss_profile": symbol_loss_profile,
        "recent_strategy_loss_profile": strategy_loss_profile,
    }


def rank_candidates_for_selection(candidates, ledger, info_by_symbol, settings, now):
    ranked = []
    for candidate in candidates:
        rank = candidate_selection_rank(candidate, ledger, info_by_symbol, settings, now)
        candidate["selection_rank"] = rank
        ranked.append(candidate)
    ranked.sort(
        key=lambda c: (
            c["selection_rank"]["score"],
            c["selection_rank"]["stage_score"],
            c["selection_rank"]["info_score"],
            c["selection_rank"]["oos_final_capital"],
            c["selection_rank"]["oos_win_rate_pct"],
        ),
        reverse=True,
    )
    return ranked


def open_position_cooldown_violation(pos, ledger, settings, now):
    symbol = pos.get("symbol")
    mode = pos.get("paper_entry_mode")
    if not symbol or not mode:
        return False, {}
    allowed, detail = post_loss_reentry_cooldown(symbol, mode, ledger, settings, now)
    if allowed:
        return False, detail
    try:
        opened_at = parse_iso(pos.get("opened_at"))
        closed_at = parse_iso(detail.get("closed_at"))
    except Exception:
        return False, detail
    if opened_at >= closed_at:
        return True, detail
    return False, detail


def choose_candidates(scan, ledger, market, cg_prices, settings, info_signals, now=None):
    open_symbols = {p["symbol"] for p in ledger.get("open_positions", [])}
    if len(ledger.get("open_positions", [])) >= int(settings["max_open_positions"]):
        return [], [{"reason": "max_open_positions_reached", "open_symbols": sorted(open_symbols)}]
    now = now or utc_now()
    rejections = []
    selected = []
    reserved_symbols = set(open_symbols)
    shadow_ledger = copy.deepcopy(ledger)
    max_new = min(
        int(settings.get("max_new_positions_per_run", 1)),
        max(0, int(settings["max_open_positions"]) - len(ledger.get("open_positions", []))),
    )
    if max_new <= 0:
        return [], [{"reason": "no_open_position_slots", "open_symbols": sorted(open_symbols)}]
    allowed_stages = {"target_research_pass_current_signal", "paper_forward_candidate_current_signal"}
    exploratory_stages = set(settings.get("exploratory_allowed_stages", []))
    validation_probe_stages = set(settings.get("validation_probe_allowed_stages", []))
    info_by_symbol = info_signals.get("by_symbol", {}) if info_signals else {}
    ranked = rank_candidates_for_selection(scan["top_candidates"], shadow_ledger, info_by_symbol, settings, now)
    for candidate in ranked:
        symbol = candidate["symbol"]
        info = info_by_symbol.get(symbol, {"info_pressure_score": 0, "tier": "missing", "reasons": []})
        strict_ok = candidate["stage"] in allowed_stages
        exploratory_ok = (
            bool(settings["exploratory_probe_enabled"])
            and candidate["stage"] in exploratory_stages
            and info.get("info_pressure_score", 0) >= float(settings["exploratory_min_info_score"])
            and candidate["oos_summary"]["trade_count"] >= int(settings["exploratory_min_oos_trades"])
            and candidate["oos_summary"]["max_drawdown_pct"] >= float(settings["exploratory_min_oos_drawdown_pct"])
        )
        validation_probe_ok = (
            bool(settings.get("validation_probe_enabled", False))
            and candidate["stage"] in validation_probe_stages
            and candidate["oos_summary"]["trade_count"] >= int(settings.get("validation_probe_min_oos_trades", 10))
            and candidate["oos_summary"]["win_rate_pct"] >= float(settings.get("validation_probe_min_oos_win_rate_pct", 50.0))
            and candidate["oos_summary"]["final_capital"] >= float(settings.get("validation_probe_min_oos_final_capital", settings.get("default_initial_capital_usd", 500.0)))
            and candidate["oos_summary"].get("net_return_pct", -999.0) >= float(settings.get("validation_probe_min_oos_net_return_pct", 0.0))
            and candidate["oos_summary"]["max_drawdown_pct"] >= float(settings.get("validation_probe_min_oos_drawdown_pct", -35.0))
            and candidate["train_summary"].get("final_capital", 0.0) >= float(settings.get("validation_probe_min_train_final_capital", 0.0))
        )
        if not strict_ok and not exploratory_ok and not validation_probe_ok:
            validation_probe_details = None
            if candidate["stage"] in validation_probe_stages and settings.get("validation_probe_enabled", False):
                validation_probe_details = {
                    "oos_trade_count": candidate["oos_summary"].get("trade_count"),
                    "min_oos_trades": settings.get("validation_probe_min_oos_trades", 10),
                    "oos_win_rate_pct": candidate["oos_summary"].get("win_rate_pct"),
                    "min_oos_win_rate_pct": settings.get("validation_probe_min_oos_win_rate_pct", 50.0),
                    "oos_final_capital": candidate["oos_summary"].get("final_capital"),
                    "min_oos_final_capital": settings.get("validation_probe_min_oos_final_capital", settings.get("default_initial_capital_usd", 500.0)),
                    "oos_net_return_pct": candidate["oos_summary"].get("net_return_pct"),
                    "min_oos_net_return_pct": settings.get("validation_probe_min_oos_net_return_pct", 0.0),
                    "oos_max_drawdown_pct": candidate["oos_summary"].get("max_drawdown_pct"),
                    "min_oos_max_drawdown_pct": settings.get("validation_probe_min_oos_drawdown_pct", -35.0),
                    "train_final_capital": candidate["train_summary"].get("final_capital"),
                    "min_train_final_capital": settings.get("validation_probe_min_train_final_capital", 0.0),
                }
            rejections.append(
                {
                    "symbol": symbol,
                    "stage": candidate["stage"],
                    "reason": "stage_not_paper_forward_or_info_probe",
                    "info_pressure_score": info.get("info_pressure_score", 0),
                    "info_tier": info.get("tier"),
                    "validation_probe_details": validation_probe_details,
                }
            )
            continue
        scale_in = False
        scale_details = {}
        if symbol in reserved_symbols:
            scale_in, scale_reason, scale_details = scale_in_eligibility(candidate, shadow_ledger, settings, info)
            if not scale_in:
                rejections.append({"symbol": symbol, "stage": candidate["stage"], "reason": "symbol_already_open", "scale_in_rejection": scale_reason, "details": scale_details})
                continue
        candidate["paper_entry_mode"] = (
            "winner_scale_in_probe"
            if scale_in
            else ("strict_strategy_gate" if strict_ok else ("validation_probe" if validation_probe_ok else "info_exploratory_probe"))
        )
        recovery_allowed, recovery_gate = validation_recovery_plan_gate(candidate, candidate["paper_entry_mode"], settings)
        candidate["validation_recovery_gate"] = recovery_gate
        if not recovery_allowed:
            rejections.append(
                {
                    "symbol": symbol,
                    "stage": candidate["stage"],
                    "reason": "validation_recovery_plan_block",
                    "paper_entry_mode": candidate["paper_entry_mode"],
                    "validation_recovery_gate": recovery_gate,
                }
            )
            continue
        cooldown_allowed, cooldown_gate = post_loss_reentry_cooldown(symbol, candidate["paper_entry_mode"], shadow_ledger, settings, now)
        if not cooldown_allowed:
            rejections.append(
                {
                    "symbol": symbol,
                    "stage": candidate["stage"],
                    "reason": "post_loss_reentry_cooldown",
                    "paper_entry_mode": candidate["paper_entry_mode"],
                    "cooldown_gate": cooldown_gate,
                }
            )
            continue
        quality_allowed, quality_gate = recent_loss_quality_gate(candidate, candidate["paper_entry_mode"], shadow_ledger, settings, info, now)
        if not quality_allowed:
            rejections.append(
                {
                    "symbol": symbol,
                    "stage": candidate["stage"],
                    "reason": "recent_loss_quality_gate_block",
                    "paper_entry_mode": candidate["paper_entry_mode"],
                    "recent_loss_quality_gate": quality_gate,
                }
            )
            continue
        candidate["recent_loss_quality_gate"] = quality_gate
        mode_allowed, mode_gate = entry_mode_learning_gate(candidate["paper_entry_mode"], shadow_ledger, settings)
        if not mode_allowed:
            rejections.append(
                {
                    "symbol": symbol,
                    "stage": candidate["stage"],
                    "reason": "entry_mode_learning_guard_block",
                    "paper_entry_mode": candidate["paper_entry_mode"],
                    "entry_mode_learning_gate": mode_gate,
                }
            )
            continue
        candidate["entry_mode_learning_gate"] = mode_gate
        overlay_allowed, overlay_gate = pso.combined_overlay_gate(
            entry_mode=candidate["paper_entry_mode"],
            strategy_family=f"{candidate['interval']} {candidate['strategy']['family']}",
            interval=candidate["interval"],
            overlay=settings.get("paper_strategy_overlay"),
        )
        candidate["paper_strategy_overlay_decision"] = overlay_gate
        if not overlay_allowed:
            rejections.append(
                {
                    "symbol": symbol,
                    "stage": candidate["stage"],
                    "reason": "paper_strategy_overlay_block",
                    "paper_entry_mode": candidate["paper_entry_mode"],
                    "paper_strategy_overlay_decision": overlay_gate,
                }
            )
            continue
        candidate["info_signal"] = info
        if validation_probe_ok:
            candidate["validation_probe_evidence"] = {
                "stage": candidate["stage"],
                "oos_trade_count": candidate["oos_summary"].get("trade_count"),
                "oos_win_rate_pct": candidate["oos_summary"].get("win_rate_pct"),
                "oos_final_capital": candidate["oos_summary"].get("final_capital"),
                "oos_max_drawdown_pct": candidate["oos_summary"].get("max_drawdown_pct"),
                "paper_only_sample_collection": True,
            }
        sizing = apply_scale_in_sizing_cap(candidate, shadow_ledger, settings, scale_details) if scale_in else candidate_sizing_decision(candidate, shadow_ledger, settings)
        candidate["sizing_decision"] = sizing
        candidate["planned_notional_usd"] = sizing["planned_notional_usd"]
        if scale_in:
            candidate["scale_in_evidence"] = scale_details
        if candidate["planned_notional_usd"] < float(settings.get("min_trade_notional_usd", 25.0)):
            reason = "insufficient_scale_in_capacity" if scale_in else "insufficient_dynamic_sizing_capacity"
            rejections.append({"symbol": symbol, "stage": candidate["stage"], "reason": reason, "details": sizing})
            continue
        quote = execution_quote(symbol, "buy", candidate_notional(candidate, settings), market, settings)
        mid = quote.get("mid")
        validation = cg_validation(symbol, mid, cg_prices, float(settings["max_cross_source_deviation_bps"])) if mid else {"status": "missing"}
        if not quote["allow"]:
            rejections.append({"symbol": symbol, "stage": candidate["stage"], "reason": "liquidity_block", "details": quote["reasons"]})
            continue
        if validation["status"] == "disputed":
            rejections.append({"symbol": symbol, "stage": candidate["stage"], "reason": "cross_source_disputed", "details": validation})
            continue
        candidate["candidate_key"] = candidate_key(candidate)
        candidate["data_validation"] = validation
        candidate["execution_quote"] = quote
        selected.append(candidate)
        if not scale_in:
            reserved_symbols.add(symbol)
        reserve_candidate_capacity(shadow_ledger, candidate)
        if len(selected) >= max_new:
            break
    return selected, rejections


def choose_candidate(scan, ledger, market, cg_prices, settings, info_signals, now=None):
    candidates, rejections = choose_candidates(scan, ledger, market, cg_prices, settings, info_signals, now=now)
    return (candidates[0] if candidates else None), rejections


def prefilter_scan_symbols(symbols, ledger, info_signals, settings):
    open_symbols = {p["symbol"] for p in ledger.get("open_positions", [])}
    rows = info_signals.get("rows", []) if info_signals else []
    min_score = float(settings.get("info_prefilter_min_score", 25))
    limit = int(settings.get("info_prefilter_max_symbols", 8))
    ranked = [row["symbol"] for row in rows if row.get("info_pressure_score", 0) >= min_score]
    queue_symbols = strategy_recovery_queue_symbols(
        settings.get("strategy_recovery_optimizer_state") or {},
        max_symbols=int(settings.get("strategy_recovery_queue_max_symbols", 8) or 8),
    )
    selected = []
    allowed = set(symbols)
    if settings.get("strategy_recovery_queue_allow_symbols_outside_default_universe", True):
        allowed.update(queue_symbols)
    for symbol in ["BTCUSDT", *open_symbols, *ranked, *queue_symbols, *symbols]:
        if symbol in allowed and symbol not in selected:
            selected.append(symbol)
        if len([s for s in selected if s not in open_symbols and s != "BTCUSDT"]) >= limit:
            break
    return selected


def trade_notional(candidate, settings):
    if not candidate:
        return 0.0
    if candidate.get("planned_notional_usd") is not None:
        return float(candidate["planned_notional_usd"])
    if candidate.get("paper_entry_mode") == "info_exploratory_probe":
        return float(settings["exploratory_notional_usd"])
    oos = candidate["oos_summary"]
    train = candidate["train_summary"]
    high_stage = candidate["stage"] == "target_research_pass_current_signal"
    robust = oos["win_rate_pct"] >= 60 and oos["max_drawdown_pct"] >= -20 and train["net_return_pct"] >= 20
    return float(settings["max_single_trade_notional_usd"] if high_stage and robust else settings["default_notional_usd"])


def update_position_marks(pos, mark_price, execution=None):
    qty = float(pos["quantity"])
    entry_price = float(pos["entry_price"])
    notional = float(pos["notional_usd"])
    gross_pnl = (mark_price - entry_price) * qty
    entry_commission = float(pos.get("entry_commission_usd", 0.0))
    commission_bps = float(pos.get("commission_bps", 0.0))
    if execution:
        net_proceeds = qty * float(execution["execution_price"]) - qty * float(execution["execution_price"]) * commission_bps / 10000.0
        net_pnl = net_proceeds - notional
    else:
        exit_commission = mark_price * qty * commission_bps / 10000.0
        net_pnl = gross_pnl - entry_commission - exit_commission
    pos["last_price"] = round(mark_price, 12)
    pos["unrealized_gross_pnl_usd"] = round(gross_pnl, 6)
    pos["unrealized_net_pnl_usd"] = round(net_pnl, 6)
    pos["unrealized_pnl_usd"] = round(net_pnl if pos.get("realistic_execution_enabled") else gross_pnl, 6)
    pos["unrealized_pnl_pct"] = round((pos["unrealized_pnl_usd"] / notional) * 100.0 if notional else 0.0, 4)
    pos["max_unrealized_pnl_usd"] = max(float(pos.get("max_unrealized_pnl_usd", pos["unrealized_pnl_usd"])), pos["unrealized_pnl_usd"])
    pos["min_unrealized_pnl_usd"] = min(float(pos.get("min_unrealized_pnl_usd", pos["unrealized_pnl_usd"])), pos["unrealized_pnl_usd"])
    inferred_max_pct = float(pos.get("max_unrealized_pnl_usd", pos["unrealized_pnl_usd"])) / notional * 100.0 if notional else pos["unrealized_pnl_pct"]
    inferred_min_pct = float(pos.get("min_unrealized_pnl_usd", pos["unrealized_pnl_usd"])) / notional * 100.0 if notional else pos["unrealized_pnl_pct"]
    pos["max_unrealized_pnl_pct"] = max(float(pos.get("max_unrealized_pnl_pct", inferred_max_pct)), pos["unrealized_pnl_pct"], inferred_max_pct)
    pos["min_unrealized_pnl_pct"] = min(float(pos.get("min_unrealized_pnl_pct", inferred_min_pct)), pos["unrealized_pnl_pct"], inferred_min_pct)


def holding_age_hours(pos, now):
    opened_at = pos.get("opened_at")
    if not opened_at:
        return 0.0
    return max(0.0, (now - parse_iso(opened_at)).total_seconds() / 3600.0)


def is_short_horizon_position(pos):
    family = str(pos.get("strategy_family") or "").lower()
    strategy = ((pos.get("strategy_candidate") or {}).get("strategy") or {})
    interval = str(strategy.get("interval") or "").lower()
    return family.startswith("15m") or interval == "15m"


def paper_overlay_profit_protection(pos, settings, current: dict):
    """Apply paper-only overlay exit A/B settings to dynamic profit protection."""

    overlay = settings.get("paper_strategy_overlay") if isinstance(settings.get("paper_strategy_overlay"), dict) else {}
    exit_rules = overlay.get("exit_rules") if isinstance(overlay.get("exit_rules"), dict) else {}
    rule = exit_rules.get("profit_protection") if isinstance(exit_rules.get("profit_protection"), dict) else {}
    status = str(rule.get("status") or rule.get("paper_status") or "")
    if status not in {"paper_applied", "paper_ab_testing", "paper_promoted_candidate"}:
        return current
    out = dict(current)
    try:
        arm_after = float(rule.get("arm_after_mfe_pct"))
        out["trigger"] = min(float(out["trigger"]), arm_after)
    except Exception:
        pass
    try:
        floor = float(rule.get("trailing_floor_pct"))
        out["break_even_floor"] = max(float(out["break_even_floor"]), floor)
    except Exception:
        pass
    try:
        max_giveback = rule.get("max_giveback_pct")
        if max_giveback is not None:
            out["giveback"] = min(float(out["giveback"]), float(max_giveback))
    except Exception:
        pass
    out["overlay_applied"] = True
    out["overlay_source_change_id"] = rule.get("source_change_id")
    out["overlay_status"] = status
    out["overlay_strategy_version"] = overlay.get("strategy_version")
    return out


def dynamic_exit_decision(pos, now, settings, info_signal=None):
    if not settings.get("dynamic_exit_enabled", True):
        return None, {}

    pnl_pct = float(pos.get("unrealized_pnl_pct", 0.0))
    max_pnl_pct = max(float(pos.get("max_unrealized_pnl_pct", pnl_pct)), pnl_pct)
    risk_state = dict(pos.get("risk_state") or {})
    age_hours = holding_age_hours(pos, now)
    short_horizon = is_short_horizon_position(pos)
    scale_evidence = pos.get("scale_in_evidence") or ((pos.get("strategy_candidate") or {}).get("scale_in_evidence") or {})
    is_scale_in = pos.get("paper_entry_mode") == "winner_scale_in_probe"
    target_sprint = bool(scale_evidence.get("target_sprint_scale_in"))

    if target_sprint:
        loss_cut = float(settings.get("target_sprint_loss_cut_pct", -5.5))
        if pnl_pct <= loss_cut:
            return "target_sprint_loss_cut", {
                "pnl_pct": round(pnl_pct, 4),
                "loss_cut_pct": loss_cut,
                "age_hours": round(age_hours, 2),
            }
    if is_scale_in:
        loss_cut = float(settings.get("scale_in_loss_cut_pct", -7.0))
        if pnl_pct <= loss_cut:
            return "scale_in_loss_cut", {
                "pnl_pct": round(pnl_pct, 4),
                "loss_cut_pct": loss_cut,
                "age_hours": round(age_hours, 2),
            }

    if (
        settings.get("exploratory_capital_protection_enabled", True)
        and pos.get("paper_entry_mode") == "info_exploratory_probe"
    ):
        exploratory_min_hold_minutes = float(settings.get("exploratory_capital_protection_min_hold_minutes", 30.0))
        exploratory_loss_cut_pct = float(settings.get("exploratory_capital_protection_loss_cut_pct", -3.25))
        exploratory_max_favorable_pct = float(settings.get("exploratory_capital_protection_max_favorable_pnl_pct", 1.0))
        if (
            age_hours * 60.0 >= exploratory_min_hold_minutes
            and pnl_pct <= exploratory_loss_cut_pct
            and max_pnl_pct <= exploratory_max_favorable_pct
        ):
            return "exploratory_capital_protection_loss_cut", {
                "pnl_pct": round(pnl_pct, 4),
                "max_pnl_pct": round(max_pnl_pct, 4),
                "loss_cut_pct": exploratory_loss_cut_pct,
                "max_favorable_pnl_pct": exploratory_max_favorable_pct,
                "age_hours": round(age_hours, 2),
            }

    exploratory_profit_guard = (
        settings.get("exploratory_profit_protection_enabled", True)
        and pos.get("paper_entry_mode") == "info_exploratory_probe"
        and not short_horizon
    )
    if exploratory_profit_guard:
        trigger = float(settings.get("exploratory_profit_protection_trigger_pct", 3.0))
        giveback = float(settings.get("exploratory_profit_trailing_giveback_pct", 1.25))
        break_even_floor = float(settings.get("exploratory_break_even_floor_pct", 0.5))
        min_hold_minutes = float(settings.get("exploratory_profit_protection_min_hold_minutes", 15.0))
    else:
        trigger = float(settings.get("short_horizon_profit_protection_trigger_pct" if short_horizon else "profit_protection_trigger_pct", 3.5 if short_horizon else 6.0))
        giveback = float(settings.get("short_horizon_profit_trailing_giveback_pct" if short_horizon else "profit_trailing_giveback_pct", 1.5 if short_horizon else 3.5))
        break_even_floor = float(settings.get("short_horizon_break_even_floor_pct" if short_horizon else "profit_break_even_floor_pct", 0.4 if short_horizon else 0.6))
        min_hold_minutes = float(settings.get("short_horizon_profit_protection_min_hold_minutes" if short_horizon else "profit_protection_min_hold_minutes", 15.0 if short_horizon else 30.0))
    overlay_exit = paper_overlay_profit_protection(
        pos,
        settings,
        {
            "trigger": trigger,
            "giveback": giveback,
            "break_even_floor": break_even_floor,
            "min_hold_minutes": min_hold_minutes,
        },
    )
    trigger = float(overlay_exit["trigger"])
    giveback = float(overlay_exit["giveback"])
    break_even_floor = float(overlay_exit["break_even_floor"])
    min_hold_minutes = float(overlay_exit["min_hold_minutes"])

    if max_pnl_pct >= trigger and age_hours * 60.0 >= min_hold_minutes:
        trailing_floor = max(break_even_floor, max_pnl_pct - giveback)
        risk_state.update(
            {
                "profit_protection_armed": True,
                "short_horizon_profit_protection": short_horizon,
                "exploratory_profit_protection": exploratory_profit_guard,
                "highest_unrealized_pnl_pct": round(max_pnl_pct, 4),
                "trailing_floor_pct": round(trailing_floor, 4),
                "profit_protection_trigger_pct": trigger,
                "profit_trailing_giveback_pct": giveback,
                "paper_strategy_overlay_exit_applied": bool(overlay_exit.get("overlay_applied")),
                "paper_strategy_overlay_exit_source_change_id": overlay_exit.get("overlay_source_change_id"),
                "paper_strategy_overlay_exit_status": overlay_exit.get("overlay_status"),
                "paper_strategy_overlay_version": overlay_exit.get("overlay_strategy_version"),
            }
        )
        pos["risk_state"] = risk_state
        if pnl_pct <= trailing_floor:
            return "trailing_profit_protection", {
                "pnl_pct": round(pnl_pct, 4),
                "max_pnl_pct": round(max_pnl_pct, 4),
                "trailing_floor_pct": round(trailing_floor, 4),
                "paper_strategy_overlay_exit_applied": bool(overlay_exit.get("overlay_applied")),
                "paper_strategy_overlay_exit_source_change_id": overlay_exit.get("overlay_source_change_id"),
                "paper_strategy_overlay_exit_status": overlay_exit.get("overlay_status"),
                "paper_strategy_overlay_version": overlay_exit.get("overlay_strategy_version"),
            }

    info_score = float((info_signal or {}).get("info_pressure_score", risk_state.get("last_info_pressure_score", 0.0)) or 0.0)
    risk_state["last_info_pressure_score"] = info_score
    pos["risk_state"] = risk_state
    decay_hours_key = "short_horizon_time_decay_exit_hours" if short_horizon else "time_decay_exit_hours"
    decay_max_pnl_key = "short_horizon_time_decay_exit_max_pnl_pct" if short_horizon else "time_decay_exit_max_pnl_pct"
    decay_min_info_key = "short_horizon_time_decay_min_info_score" if short_horizon else "time_decay_min_info_score"
    if (
        settings.get("time_decay_exit_enabled", True)
        and pos.get("paper_entry_mode") == "info_exploratory_probe"
        and age_hours >= float(settings.get(decay_hours_key, 24.0 if short_horizon else 96.0))
        and pnl_pct <= float(settings.get(decay_max_pnl_key, 1.0 if short_horizon else 0.0))
        and info_score < float(settings.get(decay_min_info_key, 30.0 if short_horizon else 25.0))
    ):
        return "short_horizon_time_decay_info_probe" if short_horizon else "time_decay_info_probe", {
            "age_hours": round(age_hours, 2),
            "pnl_pct": round(pnl_pct, 4),
            "info_pressure_score": round(info_score, 2),
            "short_horizon": short_horizon,
        }

    return None, {}


def record_paper_order(ledger, now, side, symbol, quote, quantity, notional_usd, paper_trade_id, run_id, reason, strategy_version=None):
    orders = ledger.setdefault("paper_orders", [])
    order_id = f"pord-{local_dt(now).strftime('%Y%m%d-%H%M%S')}-{side.lower()}-{symbol}-{len(orders) + 1:04d}"
    order = {
        "paper_order_id": order_id,
        "paper_trade_id": paper_trade_id,
        "run_id": run_id,
        "created_at": iso(now),
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "time_in_force": "IOC",
        "status": "FILLED",
        "simulated_status": "PAPER_FILLED",
        "simulated_api": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "requested_notional_usd": round(float(notional_usd), 6),
        "executed_quantity": round(float(quantity), 12),
        "average_fill_price": quote.get("execution_price"),
        "commission_usd": quote.get("commission_usd"),
        "commission_bps": quote.get("commission_bps"),
        "spread_bps": quote.get("spread_bps"),
        "slippage_bps": quote.get("total_slippage_bps"),
        "depth_1pct_usd": quote.get("depth_1pct_usd"),
        "quote_status": quote.get("status"),
        "quote_reasons": quote.get("reasons", []),
        "order_reason": reason,
        "strategy_version": strategy_version,
        "notes": "simulated exchange API order only; no private or live endpoint called",
    }
    orders.append(order)
    return order


def backfill_legacy_entry_orders(ledger, now):
    orders = ledger.setdefault("paper_orders", [])
    existing_buy_by_trade = {
        order.get("paper_trade_id"): order for order in orders if order.get("side") == "BUY" and order.get("paper_trade_id")
    }
    changed = False
    for pos in ledger.get("open_positions", []):
        trade_id = pos.get("paper_trade_id")
        if not trade_id:
            continue
        order = existing_buy_by_trade.get(trade_id)
        if order is None:
            order_id = f"pord-backfill-{trade_id}"
            order = {
                "paper_order_id": order_id,
                "paper_trade_id": trade_id,
                "run_id": pos.get("run_id", "legacy-backfill"),
                "created_at": pos.get("opened_at", iso(now)),
                "symbol": pos.get("symbol"),
                "side": "BUY",
                "type": "MARKET",
                "time_in_force": "IOC",
                "status": "BACKFILLED_FILLED",
                "simulated_api": True,
                "live_orders_enabled": False,
                "private_api_used": False,
                "requested_notional_usd": pos.get("notional_usd"),
                "executed_quantity": pos.get("quantity"),
                "average_fill_price": pos.get("entry_price"),
                "commission_usd": pos.get("entry_commission_usd"),
                "commission_bps": pos.get("commission_bps"),
                "spread_bps": pos.get("entry_spread_bps"),
                "slippage_bps": pos.get("entry_slippage_bps"),
                "depth_1pct_usd": pos.get("entry_depth_1pct_usd"),
                "quote_status": "legacy_backfill",
                "quote_reasons": [],
                "order_reason": "legacy_position_order_backfill",
                "strategy_version": pos.get("strategy_version"),
                "notes": "backfilled simulated API order for an existing paper position; no live endpoint called",
            }
            orders.append(order)
            existing_buy_by_trade[trade_id] = order
            changed = True
        if not pos.get("entry_order_id"):
            pos["entry_order_id"] = order["paper_order_id"]
            changed = True
        lifecycle = dict(pos.get("simulated_api_order_lifecycle") or {})
        if not lifecycle:
            pos["simulated_api_order_lifecycle"] = {
                "entry_order_id": order["paper_order_id"],
                "entry_status": order.get("status"),
                "exit_order_id": None,
                "exit_status": None,
                "live_orders_enabled": False,
            }
            changed = True
    return changed


def review_positions(
    ledger,
    market,
    settings,
    now,
    info_signals=None,
    run_id=None,
    historical_barrier_replays=None,
):
    still_open = []
    reviewed = []
    info_by_symbol = (info_signals or {}).get("by_symbol", {})
    historical_barrier_replays = historical_barrier_replays or {}
    for pos in ledger.get("open_positions", []):
        symbol = pos["symbol"]
        replay = historical_barrier_replays.get(pos.get("paper_trade_id"))
        book = market.get(symbol, {}).get("book", {})
        if not replay and now >= parse_iso(pos["expires_at"]) and pos.get("barrier_replay_status") == "failed":
            reviewed.append(
                {
                    "paper_trade_id": pos["paper_trade_id"],
                    "symbol": symbol,
                    "status": "degraded",
                    "reason": "historical_replay_unavailable_for_expired_position",
                }
            )
            still_open.append(pos)
            continue
        if not book and not replay:
            reviewed.append({"paper_trade_id": pos["paper_trade_id"], "symbol": symbol, "status": "degraded", "reason": "missing_market"})
            still_open.append(pos)
            continue
        if replay:
            mark_price = float(replay["trigger_mark_price"])
            sell_quote = dict(replay["execution_quote"])
            for extreme_key in ("period_high_until_trigger", "period_low_until_trigger"):
                if replay.get(extreme_key) is not None:
                    update_position_marks(pos, float(replay[extreme_key]))
        else:
            mark_price = (float(book["bidPrice"]) + float(book["askPrice"])) / 2.0
            sell_quote = execution_quote(symbol, "sell", mark_price * float(pos["quantity"]), market, settings)
        update_position_marks(pos, mark_price, execution=sell_quote if sell_quote.get("allow") else None)
        exit_reason = replay.get("exit_reason") if replay else None
        exit_detail = {"historical_barrier_replay": replay} if replay else {}
        execution_time = parse_iso(replay["triggered_at"]) if replay else now
        if exit_reason is None:
            if mark_price <= float(pos["stop_price"]):
                exit_reason = "stop"
            elif mark_price >= float(pos["take_profit_price"]):
                exit_reason = "take_profit"
            elif now >= parse_iso(pos["expires_at"]):
                exit_reason = "time_expired"
        if exit_reason is None:
            cooldown_violation, cooldown_detail = open_position_cooldown_violation(pos, ledger, settings, now)
            if cooldown_violation:
                exit_reason = "post_loss_reentry_cooldown_violation"
                exit_detail = cooldown_detail
        if exit_reason is None:
            exit_reason, exit_detail = dynamic_exit_decision(pos, now, settings, info_by_symbol.get(symbol))
        if exit_reason is None:
            reviewed.append(
                {
                    "paper_trade_id": pos["paper_trade_id"],
                    "symbol": symbol,
                    "status": "open",
                    "last_price": round(mark_price, 12),
                    "unrealized_net_pnl_usd": pos.get("unrealized_net_pnl_usd"),
                    "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
                }
            )
            still_open.append(pos)
            continue
        exit_price = float(sell_quote.get("execution_price", mark_price))
        gross_proceeds = float(pos["quantity"]) * exit_price
        exit_commission = gross_proceeds * float(settings["commission_bps"]) / 10000.0
        net_proceeds = gross_proceeds - exit_commission
        pnl_usd = net_proceeds - float(pos["notional_usd"])
        pnl_pct = pnl_usd / float(pos["notional_usd"]) * 100.0 if pos["notional_usd"] else 0.0
        exit_order = record_paper_order(
            ledger,
            execution_time,
            "SELL",
            symbol,
            sell_quote,
            float(pos["quantity"]),
            gross_proceeds,
            pos["paper_trade_id"],
            run_id or pos.get("run_id", "manual-review"),
            exit_reason,
            pos.get("strategy_version"),
        )
        closed = dict(pos)
        lifecycle = dict(closed.get("simulated_api_order_lifecycle") or {})
        if lifecycle:
            lifecycle["exit_order_id"] = exit_order["paper_order_id"]
            lifecycle["exit_status"] = "FILLED"
        closed.update(
            {
                "status": "closed",
                "outcome": "hit" if pnl_usd > 0 else "failed",
                "closed_at": iso(execution_time),
                "reconciled_at": iso(now) if replay else None,
                "exit_price": round(exit_price, 12),
                "exit_mark_price": round(mark_price, 12),
                "exit_reason": exit_reason,
                "exit_detail": exit_detail,
                "historical_barrier_replay": replay,
                "exit_commission_usd": round(exit_commission, 6),
                "exit_execution_quote": sell_quote,
                "exit_order_id": exit_order["paper_order_id"],
                "simulated_api_order_lifecycle": lifecycle or {
                    "entry_order_id": closed.get("entry_order_id"),
                    "entry_status": "FILLED" if closed.get("entry_order_id") else None,
                    "exit_order_id": exit_order["paper_order_id"],
                    "exit_status": "FILLED",
                    "live_orders_enabled": False,
                },
                "realized_gross_proceeds_usd": round(gross_proceeds, 6),
                "realized_net_proceeds_usd": round(net_proceeds, 6),
                "realized_pnl_usd": round(pnl_usd, 6),
                "realized_pnl_pct": round(pnl_pct, 4),
            }
        )
        ledger["cash_usd"] = round(float(ledger["cash_usd"]) + net_proceeds, 6)
        ledger.setdefault("closed_trades", []).append(closed)
        ledger.setdefault("events", []).append(
            {
                "event_type": "paper_close",
                "created_at": iso(execution_time),
                "reconciled_at": iso(now) if replay else None,
                "paper_trade_id": pos["paper_trade_id"],
                "symbol": symbol,
                "exit_reason": exit_reason,
                "exit_detail": exit_detail,
                "paper_order_id": exit_order["paper_order_id"],
                "exit_price": round(exit_price, 12),
                "realized_pnl_usd": round(pnl_usd, 6),
                "realized_pnl_pct": round(pnl_pct, 4),
                "realistic_execution_enabled": True,
                "historical_barrier_replay": bool(replay),
            }
        )
        reviewed.append(
            {
                "paper_trade_id": pos["paper_trade_id"],
                "symbol": symbol,
                "status": "closed",
                "exit_reason": exit_reason,
                "exit_detail": exit_detail,
                "exit_price": round(exit_price, 12),
                "closed_at": iso(execution_time),
                "reconciled_at": iso(now) if replay else None,
                "realized_pnl_usd": round(pnl_usd, 6),
            }
        )
    ledger["open_positions"] = still_open
    mark_ledger(ledger)
    return reviewed


def mark_ledger(ledger):
    open_value = 0.0
    for pos in ledger.get("open_positions", []):
        open_value += float(pos.get("last_price", pos["entry_price"])) * float(pos["quantity"])
    ledger["open_value_usd"] = round(open_value, 6)
    ledger["equity_usd"] = round(float(ledger.get("cash_usd", 0.0)) + open_value, 6)
    initial = float(ledger.get("initial_capital_usd", 500.0))
    ledger["net_return_pct"] = round((ledger["equity_usd"] / initial - 1.0) * 100.0, 4) if initial else 0.0
    ledger["max_drawdown_pct"] = max_drawdown_from_events(ledger)


def paper_holding_window(candidate, settings):
    interval = str(candidate.get("interval") or "").lower()
    if interval == "15m":
        hours = float(settings.get("short_horizon_max_holding_hours", 36))
        return timedelta(hours=hours), f"{int(hours)}h"
    if interval == "1h":
        hours = float(settings.get("one_hour_max_holding_hours", 96))
        return timedelta(hours=hours), f"{int(hours)}h"
    if interval == "4h":
        hours = float(settings.get("four_hour_max_holding_hours", 168))
        return timedelta(hours=hours), f"{int(hours)}h"
    text = str(settings.get("default_max_holding_window", "7d")).strip().lower()
    try:
        if text.endswith("h"):
            hours = float(text[:-1])
            return timedelta(hours=hours), f"{int(hours)}h"
        if text.endswith("d"):
            days = float(text[:-1])
            return timedelta(days=days), f"{int(days)}d"
    except ValueError:
        pass
    return timedelta(days=7), "7d"


def market_context_at_entry(candidate, settings):
    pool = settings.get("dynamic_scan_pool_profile") or {}
    return {
        "market_regime": pool.get("market_regime") or candidate.get("market_regime") or "unknown_or_not_attached",
        "market_atmosphere": pool.get("market_atmosphere") or candidate.get("market_atmosphere") or "unknown_or_not_attached",
        "short_term_state": pool.get("short_term_state") or candidate.get("short_term_state") or "unknown_or_not_attached",
        "sentiment_state": pool.get("sentiment_state") or candidate.get("sentiment_state") or "unknown_or_not_attached",
        "pool_width_policy": pool.get("pool_width_policy"),
        "pool_shape_policy": pool.get("pool_shape_policy"),
        "selected_symbol_count": pool.get("selected_symbol_count") or len(pool.get("selected_symbols") or []),
        "selected_symbols_sample": (pool.get("selected_symbols") or [])[:12],
        "source": "dynamic_scan_pool_profile",
    }


UNKNOWN_MARKET_CONTEXT_VALUES = {
    "",
    "unknown",
    "unknown_or_not_attached",
    "manual_or_cli_open_unknown",
    "unavailable",
    "none",
    "null",
}


def market_context_missing_fields(context):
    missing = []
    for field in ("market_regime", "market_atmosphere", "short_term_state", "sentiment_state"):
        value = str((context or {}).get(field) or "").strip().lower()
        if value in UNKNOWN_MARKET_CONTEXT_VALUES:
            missing.append(field)
    return missing


def open_paper_position(ledger, candidate, settings, now, run_id, strategy_version):
    market_context = market_context_at_entry(candidate, settings)
    missing_context = market_context_missing_fields(market_context)
    if missing_context:
        return None, f"market_context_incomplete:{','.join(missing_context)}"
    notional = trade_notional(candidate, settings)
    notional = min(notional, float(ledger.get("cash_usd", 0.0)))
    if notional <= 0:
        return None, "insufficient_cash"
    quote = execution_quote(candidate["symbol"], "buy", notional, candidate["market"], settings)
    if not quote["allow"]:
        return None, "execution_quote_blocked"
    qty = float(quote["base_quantity"])
    entry_price = float(quote["execution_price"])
    stop_pct = float(candidate["strategy"].get("stop_pct", settings["default_stop_pct"]))
    take_pct = float(candidate["strategy"].get("take_pct", settings["default_take_profit_pct"]))
    holding_delta, max_holding = paper_holding_window(candidate, settings)
    expires_at = now + holding_delta
    seq = len(ledger.get("open_positions", [])) + len(ledger.get("closed_trades", [])) + len(ledger.get("paper_orders", [])) + 1
    trade_id = f"paper-{local_dt(now).strftime('%Y%m%d-%H%M%S')}-{seq:04d}-{candidate['symbol'].replace('USDT', '')}-{candidate['candidate_key'][:4]}"
    position = {
        "paper_trade_id": trade_id,
        "symbol": candidate["symbol"],
        "side": "long_spot_paper",
        "strategy_family": f"{candidate['interval']} {candidate['strategy']['family']}",
        "strategy_version": strategy_version,
        "paper_entry_mode": candidate.get("paper_entry_mode", "strict_strategy_gate"),
        "run_id": run_id,
        "signal_source": f"active-alpha-paper-monitor/experiments/{run_id}.json",
        "market_regime": market_context.get("market_regime"),
        "market_atmosphere": market_context.get("market_atmosphere"),
        "short_term_state": market_context.get("short_term_state"),
        "sentiment_state": market_context.get("sentiment_state"),
        "market_context_at_entry": market_context,
        "opened_at": iso(now),
        "expires_at": iso(expires_at),
        "entry_price": round(entry_price, 12),
        "entry_mark_price": candidate["execution_quote"].get("mid"),
        "quantity": round(qty, 12),
        "notional_usd": round(notional, 6),
        "gross_quote_usd": quote["gross_quote_usd"],
        "entry_commission_usd": quote["commission_usd"],
        "commission_bps": quote["commission_bps"],
        "entry_slippage_bps": quote["total_slippage_bps"],
        "entry_spread_bps": quote["spread_bps"],
        "entry_depth_1pct_usd": quote["depth_1pct_usd"],
        "stop_pct": stop_pct,
        "take_profit_pct": take_pct,
        "stop_price": round(entry_price * (1.0 + stop_pct / 100.0), 12),
        "take_profit_price": round(entry_price * (1.0 + take_pct / 100.0), 12),
        "max_holding_window": max_holding,
        "status": "open",
        "outcome": "pending",
        "realistic_execution_enabled": True,
        "live_orders_enabled": False,
        "entry_mode_learning_gate": candidate.get("entry_mode_learning_gate"),
        "validation_recovery_gate": candidate.get("validation_recovery_gate"),
        "paper_strategy_overlay_decision": candidate.get("paper_strategy_overlay_decision"),
        "paper_testnet_risk_control": settings.get("paper_risk_control_state"),
        "strategy_candidate": {
            "stage": candidate["stage"],
            "interval": candidate["interval"],
            "strategy": candidate["strategy"],
            "train_summary": candidate["train_summary"],
            "oos_summary": candidate["oos_summary"],
            "info_signal": candidate.get("info_signal"),
            "data_validation": candidate.get("data_validation"),
            "sizing_decision": candidate.get("sizing_decision"),
            "scale_in_evidence": candidate.get("scale_in_evidence"),
            "entry_mode_learning_gate": candidate.get("entry_mode_learning_gate"),
            "validation_recovery_gate": candidate.get("validation_recovery_gate"),
            "paper_strategy_overlay_decision": candidate.get("paper_strategy_overlay_decision"),
            "paper_testnet_risk_control": settings.get("paper_risk_control_state"),
            "selection_rank": candidate.get("selection_rank"),
            "market_context_at_entry": market_context,
        },
        "scale_in_evidence": candidate.get("scale_in_evidence"),
        "sizing_decision": candidate.get("sizing_decision"),
        "execution_model": {
            "type": "realistic_spot_paper",
            "uses_bid_ask_spread": True,
            "uses_depth_1pct": True,
            "uses_commission": True,
            "uses_slippage": True,
        },
    }
    pso.apply_overlay_to_position(position, settings.get("paper_strategy_overlay"), candidate.get("paper_strategy_overlay_decision"))
    update_position_marks(position, float(quote["mid"]))
    is_scale_in = candidate.get("paper_entry_mode") == "winner_scale_in_probe"
    entry_order = record_paper_order(
        ledger,
        now,
        "BUY",
        candidate["symbol"],
        quote,
        qty,
        notional,
        trade_id,
        run_id,
        "paper_scale_in_signal" if is_scale_in else "paper_entry_signal",
        strategy_version,
    )
    position["entry_order_id"] = entry_order["paper_order_id"]
    position["simulated_api_order_lifecycle"] = {
        "entry_order_id": entry_order["paper_order_id"],
        "entry_status": "FILLED",
        "exit_order_id": None,
        "exit_status": None,
        "live_orders_enabled": False,
    }
    ledger["cash_usd"] = round(float(ledger["cash_usd"]) - notional, 6)
    ledger.setdefault("open_positions", []).append(position)
    ledger.setdefault("events", []).append(
        {
            "event_type": "paper_scale_in" if is_scale_in else "paper_open",
            "created_at": iso(now),
            "paper_trade_id": trade_id,
            "symbol": candidate["symbol"],
            "entry_price": position["entry_price"],
            "notional_usd": position["notional_usd"],
            "strategy_version": strategy_version,
            "realistic_execution_enabled": True,
            "notes": "virtual realistic paper scale-in order only; no live order placed" if is_scale_in else "virtual realistic paper order only; no live order placed",
        }
    )
    mark_ledger(ledger)
    return position, None


def load_strategy_state(path):
    return read_json(path, {"state_version": "sunday-crypto-paper-v1", "current_strategy_version": None, "revisions": []})


def evolve_strategy(state, candidate, scan, reviewed, run_id, now, strategy_prefix="sunday"):
    previous = state.get("current_strategy_version")
    if candidate:
        strategy_version = f"{strategy_prefix}-{local_dt(now).strftime('%Y%m%d-%H%M')}-{candidate['symbol']}-{candidate['interval']}-{candidate['candidate_key']}"
        action = "switch_or_confirm_strategy"
        reason = f"{candidate['stage']} current signal selected"
        state["current_strategy_version"] = strategy_version
    else:
        strategy_version = previous or f"{strategy_prefix}-{local_dt(now).strftime('%Y%m%d-%H%M')}-watch"
        action = "observe_no_new_strategy"
        reason = "no candidate passed current signal, liquidity and duplicate gates"
    revision = {
        "run_id": run_id,
        "created_at": iso(now),
        "previous_strategy_version": previous,
        "new_strategy_version": strategy_version,
        "action": action,
        "reason": reason,
        "reviewed_count": len(reviewed),
        "current_signal_strategies": scan.get("current_signal_strategies", 0),
        "top_candidate": {
            "symbol": candidate["symbol"],
            "stage": candidate["stage"],
            "interval": candidate["interval"],
            "strategy": candidate["strategy"],
            "train_summary": candidate["train_summary"],
            "oos_summary": candidate["oos_summary"],
        }
        if candidate
        else None,
    }
    state.setdefault("revisions", []).append(revision)
    state["updated_at"] = iso(now)
    return strategy_version, revision


def session_summary(ledger, now):
    local_date = local_dt(now).date().isoformat()
    trades = []
    for item in ledger.get("closed_trades", []):
        opened = parse_iso(item.get("opened_at", item.get("closed_at", iso(now)))).astimezone(TZ).date().isoformat()
        closed = parse_iso(item.get("closed_at", iso(now))).astimezone(TZ).date().isoformat()
        if opened == local_date or closed == local_date:
            trades.append(item)
    wins = [t for t in trades if float(t.get("realized_pnl_usd", 0.0)) > 0]
    losses = [t for t in trades if float(t.get("realized_pnl_usd", 0.0)) <= 0]
    pnl = sum(float(t.get("realized_pnl_usd", 0.0)) for t in trades)
    best = max(trades, key=lambda t: float(t.get("realized_pnl_usd", 0.0)), default=None)
    worst = min(trades, key=lambda t: float(t.get("realized_pnl_usd", 0.0)), default=None)
    return {
        "local_date": local_date,
        "closed_trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2) if trades else 0.0,
        "realized_pnl_usd": round(pnl, 6),
        "best_trade_id": best.get("paper_trade_id") if best else None,
        "worst_trade_id": worst.get("paper_trade_id") if worst else None,
        "open_position_count": len(ledger.get("open_positions", [])),
        "equity_usd": ledger.get("equity_usd"),
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
    }


def compact_run_output(run):
    opened_positions = run.get("opened_positions") or ([run["opened_position"]] if run.get("opened_position") else [])
    research_panel = run.get("research_panel") or {}
    external_validation = (
        research_panel.get("external_agent_validation")
        or run.get("external_agent_validation")
        or {}
    )
    research_method = (
        research_panel.get("research_method")
        or run.get("research_method")
        or "missing"
    )
    return {
        "run_id": run.get("run_id"),
        "loop_kind": run.get("loop_kind"),
        "live_orders_enabled": False,
        "data_status": run.get("data_status"),
        "optional_data_warning_count": len(run.get("optional_data_warnings") or []),
        "research_panel_missing": run.get("research_panel_missing"),
        "research_committee_degraded": run.get("research_committee_degraded"),
        "research_panel_missing_reason": run.get("research_panel_missing_reason"),
        "max_allowed_action": run.get("max_allowed_action"),
        "research_method": research_method,
        "paper_auto_learning_loop": run.get("paper_auto_learning_loop"),
        "external_roles_used": research_panel.get("external_roles_used") or [],
        "external_agent_validation": {
            "valid": external_validation.get("valid"),
            "unique_known_role_count": external_validation.get("unique_known_role_count"),
            "successful_known_role_count": external_validation.get("successful_known_role_count"),
            "degraded_role_count": external_validation.get("degraded_role_count"),
            "missing_required_external_roles": external_validation.get("missing_required_external_roles"),
        },
        "scan_summary": scan_summary(run.get("scan") or {}),
        "strategy_recovery_optimizer": {
            "status": (run.get("strategy_recovery_optimizer") or {}).get("status"),
            "path": (run.get("strategy_recovery_optimizer") or {}).get("path"),
            "eligible_retest_count": (run.get("strategy_recovery_optimizer") or {}).get("eligible_retest_count"),
            "queued_symbols": strategy_recovery_queue_symbols(run.get("strategy_recovery_optimizer") or {}, 8),
        },
        "validation_capacity_gate": compact_validation_capacity_gate(run.get("validation_capacity_gate") or {}),
        "paper_testnet_risk_control": run.get("paper_testnet_risk_control"),
        "validation_recovery_plan_summary": (run.get("validation_capacity_gate") or {}).get("validation_recovery_plan_summary"),
        "kline_cache_audit": kline_cache_audit_summary(run.get("kline_cache_audit") or {}),
        "market_fetch_budget": run.get("market_fetch_budget"),
        "kline_prefetch_budget": run.get("kline_prefetch_budget"),
        "info_fetch_budget": run.get("info_fetch_budget"),
        "reviewed_positions": run.get("reviewed_positions"),
        "new_paper_trades": [item.get("paper_trade_id") for item in opened_positions],
        "new_paper_symbols": [item.get("symbol") for item in opened_positions],
        "new_paper_trade": opened_positions[0].get("paper_trade_id") if opened_positions else None,
        "new_paper_symbol": opened_positions[0].get("symbol") if opened_positions else None,
        "paper_orders_total": len(run.get("ledger", {}).get("paper_orders", [])),
        "paper_portfolio": {
            "cash_usd": run.get("ledger", {}).get("cash_usd"),
            "open_value_usd": run.get("ledger", {}).get("open_value_usd"),
            "equity_usd": run.get("ledger", {}).get("equity_usd"),
            "net_return_pct": run.get("ledger", {}).get("net_return_pct"),
            "max_drawdown_pct": run.get("ledger", {}).get("max_drawdown_pct"),
        },
        "monthly_double_progress": run.get("monthly_double_progress"),
        "candidate_attribution_summary": candidate_attribution_summary(run),
        "rejection_summary": summarize_rejections(run.get("rejections") or []),
        "open_symbols": [p.get("symbol") for p in run.get("ledger", {}).get("open_positions", [])],
        "outputs": run.get("outputs", {}),
}


def summarize_rejections(rejections):
    counts = {}
    recovery_blocks = []
    for item in rejections:
        reason = item.get("reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
        if reason == "validation_recovery_plan_block" and len(recovery_blocks) < 8:
            gate = item.get("validation_recovery_gate") or {}
            recovery_blocks.append(
                {
                    "symbol": item.get("symbol"),
                    "stage": item.get("stage"),
                    "paper_entry_mode": item.get("paper_entry_mode"),
                    "matched_group": gate.get("matched_group"),
                    "matched_name": gate.get("matched_name"),
                    "matched_status": gate.get("matched_status"),
                }
            )
    return {
        "reason_counts": counts,
        "validation_recovery_plan_blocks": recovery_blocks,
    }


def compact_validation_capacity_gate(gate):
    if not isinstance(gate, dict):
        return {}
    return {
        key: value
        for key, value in gate.items()
        if key not in {"validation_recovery_plan"}
    }


def kline_cache_audit_summary(audit):
    return {
        "enabled": audit.get("enabled"),
        "status": audit.get("status"),
        "cache_dir": audit.get("cache_dir"),
        "checked_pairs": audit.get("checked_pairs"),
        "verified_count": audit.get("verified_count"),
        "stale_count": audit.get("stale_count"),
        "missing_count": audit.get("missing_count"),
        "open_bar_count": audit.get("open_bar_count"),
        "max_lag_minutes": audit.get("max_lag_minutes"),
        "latest_close_at": audit.get("latest_close_at"),
        "problem_rows": (audit.get("problem_rows") or [])[:8],
    }


def scan_summary(scan):
    return {
        "frames_loaded": scan.get("frames_loaded"),
        "strategies_scanned": scan.get("strategies_scanned"),
        "current_signal_strategies": scan.get("current_signal_strategies"),
        "candidate_count": scan.get("candidate_count"),
        "deduplicated_candidate_count": scan.get("deduplicated_candidate_count"),
        "performance_duplicate_count": (scan.get("dedup_summary") or {}).get("performance_duplicate_count"),
        "stage_counts": scan.get("stage_counts") or (scan.get("candidate_summary") or {}).get("stage_counts"),
        "deduplicated_stage_counts": (scan.get("deduplicated_candidate_summary") or {}).get("stage_counts"),
        "scan_budget": scan.get("scan_budget"),
        "top_candidate_count": len(scan.get("top_candidates") or []),
    }


def candidate_attribution_summary(run, limit=8):
    candidates = []
    seen = set()
    for candidate in (run.get("selected_candidates") or []):
        key = candidate_key(candidate) if candidate.get("strategy") else json.dumps(
            [candidate.get("symbol"), candidate.get("interval"), candidate.get("stage")],
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    for candidate in (run.get("scan", {}).get("top_candidates") or []):
        key = candidate_key(candidate) if candidate.get("strategy") else json.dumps(
            [candidate.get("symbol"), candidate.get("interval"), candidate.get("stage")],
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    candidates.sort(
        key=lambda c: (
            (c.get("selection_rank") or {}).get("score", -999999),
            (c.get("selection_rank") or {}).get("recent_loss_penalty_points", 0) * -1,
        ),
        reverse=True,
    )
    rows = []
    for candidate in candidates[:limit]:
        rank = candidate.get("selection_rank") or {}
        symbol_profile = rank.get("recent_symbol_loss_profile") or rank.get("recent_loss_profile") or {}
        strategy_profile = rank.get("recent_strategy_loss_profile") or {}
        strategy = candidate.get("strategy") or {}
        oos = candidate.get("oos_summary") or {}
        info = candidate.get("info_signal") or {}
        queue_profile = rank.get("strategy_recovery_queue_profile") or {}
        rows.append(
            {
                "symbol": candidate.get("symbol"),
                "stage": candidate.get("stage"),
                "interval": candidate.get("interval"),
                "strategy_family": strategy.get("family"),
                "selection_score": rank.get("score"),
                "entry_mode_estimate": strategy_profile.get("candidate_entry_mode_estimate"),
                "info_pressure_score": info.get("info_pressure_score", rank.get("info_score")),
                "strategy_recovery_queue_bonus_points": rank.get("strategy_recovery_queue_bonus_points", 0),
                "strategy_recovery_queue_decision": queue_profile.get("decision"),
                "strategy_recovery_queue_rank": queue_profile.get("queue_rank"),
                "oos_win_rate_pct": oos.get("win_rate_pct", rank.get("oos_win_rate_pct")),
                "oos_net_return_pct": oos.get("net_return_pct", rank.get("oos_net_return_pct")),
                "total_recent_loss_penalty_points": rank.get("recent_loss_penalty_points", 0),
                "symbol_loss_penalty_points": symbol_profile.get("penalty_points", 0),
                "strategy_loss_penalty_points": strategy_profile.get("penalty_points", 0),
                "symbol_loss_count": symbol_profile.get("loss_count", 0),
                "strategy_loss_count": strategy_profile.get("loss_count", 0),
                "validation_recovery_decision": (candidate.get("validation_recovery_gate") or {}).get("decision"),
                "validation_recovery_match": (candidate.get("validation_recovery_gate") or {}).get("matched_name"),
            }
        )
    return rows


def money(value):
    try:
        return f"${float(value):.6f}"
    except Exception:
        return "-"


def pct_text(value):
    try:
        return f"{float(value):+.4f}%"
    except Exception:
        return "-"


def safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_dt(value):
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def month_id_for(value):
    return value.astimezone(TZ).strftime("%Y-%m")


def normalize_monthly_goal_baselines(ledger):
    baselines = ledger.get("monthly_goal_baselines")
    if isinstance(baselines, dict):
        return baselines
    normalized = {}
    if isinstance(baselines, list):
        for record in baselines:
            if not isinstance(record, dict):
                continue
            month_id = str(record.get("month_id") or "").strip()
            if month_id:
                normalized[month_id] = record
    ledger["monthly_goal_baselines"] = normalized
    return normalized


def ensure_monthly_goal_baseline(ledger, now=None, reason="ledger_mutation"):
    """Persist the current Asia/Shanghai month-start baseline if missing.

    The monthly target is a compounding goal, so reports should not depend on a
    later heuristic scan of ledger events to rediscover the month-start equity.
    Existing explicit month records are never overwritten.
    """
    now_dt = now or utc_now()
    month_id = month_id_for(now_dt)
    baselines = normalize_monthly_goal_baselines(ledger)
    if month_id in baselines:
        return baselines[month_id]

    month_events = []
    for event in ledger.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_time = parse_dt(event.get("created_at") or event.get("updated_at"))
        if month_id_for(event_time) != month_id:
            continue
        event_equity = safe_float(event.get("equity_usd"))
        if event_equity is not None:
            month_events.append((event_time, event_equity))

    if month_events:
        month_events.sort(key=lambda item: item[0])
        started_at, baseline = month_events[0]
        source = "first_ledger_event_in_month"
    else:
        created_at = parse_dt(ledger.get("created_at"))
        initial = safe_float(ledger.get("initial_capital_usd"), 500.0) or 500.0
        equity = safe_float(ledger.get("equity_usd"), safe_float(ledger.get("cash_usd"), initial)) or initial
        if month_id_for(created_at) == month_id:
            started_at = created_at
            baseline = initial
            source = "ledger_initial_capital_current_month"
        else:
            started_at = now_dt
            baseline = equity
            source = "current_equity_fallback_no_month_baseline"

    record = {
        "month_id": month_id,
        "target_model": "monthly_compounding_double",
        "month_started_at": started_at.astimezone(timezone.utc).isoformat(),
        "created_at": iso(now_dt),
        "baseline_source": source,
        "baseline_reason": reason,
        "month_start_equity_usd": round(float(baseline), 6),
        "target_equity_usd": round(float(baseline) * 2.0, 6),
        "lifetime_initial_capital_usd": round(safe_float(ledger.get("initial_capital_usd"), 500.0) or 500.0, 6),
    }
    baselines[month_id] = record
    ledger.setdefault("events", []).append(
        {
            "event_type": "monthly_goal_baseline_created",
            "created_at": iso(now_dt),
            "month_id": month_id,
            "month_start_equity_usd": record["month_start_equity_usd"],
            "target_equity_usd": record["target_equity_usd"],
            "baseline_source": source,
            "baseline_reason": reason,
        }
    )
    return record


def reconcile_provisional_monthly_baseline_after_historical_close(ledger, reviewed, now):
    """Correct an unsaved fallback baseline after a pre-month historical close.

    Existing persisted monthly baselines remain immutable. This only adjusts a
    fallback record created in memory by the current load, before its first
    save, when barrier replay proves the position should have closed before the
    current month began.
    """

    if not ledger.get("_runtime_monthly_baseline_created_on_load"):
        return False
    month_id = month_id_for(now)
    record = normalize_monthly_goal_baselines(ledger).get(month_id)
    if not isinstance(record, dict) or record.get("baseline_source") != "current_equity_fallback_no_month_baseline":
        return False
    month_local = now.astimezone(TZ)
    month_start = datetime(month_local.year, month_local.month, 1, tzinfo=TZ).astimezone(timezone.utc)
    replay_closes = []
    for item in reviewed or []:
        replay = ((item.get("exit_detail") or {}).get("historical_barrier_replay") or {})
        if item.get("status") != "closed" or not replay.get("triggered_at"):
            continue
        triggered_at = parse_iso(replay["triggered_at"])
        if triggered_at < month_start:
            replay_closes.append(item)
    if not replay_closes:
        return False
    corrected = round(float(ledger.get("equity_usd", ledger.get("cash_usd", 0.0))), 6)
    record.update(
        {
            "month_started_at": iso(month_start),
            "created_at": iso(now),
            "baseline_source": "historical_replay_corrected_current_equity_fallback",
            "baseline_reason": "pre_month_barrier_replay_before_first_baseline_save",
            "month_start_equity_usd": corrected,
            "target_equity_usd": round(corrected * 2.0, 6),
        }
    )
    for event in ledger.get("events") or []:
        if event.get("event_type") == "monthly_goal_baseline_created" and event.get("month_id") == month_id:
            event.update(
                {
                    "created_at": iso(now),
                    "month_start_equity_usd": corrected,
                    "target_equity_usd": round(corrected * 2.0, 6),
                    "baseline_source": record["baseline_source"],
                    "baseline_reason": record["baseline_reason"],
                }
            )
            break
    ledger.setdefault("events", []).append(
        {
            "event_type": "monthly_goal_baseline_corrected_before_first_save",
            "created_at": iso(now),
            "month_id": month_id,
            "month_start_equity_usd": corrected,
            "historical_replay_trade_ids": [item.get("paper_trade_id") for item in replay_closes],
            "live_orders_enabled": False,
        }
    )
    return True


def monthly_goal_baseline(ledger, now=None):
    now_dt = now or utc_now()
    month_id = month_id_for(now_dt)
    initial = float(ledger.get("initial_capital_usd", 500.0) or 500.0)
    equity = float(ledger.get("equity_usd", initial) or initial)
    baseline = None
    source = "missing"
    baseline_created_at = None
    baselines = ledger.get("monthly_goal_baselines")
    if isinstance(baselines, dict):
        record = baselines.get(month_id)
        if isinstance(record, dict):
            baseline = (
                safe_float(record.get("month_start_equity_usd"))
                or safe_float(record.get("baseline_equity_usd"))
                or safe_float(record.get("initial_capital_usd"))
            )
            baseline_created_at = record.get("month_started_at") or record.get("created_at")
        else:
            baseline = safe_float(record)
        if baseline is not None:
            source = "ledger_monthly_goal_baselines"
    elif isinstance(baselines, list):
        for record in baselines:
            if not isinstance(record, dict) or str(record.get("month_id")) != month_id:
                continue
            baseline = (
                safe_float(record.get("month_start_equity_usd"))
                or safe_float(record.get("baseline_equity_usd"))
                or safe_float(record.get("initial_capital_usd"))
            )
            baseline_created_at = record.get("month_started_at") or record.get("created_at")
            if baseline is not None:
                source = "ledger_monthly_goal_baselines"
                break
    if baseline is None:
        month_events = []
        for event in ledger.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_time = parse_dt(event.get("created_at") or event.get("updated_at"))
            if month_id_for(event_time) != month_id:
                continue
            event_equity = safe_float(event.get("equity_usd"))
            if event_equity is not None:
                month_events.append((event_time, event_equity))
        if month_events:
            month_events.sort(key=lambda item: item[0])
            baseline_created_at, baseline = month_events[0]
            baseline_created_at = baseline_created_at.isoformat()
            source = "first_ledger_event_in_month"
    if baseline is None:
        created_at = parse_dt(ledger.get("created_at"))
        if month_id_for(created_at) == month_id:
            baseline = initial
            baseline_created_at = ledger.get("created_at")
            source = "ledger_initial_capital_current_month"
        else:
            baseline = equity
            baseline_created_at = now_dt.isoformat()
            source = "current_equity_fallback_no_month_baseline"
    return month_id, float(baseline), source, baseline_created_at


def monthly_double_progress(ledger):
    month_id, initial, source, baseline_created_at = monthly_goal_baseline(ledger)
    lifetime_initial = float(ledger.get("initial_capital_usd", 500.0) or 500.0)
    equity = float(ledger.get("equity_usd", initial) or initial)
    target = initial * 2.0
    denominator = max(target - initial, 1e-9)
    progress = max(0.0, min(100.0, (equity - initial) / denominator * 100.0))
    return {
        "target_model": "monthly_compounding_double",
        "month_id": month_id,
        "baseline_source": source,
        "baseline_created_at": baseline_created_at,
        "lifetime_initial_capital_usd": round(lifetime_initial, 6),
        "initial_capital_usd": round(initial, 6),
        "month_start_equity_usd": round(initial, 6),
        "target_equity_usd": round(target, 6),
        "current_equity_usd": round(equity, 6),
        "gap_to_target_usd": round(target - equity, 6),
        "progress_pct": round(progress, 4),
        "target_return_pct": 100.0,
        "current_return_pct": round((equity / initial - 1.0) * 100.0 if initial else 0.0, 4),
    }


def render_open_positions_table(ledger):
    positions = ledger.get("open_positions", [])
    lines = [
        "| Trade | Symbol | Mode | Size Tier | Notional | Entry | Last | PnL | PnL % | Max PnL % | Risk Guard | Stop | Take Profit | Expiry |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    if not positions:
        lines.append("| none | - | - | - | - | - | - | - | - | - | - | - | - | - |")
        return lines
    for pos in positions:
        risk = pos.get("risk_state") or {}
        guard = "armed" if risk.get("profit_protection_armed") else "watch"
        if risk.get("trailing_floor_pct") is not None:
            guard = f"floor {risk.get('trailing_floor_pct')}%"
        size_tier = (pos.get("sizing_decision") or {}).get("tier", "-")
        lines.append(
            "| "
            f"`{pos.get('paper_trade_id')}` | `{pos.get('symbol')}` | "
            f"{pos.get('paper_entry_mode', pos.get('strategy_family', '-'))} | {size_tier} | "
            f"{money(pos.get('notional_usd'))} | {pos.get('entry_price', '-')} | {pos.get('last_price', '-')} | "
            f"{money(pos.get('unrealized_pnl_usd'))} | {pct_text(pos.get('unrealized_pnl_pct'))} | "
            f"{pct_text(pos.get('max_unrealized_pnl_pct'))} | {guard} | "
            f"{pos.get('stop_price', '-')} | {pos.get('take_profit_price', '-')} | {pos.get('expires_at', '-')} |"
        )
    return lines


def render_closed_trades_table(ledger):
    trades = ledger.get("closed_trades", [])[-8:]
    lines = [
        "| Trade | Symbol | Outcome | Exit Reason | Entry | Exit | Realized PnL | PnL % | Closed At |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    if not trades:
        lines.append("| none | - | - | - | - | - | - | - | - |")
        return lines
    for trade in trades:
        lines.append(
            "| "
            f"`{trade.get('paper_trade_id')}` | `{trade.get('symbol')}` | {trade.get('outcome', '-')} | "
            f"{trade.get('exit_reason', '-')} | {trade.get('entry_price', '-')} | {trade.get('exit_price', '-')} | "
            f"{money(trade.get('realized_pnl_usd'))} | {pct_text(trade.get('realized_pnl_pct'))} | {trade.get('closed_at', '-')} |"
        )
    return lines


def render_recent_events_table(ledger):
    events = ledger.get("events", [])[-10:]
    lines = [
        "| Time | Event | Symbol | Trade | Detail |",
        "|---|---|---|---|---|",
    ]
    if not events:
        lines.append("| none | - | - | - | - |")
        return lines
    for event in events:
        detail = event.get("exit_reason") or event.get("notes") or f"equity={event.get('equity_usd', '-')}"
        lines.append(
            f"| {event.get('created_at', '-')} | {event.get('event_type', '-')} | `{event.get('symbol', '-')}` | "
            f"`{event.get('paper_trade_id', event.get('run_id', '-'))}` | {detail} |"
        )
    return lines


def render_paper_orders_table(ledger):
    orders = ledger.get("paper_orders", [])[-10:]
    lines = [
        "| Time | Order | Trade | Symbol | Side | Status | Qty | Avg Fill | Fee | Reason |",
        "|---|---|---|---|---|---|---:|---:|---:|---|",
    ]
    if not orders:
        lines.append("| none | - | - | - | - | - | - | - | - | - |")
        return lines
    for order in orders:
        lines.append(
            f"| {order.get('created_at', '-')} | `{order.get('paper_order_id')}` | "
            f"`{order.get('paper_trade_id')}` | `{order.get('symbol')}` | {order.get('side')} | "
            f"{order.get('status')} | {order.get('executed_quantity', '-')} | "
            f"{order.get('average_fill_price', '-')} | {money(order.get('commission_usd'))} | "
            f"{order.get('order_reason', '-')} |"
        )
    return lines


def render_dynamic_scan_pool_table(run):
    pool = run.get("dynamic_scan_pool") or {}
    pool_shape = pool.get("pool_shape_policy") or {}
    pool_width = pool.get("pool_width_policy") or {}
    selected = pool.get("selected_symbols") or []
    top = pool.get("top_dynamic_candidates") or []
    lines = [
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{pool.get('status', 'missing')}` |",
        f"| Selection policy | {pool.get('selection_policy') or '-'} |",
        f"| Configured max symbols | `{pool_width.get('configured_limit', pool.get('configured_max_symbols', pool.get('max_symbols')) )}` |",
        f"| Effective max symbols | `{pool_width.get('effective_limit', pool.get('max_symbols'))}` |",
        f"| Width policy | `{pool_width.get('reason') or '-'}` |",
        f"| Width multiplier | `{pool_width.get('multiplier') or '-'}` |",
        f"| Pool shape reason | `{pool_shape.get('reason') or '-'}` |",
        f"| Pool quota status | `{pool_shape.get('quota_status') or '-'}` |",
        f"| Pool min slots | `{pool_shape.get('min_slots') or {}}` |",
        f"| Pool max slots | `{pool_shape.get('max_slots') or {}}` |",
        f"| Pool selected counts | `{pool_shape.get('selected_counts') or {}}` |",
        f"| Pool min slot gaps | `{pool_shape.get('min_slot_gaps') or {}}` |",
        f"| Pool max slot excess | `{pool_shape.get('max_slot_excess') or {}}` |",
        f"| Market regime | `{pool.get('market_regime')}` |",
        f"| Market atmosphere | `{pool.get('market_atmosphere')}` |",
        f"| Pool bias | `{pool.get('pool_bias')}` |",
        f"| Sentiment state | `{pool.get('sentiment_state')}` |",
        f"| Sentiment overlay | `{pool.get('sentiment_overlay')}` |",
        f"| Short-term state | `{pool.get('short_term_state')}` |",
        f"| Short-term overlay | `{pool.get('short_term_overlay')}` |",
        f"| Why pool changed from static baseline | {pool.get('why_pool_changed_from_static_baseline') or '-'} |",
        f"| Short-term anchor 1h avg | `{pool.get('short_term_avg_1h_anchor_change_pct')}` |",
        f"| Short-term anchor 4h avg | `{pool.get('short_term_avg_4h_anchor_change_pct')}` |",
        f"| Social handoff freshness | `{pool.get('social_handoff_freshness')}` |",
        f"| Social handoff age hours | `{pool.get('social_handoff_age_hours')}` |",
        f"| Social handoff max age hours | `{pool.get('social_handoff_max_age_hours')}` |",
        f"| Social handoff reason | `{pool.get('social_handoff_reason') or '-'}` |",
        f"| Positive breadth | `{pool.get('positive_breadth_pct')}` |",
        f"| Avg 24h change | `{pool.get('average_change_24h_pct')}` |",
        f"| Strong gainers pct | `{pool.get('strong_gainers_pct')}` |",
        f"| Hard sellers pct | `{pool.get('hard_sellers_pct')}` |",
        f"| Top20 avg 24h | `{pool.get('top20_average_change_24h_pct')}` |",
        f"| BTC 24h | `{pool.get('btc_change_24h_pct')}` |",
        f"| ETH 24h | `{pool.get('eth_change_24h_pct')}` |",
        f"| Social long total | `{pool.get('social_long_total')}` |",
        f"| Social risk total | `{pool.get('social_risk_total')}` |",
        f"| Liquid USDT symbols | `{pool.get('liquid_usdt_symbol_count')}` |",
        f"| Selected symbols | `{', '.join(selected[:30]) or '-'}` |",
        f"| Open symbols preserved | `{', '.join(pool.get('open_symbols') or []) or '-'}` |",
        f"| Recovery queue preserved | `{', '.join(pool.get('queued_symbols') or []) or '-'}` |",
    ]
    lines.extend(["", "| Rank | Symbol | Bucket | Score | 24h | Volume | Social Long | Social Risk |", "|---:|---|---|---:|---:|---:|---:|---:|"])
    if not top:
        lines.append("| none | - | - | - | - | - | - | - |")
        return lines
    for idx, item in enumerate(top[:10], 1):
        lines.append(
            f"| {idx} | `{item.get('symbol')}` | `{item.get('bucket')}` | {item.get('score')} | "
            f"{pct_text(item.get('price_change_24h_pct'))} | {money(item.get('quote_volume_24h_usd'))} | "
            f"{item.get('social_long_score', 0)} | {item.get('social_risk_score', 0)} |"
        )
    return lines


def render_monitoring_table(run):
    info_by_symbol = {row["symbol"]: row for row in run.get("info_signals", {}).get("rows", [])}
    candidates_by_symbol = {}
    for candidate in run.get("scan", {}).get("top_candidates", []):
        candidates_by_symbol.setdefault(candidate["symbol"], candidate)
    symbols = run.get("scan_symbols", [])
    lines = [
        "| Symbol | Info Score | Tier | 24h | Volume | Best Stage | Watch Reason |",
        "|---|---:|---|---:|---:|---|---|",
    ]
    if not symbols:
        lines.append("| none | - | - | - | - | - | - |")
        return lines
    for symbol in symbols:
        info = info_by_symbol.get(symbol, {})
        candidate = candidates_by_symbol.get(symbol, {})
        reasons = ", ".join((info.get("reasons") or [])[:3]) or "open/benchmark/deep-scan"
        lines.append(
            f"| `{symbol}` | {info.get('info_pressure_score', 0)} | {info.get('tier', '-')} | "
            f"{pct_text(info.get('price_change_24h_pct'))} | {money(info.get('quote_volume_24h_usd'))} | "
            f"{candidate.get('stage', '-')} | {reasons} |"
        )
    return lines


def render_candidate_attribution_table(run):
    rows = candidate_attribution_summary(run)
    lines = [
        "| Symbol | Stage | Strategy | Score | Info | Recovery Queue | OOS Win | OOS Return | Total Penalty | Symbol Penalty | Strategy Penalty | Entry Mode | Recovery Gate |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    if not rows:
        lines.append("| none | - | - | - | - | - | - | - | - | - | - | - | - |")
        return lines
    for row in rows:
        strategy = " ".join(
            part
            for part in [row.get("interval"), row.get("strategy_family")]
            if part
        ) or "-"
        lines.append(
            "| "
            f"`{row.get('symbol', '-')}` | {row.get('stage', '-')} | {strategy} | "
            f"{row.get('selection_score', '-')} | {row.get('info_pressure_score', 0)} | "
            f"{row.get('strategy_recovery_queue_bonus_points', 0)}"
            f"{(' / #' + str(row.get('strategy_recovery_queue_rank'))) if row.get('strategy_recovery_queue_rank') else ''} | "
            f"{pct_text(row.get('oos_win_rate_pct'))} | {pct_text(row.get('oos_net_return_pct'))} | "
            f"{row.get('total_recent_loss_penalty_points', 0)} | {row.get('symbol_loss_penalty_points', 0)} | "
            f"{row.get('strategy_loss_penalty_points', 0)} | {row.get('entry_mode_estimate', '-')} | "
            f"{row.get('validation_recovery_decision') or '-'}"
            f"{(' / ' + row.get('validation_recovery_match')) if row.get('validation_recovery_match') else ''} |"
        )
    return lines


def render_kline_cache_audit_table(run):
    audit = run.get("kline_cache_audit") or {}
    summary = kline_cache_audit_summary(audit)
    kline_filter = run.get("kline_prefetch_filter") or {}
    lines = [
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{summary.get('status', '-')}` |",
        f"| Cache dir | `{summary.get('cache_dir', '-')}` |",
        f"| Checked pairs | {summary.get('checked_pairs', '-')} |",
        f"| Verified | {summary.get('verified_count', '-')} |",
        f"| Stale | {summary.get('stale_count', '-')} |",
        f"| Missing | {summary.get('missing_count', '-')} |",
        f"| Open bars | {summary.get('open_bar_count', '-')} |",
        f"| Max lag minutes | {summary.get('max_lag_minutes', '-')} |",
        f"| Latest data time | `{summary.get('latest_close_at', '-')}` |",
        f"| Stale/missing filter | `{kline_filter.get('status', '-')}` |",
        f"| Removed symbols | `{', '.join(kline_filter.get('removed_symbols') or []) or '-'}` |",
    ]
    problems = summary.get("problem_rows") or []
    if problems:
        lines.extend(["", "| Symbol | Interval | Status | Lag Minutes | Max Allowed |", "|---|---|---|---:|---:|"])
        for row in problems:
            lines.append(
                f"| `{row.get('symbol', '-')}` | {row.get('interval', '-')} | {row.get('status', '-')} | "
                f"{row.get('lag_minutes', '-')} | {row.get('max_allowed_lag_minutes', '-')} |"
            )
    return lines


def render_report(run):
    opened = run.get("opened_position")
    opened_positions = run.get("opened_positions") or ([opened] if opened else [])
    target = run.get("monthly_double_progress") or monthly_double_progress(run["ledger"])
    lines = [
        f"# {run.get('loop_title', 'Sunday Crypto Realistic Paper Loop')} | {run['run_id']}",
        "",
        "No live orders were placed. This is realistic paper trading only.",
        "",
        "## Portfolio",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Cash | ${run['ledger']['cash_usd']:.6f} |",
        f"| Open value | ${run['ledger'].get('open_value_usd', 0.0):.6f} |",
        f"| Equity | ${run['ledger'].get('equity_usd', 0.0):.6f} |",
        f"| Net return | {run['ledger'].get('net_return_pct', 0.0):+.4f}% |",
        f"| Max drawdown | {run['ledger'].get('max_drawdown_pct', 0.0):+.4f}% |",
        "",
        "## Monthly Double Tracker",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Target model | `{target.get('target_model')}` |",
        f"| Month | `{target.get('month_id')}` |",
        f"| Baseline source | `{target.get('baseline_source')}` |",
        f"| Month-start equity | ${target['month_start_equity_usd']:.6f} |",
        f"| Lifetime initial capital | ${target.get('lifetime_initial_capital_usd', target['initial_capital_usd']):.6f} |",
        f"| Target equity | ${target['target_equity_usd']:.6f} |",
        f"| Current equity | ${target['current_equity_usd']:.6f} |",
        f"| Gap to target | ${target['gap_to_target_usd']:.6f} |",
        f"| Progress | {target['progress_pct']:.4f}% |",
        f"| Current return | {target['current_return_pct']:+.4f}% |",
        "",
        "## Current Holdings",
        "",
        *render_open_positions_table(run["ledger"]),
        "",
        "## Simulated API Orders",
        "",
        *render_paper_orders_table(run["ledger"]),
        "",
        "## Trade Tracking",
        "",
        *render_closed_trades_table(run["ledger"]),
        "",
        "## Recent Ledger Events",
        "",
        *render_recent_events_table(run["ledger"]),
        "",
        "## Execution",
        "",
        f"- Reviewed positions: {len(run['reviewed_positions'])}",
        f"- New paper open: {', '.join(p['paper_trade_id'] for p in opened_positions) if opened_positions else 'none'}",
        f"- New paper open count: {len(opened_positions)}",
        f"- Strategy version: `{run['strategy_version']}`",
        f"- Data status: `{run['data_status']}`",
        f"- Entry mode: `{(opened or {}).get('paper_entry_mode', 'none')}`",
        f"- Research committee: `{'degraded' if run.get('research_committee_degraded') else 'available'}`",
        f"- Research panel missing reason: `{run.get('research_panel_missing_reason') or 'none'}`",
        f"- Max allowed action: `{run.get('max_allowed_action')}`",
        "",
        "## Paper Auto Learning Loop",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Strategy version | `{(run.get('paper_auto_learning_loop') or {}).get('strategy_version')}` |",
        f"| Overlay updated | `{(run.get('paper_auto_learning_loop') or {}).get('overlay_updated_at')}` |",
        f"| Auto learning enabled | `{(run.get('paper_auto_learning_loop') or {}).get('auto_learning_enabled')}` |",
        f"| Overlay path | `{(run.get('paper_auto_learning_loop') or {}).get('overlay_path')}` |",
        f"| Live orders | `{str((run.get('paper_auto_learning_loop') or {}).get('live_orders_enabled')).lower()}` |",
        f"| Private API | `{str((run.get('paper_auto_learning_loop') or {}).get('private_api_used')).lower()}` |",
        "",
        "## Paper/Testnet Portfolio Risk Control",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Audit status | `{(run.get('paper_testnet_risk_control') or {}).get('status')}` |",
        f"| Contract status | `{(run.get('paper_testnet_risk_control') or {}).get('control_contract_status')}` |",
        f"| Risk decision | `{(run.get('paper_testnet_risk_control') or {}).get('risk_decision')}` |",
        f"| Size multiplier | `{(run.get('paper_testnet_risk_control') or {}).get('recommended_new_entry_size_multiplier')}` |",
        f"| Max new notional | `${(run.get('paper_testnet_risk_control') or {}).get('max_new_entry_notional_usd')}` |",
        f"| Daily realized PnL | `${((run.get('paper_testnet_risk_control') or {}).get('metrics') or {}).get('daily_realized_pnl_usd')}` |",
        f"| Consecutive losses | `{((run.get('paper_testnet_risk_control') or {}).get('metrics') or {}).get('consecutive_loss_streak')}` |",
        f"| Triggers | `{', '.join((run.get('paper_testnet_risk_control') or {}).get('triggers') or []) or 'none'}` |",
        "",
        "## Validation Capacity Gate",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Enabled | `{(run.get('validation_capacity_gate') or {}).get('enabled')}` |",
        f"| Decision | `{(run.get('validation_capacity_gate') or {}).get('decision')}` |",
        f"| Sample action | `{(run.get('validation_capacity_gate') or {}).get('sample_action')}` |",
        f"| Runner mode | `{(run.get('validation_capacity_gate') or {}).get('recommended_runner_mode')}` |",
        f"| Paper win rate | `{(run.get('validation_capacity_gate') or {}).get('paper_win_rate_pct')}` |",
        f"| Closed net return | `{(run.get('validation_capacity_gate') or {}).get('closed_net_return_pct')}` |",
        f"| Failed gates | `{', '.join((run.get('validation_capacity_gate') or {}).get('failed_gates') or []) or 'none'}` |",
        "",
        "## Validation Recovery Gate",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Recovery status | `{(((run.get('validation_capacity_gate') or {}).get('validation_recovery_plan_summary') or {}).get('status'))}` |",
        f"| New sample policy | `{(((run.get('validation_capacity_gate') or {}).get('validation_recovery_plan_summary') or {}).get('new_sample_policy'))}` |",
        f"| Retired entry modes | `{', '.join((((run.get('validation_capacity_gate') or {}).get('validation_recovery_plan_summary') or {}).get('retired_entry_modes') or [])) or 'none'}` |",
        f"| Cooldown entry modes | `{', '.join((((run.get('validation_capacity_gate') or {}).get('validation_recovery_plan_summary') or {}).get('cooldown_entry_modes') or [])) or 'none'}` |",
        f"| Retired strategy families | `{', '.join((((run.get('validation_capacity_gate') or {}).get('validation_recovery_plan_summary') or {}).get('retired_strategy_families') or [])) or 'none'}` |",
        f"| Eligible entry modes | `{', '.join((((run.get('validation_capacity_gate') or {}).get('validation_recovery_plan_summary') or {}).get('eligible_entry_modes') or [])) or 'none'}` |",
        "",
        "## Dynamic Scan Pool",
        "",
        "The configured symbol list is only the baseline/fallback. The effective scan pool is rebuilt from live market breadth, liquidity, 24h movement, asset bucket bias, recovery queue and latest social sentiment before K-line scanning.",
        "",
        *render_dynamic_scan_pool_table(run),
        "",
        "## Scan",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Symbols deep-scanned | {len(run.get('scan_symbols', []))} |",
        f"| Frames loaded | {run['scan']['frames_loaded']} |",
        f"| Strategies scanned | {run['scan']['strategies_scanned']} |",
        f"| Current signal strategies | {run['scan']['current_signal_strategies']} |",
        f"| Raw candidates after per-frame top filter | {run['scan'].get('candidate_count', 0)} |",
        f"| Deduplicated candidates | {run['scan'].get('deduplicated_candidate_count', 0)} |",
        f"| Performance duplicates removed | {(run['scan'].get('dedup_summary') or {}).get('performance_duplicate_count', 0)} |",
        f"| Scan budget decision | `{(run['scan'].get('scan_budget') or {}).get('decision', '-')}` |",
        f"| Effective strategies per frame | {(run['scan'].get('scan_budget') or {}).get('effective_max_strategies_per_frame', '-')} |",
        f"| Estimated max strategies | {(run['scan'].get('scan_budget') or {}).get('estimated_max_strategies', '-')} |",
        f"| Market fetch budget status | `{(run.get('market_fetch_budget') or {}).get('status', '-')}` |",
        f"| Kline fetch budget seconds | `{(run.get('kline_prefetch_budget') or {}).get('wall_clock_seconds', '-')}` |",
        f"| Kline fetch budget status | `{((run.get('kline_prefetch_budget') or {}).get('budget_status') or {}).get('status', '-')}` |",
        f"| Info fetch budget status | `{(run.get('info_fetch_budget') or {}).get('status', '-')}` |",
        "",
        "## Kline Cache Freshness",
        "",
        *render_kline_cache_audit_table(run),
        "",
        "## Candidate Attribution",
        "",
        *render_candidate_attribution_table(run),
        "",
        "## Monitored Symbols",
        "",
        *render_monitoring_table(run),
    ]
    selected_candidates = run.get("selected_candidates") or ([run["selected_candidate"]] if run.get("selected_candidate") else [])
    if selected_candidates:
        c = selected_candidates[0]
        lines.extend(
            [
                "",
                "## Selected Candidates",
                "",
            ]
        )
        for c in selected_candidates:
            lines.extend(
                [
                f"- Symbol: `{c['symbol']}`",
                f"- Stage: `{c['stage']}`",
                f"- Strategy: `{c['interval']} {c['strategy']['family']}`",
                f"- OOS final capital: `${c['oos_summary']['final_capital']}`",
                f"- OOS win rate: `{c['oos_summary']['win_rate_pct']}%`",
                f"- OOS max drawdown: `{c['oos_summary']['max_drawdown_pct']}%`",
                f"- Info pressure: `{(c.get('info_signal') or {}).get('info_pressure_score', 0)}`",
                f"- Selection score: `{(c.get('selection_rank') or {}).get('score', '-')}`",
                f"- Recent-loss rotation penalty: `{(c.get('selection_rank') or {}).get('recent_loss_penalty_points', 0)}`",
                f"- Symbol-loss penalty: `{(((c.get('selection_rank') or {}).get('recent_symbol_loss_profile') or {}).get('penalty_points', 0))}`",
                f"- Strategy-loss penalty: `{(((c.get('selection_rank') or {}).get('recent_strategy_loss_profile') or {}).get('penalty_points', 0))}`",
                f"- Entry mode: `{c.get('paper_entry_mode')}`",
                f"- Validation recovery gate: `{((c.get('validation_recovery_gate') or {}).get('decision') or '-')}`",
                f"- Learning guard: `{((c.get('entry_mode_learning_gate') or {}).get('decision') or '-')}`",
                f"- Recent-loss quality gate: `{((c.get('recent_loss_quality_gate') or {}).get('decision') or '-')}`",
                f"- Planned paper notional: `{money(c.get('planned_notional_usd'))}`",
                f"- Sizing tier: `{(c.get('sizing_decision') or {}).get('tier', '-')}`",
                f"- Sizing reason: `{(c.get('sizing_decision') or {}).get('reason', '-')}`",
                "",
                ]
            )
    if run.get("info_signals"):
        lines.extend(["", "## Info/Volatility Radar", "", "| Symbol | Score | Tier | 24h | Volume | Reasons |", "|---|---:|---|---:|---:|---|"])
        for row in run["info_signals"].get("rows", [])[:8]:
            lines.append(
                f"| `{row['symbol']}` | {row['info_pressure_score']} | {row['tier']} | "
                f"{row['price_change_24h_pct']:+.2f}% | ${row['quote_volume_24h_usd']:.0f} | {', '.join(row['reasons'][:3])} |"
            )
    if run.get("rejections"):
        lines.extend(["", "## Rejections", ""])
        for item in run["rejections"][:8]:
            gate = item.get("validation_recovery_gate") or {}
            if item.get("reason") == "validation_recovery_plan_block":
                lines.append(
                    f"- `{item.get('symbol', 'portfolio')}`: {item.get('reason')} "
                    f"({gate.get('matched_group')} `{gate.get('matched_name')}` -> `{gate.get('matched_status')}`)"
                )
            else:
                lines.append(f"- `{item.get('symbol', 'portfolio')}`: {item.get('reason')}")
    lines.extend(
        [
            "",
            "## Session Summary",
            "",
            f"- Closed trades today: {run['session_summary']['closed_trade_count']}",
            f"- Realized PnL today: ${run['session_summary']['realized_pnl_usd']:.6f}",
            f"- Session win rate: {run['session_summary']['win_rate_pct']:.2f}%",
            "",
        ]
    )
    return "\n".join(lines)


@contextmanager
def run_lock(enabled=True):
    if not enabled:
        yield
        return
    if LOCK_PATH.exists():
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age < 55 * 60:
            raise SystemExit(f"run lock exists: {LOCK_PATH}")
        LOCK_PATH.unlink()
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
        yield
    finally:
        os.close(fd)
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass


def in_sunday_window(now):
    local = local_dt(now)
    return local.weekday() == 6 and 10 <= local.hour <= 18


def mode_metadata(loop_kind):
    if loop_kind == "fast":
        return {
            "config_key": "fast_crypto_paper_auto_trader",
            "run_label": "fast-crypto-paper-auto-trader",
            "state_name": "fast_crypto_strategy_state.json",
            "cache_name": "fast_crypto_paper_auto_trader",
            "report_prefix": "fast-crypto-paper",
            "candidate_type": "fast_crypto_paper_auto_trader",
            "title": "Fast Crypto Paper Auto Trader",
        }
    if loop_kind == "daily":
        return {
            "config_key": "daily_crypto_paper_auto_trader",
            "run_label": "daily-crypto-paper-auto-trader",
            "state_name": "daily_crypto_strategy_state.json",
            "cache_name": "daily_crypto_paper_auto_trader",
            "report_prefix": "daily-crypto-paper",
            "candidate_type": "daily_crypto_paper_auto_trader",
            "title": "Daily Crypto Paper Auto Trader",
        }
    return {
        "config_key": "sunday_crypto_realistic_paper_loop",
        "run_label": "sunday-crypto-realistic-paper",
        "state_name": "sunday_crypto_strategy_state.json",
        "cache_name": "sunday_crypto_realistic",
        "report_prefix": "sunday-crypto-hourly",
        "candidate_type": "sunday_crypto_realistic_paper_loop",
        "title": "Sunday Crypto Realistic Paper Loop",
    }


def run_loop(args):
    now = parse_now(args.now)
    local = local_dt(now)
    cfg = load_config(args.config)
    paper_strategy_overlay = pso.load_overlay()
    meta = mode_metadata(args.loop_kind)
    loop_cfg = dict(cfg.get("sunday_crypto_realistic_paper_loop", {}))
    loop_cfg.update(cfg.get(meta["config_key"], {}))
    paper_cfg = cfg.get("paper_portfolio", {})
    exit_cfg = cfg.get("paper_exit_management", {})
    sizing_cfg = cfg.get("paper_dynamic_sizing", {})
    recent_loss_quality_cfg = cfg.get("paper_recent_loss_quality_gate", {})
    recovery_gate_cfg = cfg.get("validation_recovery_plan_gate", {})
    strategy_recovery_cfg = cfg.get("strategy_recovery_optimizer", {})
    scan_budget_cfg = loop_cfg.get("adaptive_scan_budget", {})
    settings = {
        "commission_bps": loop_cfg.get("commission_bps", 10),
        "base_slippage_bps": loop_cfg.get("base_slippage_bps", 8),
        "max_dynamic_impact_bps": loop_cfg.get("max_dynamic_impact_bps", 50),
        "min_quote_volume_usd": loop_cfg.get("min_quote_volume_usd", 5_000_000),
        "min_depth_1pct_usd": loop_cfg.get("min_depth_1pct_usd", 25_000),
        "min_depth_to_notional": loop_cfg.get("min_depth_to_notional", 5),
        "max_spread_bps": loop_cfg.get("max_spread_bps", 35),
        "max_cross_source_deviation_bps": loop_cfg.get("max_cross_source_deviation_bps", 200),
        "default_notional_usd": loop_cfg.get("default_notional_usd", 75),
        "default_initial_capital_usd": paper_cfg.get("initial_capital_usd", 500),
        "exploratory_probe_enabled": loop_cfg.get("exploratory_probe_enabled", True),
        "exploratory_notional_usd": loop_cfg.get("exploratory_notional_usd", 25),
        "exploratory_allowed_stages": loop_cfg.get("exploratory_allowed_stages", ["paper_only", "research_watch"]),
        "exploratory_min_info_score": loop_cfg.get("exploratory_min_info_score", 30),
        "exploratory_min_oos_trades": loop_cfg.get("exploratory_min_oos_trades", 5),
        "exploratory_min_oos_drawdown_pct": loop_cfg.get("exploratory_min_oos_drawdown_pct", -35),
        "validation_probe_enabled": loop_cfg.get("validation_probe_enabled", False),
        "validation_probe_allowed_stages": loop_cfg.get("validation_probe_allowed_stages", ["paper_only"]),
        "validation_probe_notional_usd": loop_cfg.get("validation_probe_notional_usd", sizing_cfg.get("min_trade_notional_usd", 25)),
        "validation_probe_min_oos_trades": loop_cfg.get("validation_probe_min_oos_trades", 10),
        "validation_probe_min_oos_win_rate_pct": loop_cfg.get("validation_probe_min_oos_win_rate_pct", 50),
        "validation_probe_min_oos_final_capital": loop_cfg.get("validation_probe_min_oos_final_capital", paper_cfg.get("initial_capital_usd", 500)),
        "validation_probe_min_oos_net_return_pct": loop_cfg.get("validation_probe_min_oos_net_return_pct", 0),
        "validation_probe_min_oos_drawdown_pct": loop_cfg.get("validation_probe_min_oos_drawdown_pct", -35),
        "validation_probe_min_train_final_capital": loop_cfg.get("validation_probe_min_train_final_capital", 0),
        "info_prefilter_min_score": loop_cfg.get("info_prefilter_min_score", 25),
        "info_prefilter_max_symbols": loop_cfg.get("info_prefilter_max_symbols", 8),
        "dynamic_scan_pool_enabled": loop_cfg.get("dynamic_scan_pool_enabled", True),
        "dynamic_scan_pool_max_symbols": loop_cfg.get(
            "dynamic_scan_pool_max_symbols",
            max(int(loop_cfg.get("info_prefilter_max_symbols", 8) or 8) * 2, 18),
        ),
        "dynamic_scan_pool_min_quote_volume_usd": loop_cfg.get(
            "dynamic_scan_pool_min_quote_volume_usd",
            loop_cfg.get("min_quote_volume_usd", 5_000_000),
        ),
        "dynamic_scan_pool_social_max_age_hours": loop_cfg.get("dynamic_scan_pool_social_max_age_hours", 12.0),
        "max_single_trade_notional_usd": paper_cfg.get("max_single_trade_notional_usd", 150),
        "max_open_positions": paper_cfg.get("max_open_positions", 3),
        "default_stop_pct": paper_cfg.get("default_stop_pct", -8),
        "default_take_profit_pct": paper_cfg.get("default_take_profit_pct", 20),
        "default_max_holding_window": paper_cfg.get("default_max_holding_window", "7d"),
        "short_horizon_max_holding_hours": paper_cfg.get("short_horizon_max_holding_hours", 36),
        "one_hour_max_holding_hours": paper_cfg.get("one_hour_max_holding_hours", 96),
        "four_hour_max_holding_hours": paper_cfg.get("four_hour_max_holding_hours", 168),
        "request_pause_seconds": loop_cfg.get("request_pause_seconds", 0.03),
        "market_fetch_wall_clock_seconds": (
            args.market_fetch_wall_clock_seconds
            if args.market_fetch_wall_clock_seconds is not None
            else loop_cfg.get("market_fetch_wall_clock_seconds", 0)
        ),
        "market_fetch_min_request_start_seconds": loop_cfg.get("market_fetch_min_request_start_seconds", 5),
        "market_request_timeout_seconds": loop_cfg.get("market_request_timeout_seconds", 8),
        "market_fetch_attempts": loop_cfg.get("market_fetch_attempts", 1),
        "info_fetch_wall_clock_seconds": (
            args.info_fetch_wall_clock_seconds
            if args.info_fetch_wall_clock_seconds is not None
            else loop_cfg.get("info_fetch_wall_clock_seconds", 0)
        ),
        "info_fetch_min_request_start_seconds": loop_cfg.get("info_fetch_min_request_start_seconds", 5),
        "info_request_timeout_seconds": loop_cfg.get("info_request_timeout_seconds", 8),
        "dynamic_sizing_enabled": sizing_cfg.get("enabled", True),
        "min_trade_notional_usd": sizing_cfg.get("min_trade_notional_usd", loop_cfg.get("exploratory_notional_usd", 25)),
        "mid_probe_notional_usd": sizing_cfg.get("mid_probe_notional_usd", 50),
        "accelerated_probe_notional_usd": sizing_cfg.get("accelerated_probe_notional_usd", 75),
        "strict_default_notional_usd": sizing_cfg.get("strict_default_notional_usd", loop_cfg.get("default_notional_usd", 75)),
        "strict_max_notional_usd": sizing_cfg.get("strict_max_notional_usd", paper_cfg.get("max_single_trade_notional_usd", 150)),
        "max_single_position_pct_of_equity": sizing_cfg.get("max_single_position_pct_of_equity", 30),
        "max_total_open_exposure_pct_of_equity": sizing_cfg.get("max_total_open_exposure_pct_of_equity", 75),
        "min_cash_reserve_usd": sizing_cfg.get("min_cash_reserve_usd", 50),
        "high_cash_ratio_threshold_pct": sizing_cfg.get("high_cash_ratio_threshold_pct", 70),
        "mid_probe_min_info_score": sizing_cfg.get("mid_probe_min_info_score", 30),
        "mid_probe_min_oos_win_rate_pct": sizing_cfg.get("mid_probe_min_oos_win_rate_pct", 60),
        "mid_probe_min_oos_final_capital": sizing_cfg.get("mid_probe_min_oos_final_capital", 650),
        "mid_probe_min_oos_drawdown_pct": sizing_cfg.get("mid_probe_min_oos_drawdown_pct", -25),
        "accelerated_probe_min_info_score": sizing_cfg.get("accelerated_probe_min_info_score", 40),
        "accelerated_probe_min_oos_win_rate_pct": sizing_cfg.get("accelerated_probe_min_oos_win_rate_pct", 70),
        "accelerated_probe_min_oos_final_capital": sizing_cfg.get("accelerated_probe_min_oos_final_capital", 800),
        "accelerated_probe_min_oos_drawdown_pct": sizing_cfg.get("accelerated_probe_min_oos_drawdown_pct", -15),
        "strict_full_size_min_train_net_return_pct": sizing_cfg.get("strict_full_size_min_train_net_return_pct", 20),
        "strict_full_size_min_oos_win_rate_pct": sizing_cfg.get("strict_full_size_min_oos_win_rate_pct", 60),
        "strict_full_size_min_oos_drawdown_pct": sizing_cfg.get("strict_full_size_min_oos_drawdown_pct", -20),
        "target_pressure_enabled": sizing_cfg.get("target_pressure_enabled", False),
        "target_pressure_notional_usd": sizing_cfg.get("target_pressure_notional_usd", sizing_cfg.get("accelerated_probe_notional_usd", 75)),
        "target_pressure_min_gap_to_target_pct": sizing_cfg.get("target_pressure_min_gap_to_target_pct", 40),
        "target_pressure_min_cash_ratio_pct": sizing_cfg.get("target_pressure_min_cash_ratio_pct", 70),
        "target_pressure_allowed_entry_modes": sizing_cfg.get("target_pressure_allowed_entry_modes", ["info_exploratory_probe", "strict_strategy_gate"]),
        "target_pressure_min_entry_mode_win_rate_pct": sizing_cfg.get("target_pressure_min_entry_mode_win_rate_pct", 60),
        "target_pressure_min_entry_mode_return_pct": sizing_cfg.get("target_pressure_min_entry_mode_return_pct", 5),
        "target_pressure_min_info_score": sizing_cfg.get("target_pressure_min_info_score", 50),
        "target_pressure_min_oos_win_rate_pct": sizing_cfg.get("target_pressure_min_oos_win_rate_pct", 50),
        "target_pressure_min_oos_net_return_pct": sizing_cfg.get("target_pressure_min_oos_net_return_pct", 80),
        "target_pressure_min_oos_drawdown_pct": sizing_cfg.get("target_pressure_min_oos_drawdown_pct", -30),
        "target_pressure_min_train_net_return_pct": sizing_cfg.get("target_pressure_min_train_net_return_pct", 20),
        "dynamic_exit_enabled": exit_cfg.get("enabled", True),
        "historical_barrier_replay_enabled": exit_cfg.get("historical_barrier_replay_enabled", True),
        "historical_barrier_interval": exit_cfg.get("historical_barrier_interval", "5m"),
        "historical_barrier_request_timeout_seconds": exit_cfg.get("historical_barrier_request_timeout_seconds", 8),
        "historical_barrier_max_pages": exit_cfg.get("historical_barrier_max_pages", 100),
        "historical_barrier_extra_slippage_bps": exit_cfg.get("historical_barrier_extra_slippage_bps", 12),
        "profit_protection_trigger_pct": exit_cfg.get("profit_protection_trigger_pct", 6.0),
        "profit_trailing_giveback_pct": exit_cfg.get("profit_trailing_giveback_pct", 3.5),
        "profit_break_even_floor_pct": exit_cfg.get("profit_break_even_floor_pct", 0.6),
        "profit_protection_min_hold_minutes": exit_cfg.get("profit_protection_min_hold_minutes", 30),
        "exploratory_profit_protection_enabled": exit_cfg.get("exploratory_profit_protection_enabled", True),
        "exploratory_profit_protection_trigger_pct": exit_cfg.get("exploratory_profit_protection_trigger_pct", 3.0),
        "exploratory_profit_trailing_giveback_pct": exit_cfg.get("exploratory_profit_trailing_giveback_pct", 1.25),
        "exploratory_break_even_floor_pct": exit_cfg.get("exploratory_break_even_floor_pct", 0.5),
        "exploratory_profit_protection_min_hold_minutes": exit_cfg.get("exploratory_profit_protection_min_hold_minutes", 15),
        "short_horizon_profit_protection_trigger_pct": exit_cfg.get("short_horizon_profit_protection_trigger_pct", 3.5),
        "short_horizon_profit_trailing_giveback_pct": exit_cfg.get("short_horizon_profit_trailing_giveback_pct", 1.5),
        "short_horizon_break_even_floor_pct": exit_cfg.get("short_horizon_break_even_floor_pct", 0.4),
        "short_horizon_profit_protection_min_hold_minutes": exit_cfg.get("short_horizon_profit_protection_min_hold_minutes", 15),
        "time_decay_exit_enabled": exit_cfg.get("time_decay_exit_enabled", True),
        "time_decay_exit_hours": exit_cfg.get("time_decay_exit_hours", 96),
        "time_decay_exit_max_pnl_pct": exit_cfg.get("time_decay_exit_max_pnl_pct", 0.0),
        "time_decay_min_info_score": exit_cfg.get("time_decay_min_info_score", 25),
        "exploratory_capital_protection_enabled": exit_cfg.get("exploratory_capital_protection_enabled", True),
        "exploratory_capital_protection_min_hold_minutes": exit_cfg.get("exploratory_capital_protection_min_hold_minutes", 30),
        "exploratory_capital_protection_loss_cut_pct": exit_cfg.get("exploratory_capital_protection_loss_cut_pct", -3.25),
        "exploratory_capital_protection_max_favorable_pnl_pct": exit_cfg.get("exploratory_capital_protection_max_favorable_pnl_pct", 1.0),
        "post_loss_reentry_cooldown_enabled": exit_cfg.get("post_loss_reentry_cooldown_enabled", True),
        "post_loss_reentry_cooldown_hours": exit_cfg.get("post_loss_reentry_cooldown_hours", 6),
        "post_loss_reentry_cooldown_modes": exit_cfg.get("post_loss_reentry_cooldown_modes", ["info_exploratory_probe"]),
        "post_loss_reentry_cooldown_exit_reasons": exit_cfg.get("post_loss_reentry_cooldown_exit_reasons", ["exploratory_capital_protection_loss_cut"]),
        "recent_loss_quality_gate_enabled": recent_loss_quality_cfg.get("enabled", True),
        "recent_loss_quality_gate_lookback_hours": recent_loss_quality_cfg.get("lookback_hours", 48),
        "recent_loss_quality_gate_modes": recent_loss_quality_cfg.get("modes", ["info_exploratory_probe", "validation_probe"]),
        "recent_loss_quality_gate_exit_reasons": recent_loss_quality_cfg.get(
            "exit_reasons",
            ["exploratory_capital_protection_loss_cut", "post_loss_reentry_cooldown_violation", "stop"],
        ),
        "recent_loss_quality_gate_min_info_score": recent_loss_quality_cfg.get("min_info_score", 45),
        "recent_loss_quality_gate_min_oos_trades": recent_loss_quality_cfg.get("min_oos_trades", 8),
        "recent_loss_quality_gate_min_oos_win_rate_pct": recent_loss_quality_cfg.get("min_oos_win_rate_pct", 60),
        "recent_loss_quality_gate_min_oos_net_return_pct": recent_loss_quality_cfg.get("min_oos_net_return_pct", 50),
        "recent_loss_quality_gate_min_oos_drawdown_pct": recent_loss_quality_cfg.get("min_oos_drawdown_pct", -20),
        "recent_loss_quality_gate_min_train_final_capital": recent_loss_quality_cfg.get("min_train_final_capital", paper_cfg.get("initial_capital_usd", 500)),
        "recent_loss_rotation_enabled": cfg.get("paper_recent_loss_rotation", {}).get("enabled", True),
        "recent_loss_rotation_lookback_hours": cfg.get("paper_recent_loss_rotation", {}).get("lookback_hours", 72),
        "recent_loss_rotation_penalty_points_per_loss": cfg.get("paper_recent_loss_rotation", {}).get("penalty_points_per_loss", 35),
        "recent_loss_rotation_max_penalty_points": cfg.get("paper_recent_loss_rotation", {}).get("max_penalty_points", 120),
        "recent_loss_rotation_idle_cash_ratio_pct": cfg.get("paper_recent_loss_rotation", {}).get("idle_cash_ratio_pct", 70),
        "recent_loss_rotation_unfailed_symbol_bonus_points": cfg.get("paper_recent_loss_rotation", {}).get("unfailed_symbol_bonus_points", 18),
        "recent_loss_rotation_prefer_unfailed_symbols_when_cash_idle": cfg.get("paper_recent_loss_rotation", {}).get("prefer_unfailed_symbols_when_cash_idle", True),
        "recent_loss_rotation_exit_reasons": cfg.get("paper_recent_loss_rotation", {}).get(
            "exit_reasons",
            ["stop", "exploratory_capital_protection_loss_cut", "post_loss_reentry_cooldown_violation", "target_sprint_loss_cut", "scale_in_loss_cut"],
        ),
        "recent_strategy_loss_rotation_enabled": cfg.get("paper_recent_strategy_loss_rotation", {}).get("enabled", True),
        "recent_strategy_loss_rotation_lookback_hours": cfg.get("paper_recent_strategy_loss_rotation", {}).get("lookback_hours", 96),
        "recent_strategy_loss_rotation_exact_penalty_points": cfg.get("paper_recent_strategy_loss_rotation", {}).get("exact_family_interval_mode_penalty_points", 45),
        "recent_strategy_loss_rotation_family_interval_penalty_points": cfg.get("paper_recent_strategy_loss_rotation", {}).get("family_interval_penalty_points", 28),
        "recent_strategy_loss_rotation_family_mode_penalty_points": cfg.get("paper_recent_strategy_loss_rotation", {}).get("family_mode_penalty_points", 22),
        "recent_strategy_loss_rotation_family_penalty_points": cfg.get("paper_recent_strategy_loss_rotation", {}).get("family_penalty_points", 10),
        "recent_strategy_loss_rotation_max_penalty_points": cfg.get("paper_recent_strategy_loss_rotation", {}).get("max_penalty_points", 150),
        "recent_strategy_loss_rotation_exit_reasons": cfg.get("paper_recent_strategy_loss_rotation", {}).get(
            "exit_reasons",
            ["stop", "exploratory_capital_protection_loss_cut", "post_loss_reentry_cooldown_violation", "target_sprint_loss_cut", "scale_in_loss_cut"],
        ),
        "short_horizon_time_decay_exit_hours": exit_cfg.get("short_horizon_time_decay_exit_hours", 24),
        "short_horizon_time_decay_exit_max_pnl_pct": exit_cfg.get("short_horizon_time_decay_exit_max_pnl_pct", 1.0),
        "short_horizon_time_decay_min_info_score": exit_cfg.get("short_horizon_time_decay_min_info_score", 30),
        "max_strategies_per_frame": loop_cfg.get("max_strategies_per_frame", 0),
        "scan_budget_enabled": scan_budget_cfg.get("enabled", False),
        "target_max_strategies_per_run": scan_budget_cfg.get("target_max_strategies_per_run", 0),
        "min_strategies_per_frame": scan_budget_cfg.get("min_strategies_per_frame", 0),
        "max_new_positions_per_run": loop_cfg.get("max_new_positions_per_run", 1),
        "candidate_max_per_symbol": loop_cfg.get("candidate_max_per_symbol", 8),
        "candidate_max_per_symbol_interval": loop_cfg.get("candidate_max_per_symbol_interval", 4),
        "scale_in_enabled": cfg.get("paper_scale_in", {}).get("enabled", False) and loop_cfg.get("scale_in_enabled", False),
        "scale_in_max_notional_usd": cfg.get("paper_scale_in", {}).get("max_notional_usd", 25),
        "scale_in_min_info_score": cfg.get("paper_scale_in", {}).get("min_info_score", 45),
        "scale_in_min_existing_pnl_pct": cfg.get("paper_scale_in", {}).get("min_existing_pnl_pct", 4),
        "scale_in_min_existing_max_pnl_pct": cfg.get("paper_scale_in", {}).get("min_existing_max_pnl_pct", 6),
        "scale_in_max_symbol_exposure_pct_of_equity": cfg.get("paper_scale_in", {}).get("max_symbol_exposure_pct_of_equity", 20),
        "scale_in_max_open_positions_per_symbol": cfg.get("paper_scale_in", {}).get("max_open_positions_per_symbol", 2),
        "scale_in_loss_cut_pct": cfg.get("paper_scale_in", {}).get("loss_cut_pct", -7.0),
        "target_sprint_scale_in_enabled": cfg.get("paper_scale_in", {}).get("target_sprint_enabled", False),
        "target_sprint_scale_in_notional_usd": cfg.get("paper_scale_in", {}).get("target_sprint_notional_usd", cfg.get("paper_scale_in", {}).get("max_notional_usd", 25)),
        "target_sprint_min_cash_ratio_pct": cfg.get("paper_scale_in", {}).get("target_sprint_min_cash_ratio_pct", 65),
        "target_sprint_min_info_score": cfg.get("paper_scale_in", {}).get("target_sprint_min_info_score", cfg.get("paper_scale_in", {}).get("min_info_score", 45)),
        "target_sprint_min_best_pnl_pct": cfg.get("paper_scale_in", {}).get("target_sprint_min_best_pnl_pct", cfg.get("paper_scale_in", {}).get("min_existing_pnl_pct", 4)),
        "target_sprint_max_symbol_exposure_pct_of_equity": cfg.get("paper_scale_in", {}).get("target_sprint_max_symbol_exposure_pct_of_equity", cfg.get("paper_scale_in", {}).get("max_symbol_exposure_pct_of_equity", 20)),
        "target_sprint_max_open_positions_per_symbol": cfg.get("paper_scale_in", {}).get("target_sprint_max_open_positions_per_symbol", cfg.get("paper_scale_in", {}).get("max_open_positions_per_symbol", 2)),
        "target_sprint_loss_cut_pct": cfg.get("paper_scale_in", {}).get("target_sprint_loss_cut_pct", -5.5),
        "paper_entry_mode_learning_guard": cfg.get("paper_entry_mode_learning_guard", {}),
        "validation_recovery_plan_gate_enabled": recovery_gate_cfg.get("enabled", True),
        "validation_recovery_plan_block_statuses": recovery_gate_cfg.get("block_statuses", ["retire_from_new_samples", "cooldown_until_retested"]),
        "validation_recovery_plan_block_groups": recovery_gate_cfg.get("block_groups", ["entry_mode", "strategy_family", "interval", "symbol"]),
        "validation_recovery_plan_allow_quality_scout_weak_interval_retest": recovery_gate_cfg.get("allow_quality_scout_weak_interval_retest", True),
        "validation_recovery_plan": {},
        "strategy_recovery_queue_ranking_enabled": strategy_recovery_cfg.get("feed_queue_to_scanner", True),
        "strategy_recovery_queue_allow_symbols_outside_default_universe": strategy_recovery_cfg.get("allow_queue_symbols_outside_default_universe", True),
        "strategy_recovery_queue_max_symbols": strategy_recovery_cfg.get("max_queue_symbols_for_scan", 8),
        "strategy_recovery_queue_symbol_interval_family_bonus_points": strategy_recovery_cfg.get("selection_symbol_interval_family_bonus_points", 28),
        "strategy_recovery_queue_exact_mode_bonus_points": strategy_recovery_cfg.get("selection_exact_mode_bonus_points", 12),
        "strategy_recovery_queue_current_signal_bonus_points": strategy_recovery_cfg.get("selection_current_signal_bonus_points", 8),
        "strategy_recovery_queue_score_scale": strategy_recovery_cfg.get("selection_score_scale", 0.05),
        "strategy_recovery_queue_max_bonus_points": strategy_recovery_cfg.get("selection_max_bonus_points", 55),
        "strategy_recovery_optimizer_state": {},
        "kline_cache_freshness_enabled": cfg.get("kline_cache_freshness", {}).get("enabled", True),
        "kline_cache_max_lag_minutes_by_interval": cfg.get("kline_cache_freshness", {}).get("max_lag_minutes_by_interval", DEFAULT_MAX_KLINE_LAG_MINUTES),
        "kline_fetch_wall_clock_seconds": (
            args.kline_fetch_wall_clock_seconds
            if args.kline_fetch_wall_clock_seconds is not None
            else loop_cfg.get("kline_fetch_wall_clock_seconds", 0)
        ),
        "paper_strategy_overlay": paper_strategy_overlay,
        "paper_testnet_risk_control_contract": cfg.get("paper_testnet_risk_controls", {}),
        "paper_risk_size_multiplier": 1.0,
        "paper_risk_max_new_entry_notional_usd": paper_cfg.get("max_single_trade_notional_usd", 150),
    }
    outside_required_window = args.loop_kind == "sunday" and not in_sunday_window(now)
    if args.enforce_window and not args.allow_outside_window and outside_required_window:
        run_id = f"{local.strftime('%Y%m%d-%H%M')}-{meta['run_label']}-skipped"
        skipped = {
            "run_id": run_id,
            "created_at": iso(now),
            "status": f"skipped_outside_{args.loop_kind}_window",
            "local_time": local.isoformat(),
            "live_orders_enabled": False,
        }
        print(json.dumps(skipped, ensure_ascii=False, indent=2))
        return skipped

    configured_symbols = split_symbol_string(args.symbols or loop_cfg.get("symbols") or DEFAULT_SYMBOLS)
    explicit_symbols = split_symbol_string(args.symbols) if args.symbols else []
    symbols = list(configured_symbols)
    strategy_recovery_state = (
        latest_strategy_recovery_optimizer_payload()
        if strategy_recovery_cfg.get("enabled", True) and strategy_recovery_cfg.get("feed_queue_to_scanner", True)
        else {"enabled": False, "status": "disabled", "eligible_retest_queue": []}
    )
    settings["strategy_recovery_optimizer_state"] = strategy_recovery_state
    if strategy_recovery_cfg.get("allow_queue_symbols_outside_default_universe", True):
        for symbol in strategy_recovery_queue_symbols(
            strategy_recovery_state,
            max_symbols=int(strategy_recovery_cfg.get("max_queue_symbols_for_scan", 8) or 8),
        ):
            if symbol not in symbols:
                symbols.append(symbol)
    intervals = [s.strip() for s in (args.intervals or loop_cfg.get("intervals") or "1h,4h,1d").split(",") if s.strip()]
    run_id = f"{local.strftime('%Y%m%d-%H%M')}-{meta['run_label']}"
    cache_dir = args.cache_dir or str(
        DURABLE_KLINE_CACHE_ROOT / meta["cache_name"] / local.strftime("%Y%m%d")
    )
    ledger_path = Path(args.ledger)
    state_path = ROOT / "experiments" / meta["state_name"]
    initial = float(paper_cfg.get("initial_capital_usd", 500))

    with run_lock(enabled=not args.no_lock and not args.dry_run):
        ledger = load_ledger(ledger_path, initial)
        backfill_legacy_entry_orders(ledger, now)
        dynamic_scan_pool = build_dynamic_scan_pool(
            symbols,
            explicit_symbols,
            ledger,
            settings,
            strategy_recovery_state,
            offline_fixture=args.offline_fixture,
        )
        symbols = list(dynamic_scan_pool.get("selected_symbols") or symbols)
        settings["dynamic_scan_pool_profile"] = dynamic_scan_pool
        market, cg_prices, market_errors = fetch_market(symbols, settings, offline_fixture=args.offline_fixture)
        info_signals = build_info_signals(symbols, market, settings) if not args.offline_fixture else {"rows": [], "by_symbol": {}, "errors": []}
        if args.offline_fixture and args.dry_run and ledger.get("open_positions"):
            reviewed = [
                {
                    "paper_trade_id": pos.get("paper_trade_id"),
                    "symbol": pos.get("symbol"),
                    "status": "skipped_offline_fixture_dry_run",
                    "reason": "offline fixture prices are synthetic and must not be used to review real paper positions",
                }
                for pos in ledger.get("open_positions", [])
            ]
        else:
            barrier_replays, barrier_errors = build_historical_barrier_replays(
                ledger,
                settings,
                now,
                offline_fixture=args.offline_fixture,
            )
            market_errors.extend(barrier_errors)
            reviewed = review_positions(
                ledger,
                market,
                settings,
                now,
                info_signals=info_signals,
                run_id=run_id,
                historical_barrier_replays=barrier_replays,
            )
        latest_evolver_path = ptrc.latest("*paper-strategy-auto-evolver.json")
        latest_evolver = ptrc.read_json(latest_evolver_path, {}) if latest_evolver_path else {}
        paper_risk_control = ptrc.evaluate_risk_state(
            ledger,
            settings.get("paper_testnet_risk_control_contract") or {},
            now,
            paper_strategy_overlay,
            latest_evolver or {},
        )
        settings["paper_risk_size_multiplier"] = paper_risk_control.get("recommended_new_entry_size_multiplier", 0.0)
        settings["paper_risk_max_new_entry_notional_usd"] = paper_risk_control.get("max_new_entry_notional_usd", 0.0)
        settings["paper_risk_control_state"] = paper_risk_control
        capacity_gate = validation_capacity_gate(
            enabled=bool(loop_cfg.get("validation_capacity_gate_enabled", True))
            and not args.ignore_validation_capacity_gate
            and not args.offline_fixture
        )
        settings["validation_recovery_plan"] = capacity_gate.get("validation_recovery_plan") or {}
        kline_prefetch_filter = {
            "status": "not_run",
            "reason": "scan_not_started",
        }
        if paper_risk_control.get("risk_decision") == "block_new_entries":
            scan_symbols = []
            kline_errors = []
            kline_cache_audit = {
                "enabled": False,
                "status": "skipped_paper_risk_control",
                "reason": paper_risk_control.get("triggers")
                or paper_risk_control.get("safety_errors")
                or paper_risk_control.get("integrity_errors")
                or paper_risk_control.get("contract_errors"),
            }
            scan = skipped_capacity_scan(
                {
                    "decision": "skip_new_samples",
                    "reason": "paper_testnet_risk_control_block",
                    "sample_action": "block_new_entries",
                    "recommended_runner_mode": "exit_monitor_only",
                    "failed_gates": paper_risk_control.get("triggers") or [],
                }
            )
            candidates = []
            rejections = [
                {
                    "symbol": "portfolio",
                    "stage": "paper_testnet_risk_control",
                    "reason": "paper_testnet_risk_control_block_new_entries",
                    "details": paper_risk_control,
                }
            ]
        elif capacity_gate.get("decision") == "skip_new_samples":
            scan_symbols = []
            kline_errors = []
            kline_cache_audit = {
                "enabled": False,
                "status": "skipped_capacity_gate",
                "reason": capacity_gate.get("reason"),
            }
            scan = skipped_capacity_scan(capacity_gate)
            candidates = []
            rejections = [
                {
                    "symbol": "portfolio",
                    "stage": "validation_capacity_gate",
                    "reason": "validation_capacity_gate_skip_new_samples",
                    "details": capacity_gate,
                }
            ]
        else:
            scan_symbols = prefilter_scan_symbols(symbols, ledger, info_signals, settings)
            days_by_interval = loop_cfg.get("kline_days_by_interval", {"1h": 90, "4h": 240, "1d": 900})
            cache_dir, kline_errors = fetch_or_reuse_klines(
                scan_symbols,
                intervals,
                cache_dir,
                days_by_interval,
                offline_fixture=args.offline_fixture,
                wall_clock_seconds=settings.get("kline_fetch_wall_clock_seconds", 0),
            )
            kline_cache_audit = audit_kline_cache(
                cache_dir,
                scan_symbols,
                intervals,
                now,
                max_lag_by_interval=settings.get("kline_cache_max_lag_minutes_by_interval"),
            ) if settings.get("kline_cache_freshness_enabled") else {"enabled": False, "status": "disabled"}
            kline_prefetch_filter = {
                "status": "disabled",
                "reason": "kline_cache_freshness_disabled",
            }
            if settings.get("kline_cache_freshness_enabled") and kline_cache_audit.get("status") in {"stale", "missing"}:
                filtered_scan_symbols, kline_prefetch_filter = filter_scan_symbols_by_kline_audit(scan_symbols, kline_cache_audit)
                if filtered_scan_symbols != scan_symbols:
                    scan_symbols = filtered_scan_symbols
                    kline_cache_audit = audit_kline_cache(
                        cache_dir,
                        scan_symbols,
                        intervals,
                        now,
                        max_lag_by_interval=settings.get("kline_cache_max_lag_minutes_by_interval"),
                    )
            if kline_cache_audit.get("status") in {"stale", "missing"}:
                kline_errors.append(
                    {
                        "name": "kline_cache_freshness",
                        "status": "error",
                        "error": f"kline cache {kline_cache_audit.get('status')}",
                        "cache_dir": kline_cache_audit.get("cache_dir"),
                        "stale_count": kline_cache_audit.get("stale_count"),
                        "missing_count": kline_cache_audit.get("missing_count"),
                        "max_lag_minutes": kline_cache_audit.get("max_lag_minutes"),
                        "kline_prefetch_filter": kline_prefetch_filter,
                    }
                )
            scan = scan_current_signals(
                cache_dir,
                scan_symbols,
                intervals,
                initial,
                int(loop_cfg.get("top_train", 25)),
                max_strategies_per_frame=int(settings.get("max_strategies_per_frame", 0) or 0),
                candidate_max_per_symbol=int(settings.get("candidate_max_per_symbol", 8) or 8),
                candidate_max_per_symbol_interval=int(settings.get("candidate_max_per_symbol_interval", 4) or 4),
                target_max_strategies_per_run=int(settings.get("target_max_strategies_per_run", 0) or 0) if settings.get("scan_budget_enabled") else 0,
                min_strategies_per_frame=int(settings.get("min_strategies_per_frame", 0) or 0),
            )
            candidates, rejections = choose_candidates(scan, ledger, market, cg_prices, settings, info_signals, now=now)
        candidate = candidates[0] if candidates else None
        state = load_strategy_state(state_path)
        strategy_version, revision = evolve_strategy(state, candidate, scan, reviewed, run_id, now, strategy_prefix=args.loop_kind)
        opened = None
        opened_positions = []
        open_error = None
        open_errors = []
        for item in candidates:
            item["market"] = market
            item_strategy_version = f"{args.loop_kind}-{local.strftime('%Y%m%d-%H%M')}-{item['symbol']}-{item['interval']}-{item['candidate_key']}"
            opened_item, open_error_item = open_paper_position(ledger, item, settings, now, run_id, item_strategy_version)
            if opened_item:
                opened_positions.append(opened_item)
            if open_error_item:
                open_errors.append({"symbol": item["symbol"], "error": open_error_item})
        opened = opened_positions[0] if opened_positions else None
        open_error = open_errors[0] if open_errors else None
        mark_ledger(ledger)
        reconcile_provisional_monthly_baseline_after_historical_close(ledger, reviewed, now)
        ledger.setdefault("events", []).append(
            {
                "event_type": "paper_equity_snapshot",
                "created_at": iso(now),
                "run_id": run_id,
                "equity_usd": ledger.get("equity_usd"),
                "cash_usd": ledger.get("cash_usd"),
                "open_value_usd": ledger.get("open_value_usd"),
                "net_return_pct": ledger.get("net_return_pct"),
            }
        )
        summary = session_summary(ledger, now)
        target_progress = monthly_double_progress(ledger)
        errors = market_errors + kline_errors + info_signals.get("errors", [])
        optional_warnings = info_signals.get("optional_warnings", []) + info_signals.get("symbol_errors", [])
        data_status = "degraded" if errors else ("verified_with_optional_source_warnings" if optional_warnings else "verified")
        research_overlay = active_research_panel_overlay(
            args.external_agent_outputs_json,
            run_id,
            "subagent research committee is not invoked inside this local paper-loop runner; output is paper-only and requires manual review before real money",
        )
        if research_overlay.get("max_allowed_action") == "conditional_action":
            research_overlay["max_allowed_action"] = "paper_only"
            if isinstance(research_overlay.get("research_panel"), dict):
                research_overlay["research_panel"]["max_allowed_action"] = "paper_only"
                research_overlay["research_panel"]["arbiter_decision"] = (
                    str(research_overlay["research_panel"].get("arbiter_decision") or "")
                    + " Active paper loop caps monitor handoff at paper-only; manual skill must re-check before any real action."
                ).strip()
        run = {
            "run_id": run_id,
            "created_at": iso(now),
            "local_time": local.isoformat(),
            "loop_kind": args.loop_kind,
            "loop_title": meta["title"],
            "live_orders_enabled": False,
            "private_api_keys_used": False,
            "paper_auto_learning_loop": {
                "strategy_version": pso.strategy_version(paper_strategy_overlay),
                "overlay_updated_at": paper_strategy_overlay.get("updated_at"),
                "auto_learning_enabled": paper_strategy_overlay.get("auto_learning_enabled"),
                "overlay_path": str(pso.OVERLAY_PATH.relative_to(WORKSPACE_ROOT)),
                "live_orders_enabled": False,
                "private_api_used": False,
            },
            **research_overlay,
            "data_status": data_status,
            "errors": errors,
            "optional_data_warnings": optional_warnings[:20],
            "reviewed_positions": reviewed,
            "info_signals": info_signals,
            "dynamic_scan_pool": dynamic_scan_pool,
            "market_fetch_budget": next(
                (item for item in errors if item.get("name") == "market_fetch_wall_clock_budget"),
                {
                    "name": "market_fetch_wall_clock_budget",
                    "status": "not_exhausted",
                    "wall_clock_seconds": settings.get("market_fetch_wall_clock_seconds", 0),
                },
            ),
            "scan_symbols": scan_symbols,
            "strategy_recovery_optimizer": strategy_recovery_state,
            "validation_capacity_gate": capacity_gate,
            "paper_testnet_risk_control": paper_risk_control,
            "kline_cache_audit": kline_cache_audit,
            "kline_prefetch_filter": kline_prefetch_filter,
            "kline_prefetch_budget": {
                "wall_clock_seconds": settings.get("kline_fetch_wall_clock_seconds", 0),
                "budget_status": next(
                    (item for item in kline_errors if item.get("name") == "kline_fetch_wall_clock_budget"),
                    {"status": "not_exhausted"},
                ),
            },
            "info_fetch_budget": info_signals.get("budget_status"),
            "scan": scan,
            "selected_candidate": {k: v for k, v in candidate.items() if k != "market"} if candidate else None,
            "selected_candidates": [{k: v for k, v in item.items() if k != "market"} for item in candidates],
            "opened_position": opened,
            "opened_positions": opened_positions,
            "open_error": open_error,
            "open_errors": open_errors,
            "rejections": rejections,
            "strategy_version": strategy_version,
            "strategy_revision": revision,
            "settings": settings,
            "ledger": ledger,
            "session_summary": summary,
            "monthly_double_progress": target_progress,
            "outputs": {},
        }
        date = local.strftime("%Y-%m-%d")
        stamp = local.strftime("%Y%m%d-%H%M")
        report_path = ROOT / "reports" / f"{date}-{meta['report_prefix']}-{local.strftime('%H%M')}.md"
        handoff_path = ROOT / "handoffs" / f"{date}-{meta['report_prefix']}-{local.strftime('%H%M')}-handoff.json"
        experiment_path = ROOT / "experiments" / f"{stamp}-{meta['run_label']}.json"
        paper_path = ROOT / "paper_trades" / f"{date}-{meta['report_prefix']}-{local.strftime('%H%M')}-paper-trades.json"
        run["outputs"] = {
            "report": str(report_path.relative_to(WORKSPACE_ROOT)),
            "handoff": str(handoff_path.relative_to(WORKSPACE_ROOT)),
            "experiment": str(experiment_path.relative_to(WORKSPACE_ROOT)),
            "paper_trades": str(paper_path.relative_to(WORKSPACE_ROOT)),
            "paper_ledger": str(ledger_path.relative_to(WORKSPACE_ROOT)) if ledger_path.is_absolute() and WORKSPACE_ROOT in ledger_path.parents else str(ledger_path),
        }
        handoff = {
            "handoff_id": run_id,
            "created_at": iso(now),
            "source_skill": "active-alpha-paper-monitor",
            "target_skill": "manual-investment-strategy-operator",
            "candidate_type": meta["candidate_type"],
            "live_orders_enabled": False,
            **research_overlay,
            "monitor_recommendation": "paper_only" if opened_positions else "watch",
            "paper_order_status": "paper_opened" if opened_positions else "watch_or_review_only",
            "scan_summary": scan_summary(scan),
            "dynamic_scan_pool": dynamic_scan_pool,
            "selected_candidate": run["selected_candidate"],
            "selected_candidates": run["selected_candidates"],
            "opened_position": opened,
            "opened_positions": opened_positions,
            "paper_portfolio": {
                "cash_usd": ledger.get("cash_usd"),
                "open_value_usd": ledger.get("open_value_usd"),
                "equity_usd": ledger.get("equity_usd"),
                "net_return_pct": ledger.get("net_return_pct"),
                "max_drawdown_pct": ledger.get("max_drawdown_pct"),
            },
            "monthly_double_progress": target_progress,
            "paper_dynamic_sizing": {
                "enabled": settings.get("dynamic_sizing_enabled"),
                "selected_candidate_sizing": (run["selected_candidate"] or {}).get("sizing_decision") if run.get("selected_candidate") else None,
                "selected_candidates_sizing": [item.get("sizing_decision") for item in run.get("selected_candidates", [])],
                "current_cash_ratio_pct": round(float(ledger.get("cash_usd", 0.0)) / float(ledger.get("equity_usd", 1.0)) * 100.0, 4) if ledger.get("equity_usd") else None,
            },
            "validation_capacity_gate": capacity_gate,
            "paper_testnet_risk_control": paper_risk_control,
            "strategy_recovery_optimizer": {
                "status": strategy_recovery_state.get("status"),
                "path": strategy_recovery_state.get("path"),
                "eligible_retest_count": strategy_recovery_state.get("eligible_retest_count"),
                "queued_symbols": strategy_recovery_queue_symbols(strategy_recovery_state, settings.get("strategy_recovery_queue_max_symbols", 8)),
            },
            "kline_cache_audit": kline_cache_audit_summary(kline_cache_audit),
            "kline_prefetch_filter": kline_prefetch_filter,
            "candidate_attribution_summary": candidate_attribution_summary(run),
            "requires_manual_review_before_real_money": True,
        }
        paper_record = {
            "created_at": iso(now),
            "run_id": run_id,
            "live_orders_enabled": False,
            "reviewed_positions": reviewed,
            "new_paper_trade": opened,
            "new_paper_trades": opened_positions,
            "open_error": open_error,
            "open_errors": open_errors,
            "session_summary": summary,
            "monthly_double_progress": target_progress,
            "validation_capacity_gate": capacity_gate,
            "paper_testnet_risk_control": paper_risk_control,
            "strategy_recovery_optimizer": {
                "status": strategy_recovery_state.get("status"),
                "path": strategy_recovery_state.get("path"),
                "eligible_retest_count": strategy_recovery_state.get("eligible_retest_count"),
                "queued_symbols": strategy_recovery_queue_symbols(strategy_recovery_state, settings.get("strategy_recovery_queue_max_symbols", 8)),
            },
            "kline_cache_audit": kline_cache_audit_summary(kline_cache_audit),
            "simulated_api_orders": ledger.get("paper_orders", [])[-10:],
            "ledger_snapshot": ledger,
        }
        save_ledger(ledger_path, ledger, dry_run=args.dry_run)
        write_json(state_path, state, dry_run=args.dry_run)
        write_json(experiment_path, run, dry_run=args.dry_run)
        write_json(handoff_path, handoff, dry_run=args.dry_run)
        write_json(paper_path, paper_record, dry_run=args.dry_run)
        write_text(report_path, render_report(run), dry_run=args.dry_run)
        print(json.dumps(compact_run_output(run) if args.compact_output else run, ensure_ascii=False, indent=2))
        return run


def self_test():
    original_get_json = get_json
    host_calls = []

    def fake_get_json_second_host_ok(url, timeout=25):
        host_calls.append(url)
        if url.startswith("https://api.binance.com"):
            raise TimeoutError("unit primary host timeout")
        return {"unit_url": url, "timeout": timeout}

    globals()["get_json"] = fake_get_json_second_host_ok
    try:
        fallback_payload = binance_spot_get("/api/v3/ticker/24hr", {"symbol": "GOODUSDT"}, timeout=1)
    finally:
        globals()["get_json"] = original_get_json
    assert (
        fallback_payload["unit_url"].startswith("https://data-api.binance.vision")
        and len(host_calls) == 2
        and "symbol=GOODUSDT" in fallback_payload["unit_url"]
    ), (fallback_payload, host_calls)

    globals()["get_json"] = lambda url, timeout=25: (_ for _ in ()).throw(TimeoutError("unit all hosts down"))
    try:
        try:
            binance_spot_get("/api/v3/ticker/24hr", timeout=1)
            all_hosts_failed = False
        except RuntimeError as exc:
            all_hosts_failed = "failed on all hosts" in str(exc)
    finally:
        globals()["get_json"] = original_get_json
    assert all_hosts_failed, "binance_spot_get should surface all-host failures"

    settings = {
        "commission_bps": 10,
        "base_slippage_bps": 8,
        "max_dynamic_impact_bps": 50,
        "min_quote_volume_usd": 5_000_000,
        "min_depth_1pct_usd": 25_000,
        "min_depth_to_notional": 5,
        "max_spread_bps": 35,
    }
    market = {
        "GOODUSDT": {
            "stats": {"quoteVolume": "10000000"},
            "book": {"bidPrice": "99.9", "askPrice": "100.1"},
            "depth": {"asks": [["100.1", "1000"], ["100.5", "1000"]], "bids": [["99.9", "1000"], ["99.5", "1000"]]},
        },
        "THINUSDT": {
            "stats": {"quoteVolume": "1000"},
            "book": {"bidPrice": "99", "askPrice": "102"},
            "depth": {"asks": [["102", "1"]], "bids": [["99", "1"]]},
        },
    }
    good = execution_quote("GOODUSDT", "buy", 150, market, settings)
    thin = execution_quote("THINUSDT", "buy", 150, market, settings)
    barrier_start = parse_now("2026-07-01T00:00:00+00:00")
    barrier_end = barrier_start + timedelta(minutes=15)
    barrier_pos = {
        "paper_trade_id": "paper-barrier-self-test",
        "symbol": "GOODUSDT",
        "paper_entry_mode": "strict_strategy_gate",
        "strategy_family": "5m momentum",
        "strategy_version": "paper-auto-self-test",
        "opened_at": iso(barrier_start),
        "expires_at": iso(barrier_start + timedelta(days=1)),
        "entry_price": 100.0,
        "quantity": 0.24975,
        "notional_usd": 25.0,
        "entry_commission_usd": 0.025,
        "commission_bps": 10.0,
        "stop_price": 90.0,
        "take_profit_price": 120.0,
        "realistic_execution_enabled": True,
        "status": "open",
        "outcome": "pending",
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    barrier_rows = [
        {
            "open_time_ms": int(barrier_start.timestamp() * 1000),
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 101.0,
            "close_time_ms": int((barrier_start + timedelta(minutes=5) - timedelta(milliseconds=1)).timestamp() * 1000),
        },
        {
            "open_time_ms": int((barrier_start + timedelta(minutes=5)).timestamp() * 1000),
            "open": 96.0,
            "high": 100.0,
            "low": 89.0,
            "close": 94.0,
            "close_time_ms": int((barrier_start + timedelta(minutes=10) - timedelta(milliseconds=1)).timestamp() * 1000),
        },
    ]
    barrier_replay = detect_historical_barrier_replay(barrier_pos, barrier_rows, barrier_start, barrier_end)
    assert (
        barrier_replay
        and barrier_replay["exit_reason"] == "stop"
        and barrier_replay["trigger_mark_price"] == 90.0
        and barrier_replay["ambiguous_same_bar"] is False
    ), barrier_replay
    ambiguous_replay = detect_historical_barrier_replay(
        barrier_pos,
        [
            {
                "open_time_ms": int(barrier_start.timestamp() * 1000),
                "open": 100.0,
                "high": 121.0,
                "low": 89.0,
                "close": 101.0,
                "close_time_ms": int((barrier_start + timedelta(minutes=5) - timedelta(milliseconds=1)).timestamp() * 1000),
            }
        ],
        barrier_start,
        barrier_end,
    )
    assert ambiguous_replay["exit_reason"] == "stop" and ambiguous_replay["ambiguous_same_bar"], ambiguous_replay
    barrier_replay["source"] = "self_test_public_kline_fixture"
    barrier_replay["detection_interval"] = "5m"
    barrier_replay["execution_quote"] = historical_replay_execution_quote(
        barrier_pos,
        barrier_replay,
        {**settings, "historical_barrier_extra_slippage_bps": 12.0},
    )
    barrier_ledger = {
        "initial_capital_usd": 100.0,
        "cash_usd": 75.0,
        "open_positions": [dict(barrier_pos)],
        "closed_trades": [],
        "paper_orders": [],
        "events": [],
    }
    barrier_reviewed = review_positions(
        barrier_ledger,
        {},
        {**settings, "dynamic_exit_enabled": True},
        barrier_end,
        info_signals={"by_symbol": {}},
        run_id="self-test-historical-barrier",
        historical_barrier_replays={barrier_pos["paper_trade_id"]: barrier_replay},
    )
    assert (
        len(barrier_ledger["open_positions"]) == 0
        and len(barrier_ledger["closed_trades"]) == 1
        and barrier_ledger["closed_trades"][0]["exit_reason"] == "stop"
        and barrier_ledger["closed_trades"][0]["historical_barrier_replay"]["exit_reason"] == "stop"
        and barrier_reviewed[0]["reconciled_at"] is not None
    ), (barrier_ledger, barrier_reviewed)
    sampled = strategy_grid_for_scan("1h", max_strategies_per_frame=80)
    sampled_families = {s.family for s in sampled}
    duplicate_candidate = {
        "symbol": "AAAUSDT",
        "interval": "1h",
        "strategy": sampled[0].__dict__,
        "stage": "paper_only",
        "train_summary": {},
        "oos_summary": {},
    }
    diverse_candidate = {
        "symbol": "BBBUSDT",
        "interval": "1h",
        "strategy": sampled[1].__dict__,
        "stage": "paper_only",
        "train_summary": {},
        "oos_summary": {},
    }
    diversified = diversify_candidates([duplicate_candidate, duplicate_candidate, diverse_candidate], max_total=3)
    perf_dupe_a = {
        "symbol": "AAAUSDT",
        "interval": "1h",
        "strategy": {"family": "momentum", "lookback": 10},
        "stage": "paper_only",
        "train_summary": {"final_capital": 450.0},
        "oos_summary": {
            "trade_count": 10,
            "win_rate_pct": 60.0,
            "final_capital": 650.0,
            "net_return_pct": 30.0,
            "max_drawdown_pct": -10.0,
        },
    }
    perf_dupe_b = {
        "symbol": "AAAUSDT",
        "interval": "1h",
        "strategy": {"family": "momentum", "lookback": 20},
        "stage": "paper_only",
        "train_summary": {"final_capital": 520.0},
        "oos_summary": {
            "trade_count": 10,
            "win_rate_pct": 60.0,
            "final_capital": 650.0,
            "net_return_pct": 30.0,
            "max_drawdown_pct": -10.0,
        },
    }
    perf_deduped, perf_dedup_summary = dedupe_candidate_equivalents([perf_dupe_a, perf_dupe_b])
    daily_budget = resolve_scan_budget(
        60,
        max_strategies_per_frame=2200,
        target_max_strategies_per_run=75_000,
        min_strategies_per_frame=900,
    )
    fast_budget = resolve_scan_budget(
        26,
        max_strategies_per_frame=650,
        target_max_strategies_per_run=30_000,
        min_strategies_per_frame=400,
    )
    assert good["allow"], good
    assert not thin["allow"], thin
    assert good["net_quote_after_commission_usd"] < good["gross_quote_usd"], good
    assert len(sampled) == 80 and len(sampled_families) >= 6, (len(sampled), sampled_families)
    assert len(diversified) == 2 and {c["symbol"] for c in diversified} == {"AAAUSDT", "BBBUSDT"}, diversified
    assert len(perf_deduped) == 1 and perf_dedup_summary["performance_duplicate_count"] == 1, (
        perf_deduped,
        perf_dedup_summary,
    )
    assert daily_budget["effective_max_strategies_per_frame"] == 1250 and daily_budget["decision"] == "adaptive_budget_applied", daily_budget
    assert fast_budget["effective_max_strategies_per_frame"] == 650 and fast_budget["decision"] == "static_limit_within_budget", fast_budget
    baseline_now = parse_now("2026-06-06T10:00:00+08:00")
    baseline_ledger = {
        "created_at": "2026-05-18T00:00:00+00:00",
        "initial_capital_usd": 500.0,
        "cash_usd": 461.25,
        "equity_usd": 461.25,
        "events": [
            {
                "event_type": "paper_equity_snapshot",
                "created_at": "2026-06-01T01:00:00+00:00",
                "equity_usd": 455.0,
            },
            {
                "event_type": "paper_equity_snapshot",
                "created_at": "2026-06-02T01:00:00+00:00",
                "equity_usd": 461.25,
            },
        ],
    }
    baseline_record = ensure_monthly_goal_baseline(baseline_ledger, now=baseline_now, reason="self_test")
    event_count_after_stamp = len(baseline_ledger["events"])
    second_record = ensure_monthly_goal_baseline(baseline_ledger, now=baseline_now, reason="should_not_overwrite")
    assert (
        baseline_record["month_id"] == "2026-06"
        and baseline_record["month_start_equity_usd"] == 455.0
        and baseline_record["target_equity_usd"] == 910.0
        and baseline_record["baseline_source"] == "first_ledger_event_in_month"
        and second_record == baseline_record
        and len(baseline_ledger["events"]) == event_count_after_stamp
    ), (baseline_record, second_record, baseline_ledger["events"])
    legacy_list_baseline_ledger = {
        "created_at": "2026-05-18T00:00:00+00:00",
        "initial_capital_usd": 500.0,
        "cash_usd": 777.0,
        "equity_usd": 777.0,
        "monthly_goal_baselines": [
            {
                "month_id": "2026-06",
                "month_start_equity_usd": 700.0,
                "target_equity_usd": 1400.0,
                "created_at": "pinned",
            }
        ],
        "events": [],
    }
    legacy_record = ensure_monthly_goal_baseline(legacy_list_baseline_ledger, now=baseline_now, reason="self_test")
    assert (
        isinstance(legacy_list_baseline_ledger["monthly_goal_baselines"], dict)
        and legacy_record["month_start_equity_usd"] == 700.0
        and legacy_record["created_at"] == "pinned"
    ), (legacy_record, legacy_list_baseline_ledger)
    current_month = month_id_for(utc_now())
    with tempfile.TemporaryDirectory(prefix="active_alpha_monthly_baseline_", dir="/private/tmp") as tmp_dir:
        temp_ledger_path = Path(tmp_dir) / "ledger.json"
        pinned = {
            "created_at": iso(utc_now()),
            "initial_capital_usd": 500.0,
            "cash_usd": 777.0,
            "equity_usd": 777.0,
            "monthly_goal_baselines": {
                current_month: {
                    "month_id": current_month,
                    "month_start_equity_usd": 700.0,
                    "target_equity_usd": 1400.0,
                    "created_at": "pinned",
                }
            },
            "events": [],
        }
        save_ledger(temp_ledger_path, pinned, dry_run=False)
        saved_pinned = read_json(temp_ledger_path, {})
        assert saved_pinned["monthly_goal_baselines"][current_month]["created_at"] == "pinned", saved_pinned
        new_ledger = load_ledger(Path(tmp_dir) / "new-ledger.json", 500.0)
        assert (
            current_month in new_ledger["monthly_goal_baselines"]
            and new_ledger["monthly_goal_baselines"][current_month]["month_start_equity_usd"] == 500.0
        ), new_ledger
    pos = {
        "paper_trade_id": "paper-test",
        "symbol": "GOODUSDT",
        "paper_entry_mode": "strict_strategy_gate",
        "opened_at": iso(utc_now() - timedelta(hours=2)),
        "unrealized_pnl_pct": 4.0,
        "max_unrealized_pnl_pct": 9.0,
    }
    exit_reason, exit_detail = dynamic_exit_decision(
        pos,
        utc_now(),
        {
            "dynamic_exit_enabled": True,
            "profit_protection_trigger_pct": 6.0,
            "profit_trailing_giveback_pct": 3.5,
            "profit_break_even_floor_pct": 0.6,
            "profit_protection_min_hold_minutes": 30,
            "time_decay_exit_enabled": True,
        },
    )
    assert exit_reason == "trailing_profit_protection", (exit_reason, exit_detail)
    overlay_exit_pos = {
        "paper_trade_id": "paper-overlay-exit-test",
        "symbol": "GOODUSDT",
        "paper_entry_mode": "strict_strategy_gate",
        "opened_at": iso(utc_now() - timedelta(hours=2)),
        "unrealized_pnl_pct": 0.2,
        "max_unrealized_pnl_pct": 2.4,
    }
    overlay_exit_reason, overlay_exit_detail = dynamic_exit_decision(
        overlay_exit_pos,
        utc_now(),
        {
            "dynamic_exit_enabled": True,
            "profit_protection_trigger_pct": 6.0,
            "profit_trailing_giveback_pct": 3.5,
            "profit_break_even_floor_pct": 0.6,
            "profit_protection_min_hold_minutes": 30,
            "time_decay_exit_enabled": True,
            "paper_strategy_overlay": {
                "strategy_version": "paper-auto-self-test",
                "exit_rules": {
                    "profit_protection": {
                        "status": "paper_ab_testing",
                        "arm_after_mfe_pct": 2.0,
                        "trailing_floor_pct": 0.25,
                        "source_change_id": "pc-self-test-profit-protection",
                    }
                },
            },
        },
    )
    assert (
        overlay_exit_reason == "trailing_profit_protection"
        and overlay_exit_detail["paper_strategy_overlay_exit_applied"] is True
        and overlay_exit_pos["risk_state"]["profit_protection_trigger_pct"] == 2.0
        and overlay_exit_pos["risk_state"]["paper_strategy_overlay_exit_source_change_id"] == "pc-self-test-profit-protection"
    ), (overlay_exit_reason, overlay_exit_detail, overlay_exit_pos.get("risk_state"))
    sprint_loss_exit, sprint_loss_detail = dynamic_exit_decision(
        {
            "paper_trade_id": "paper-sprint-loss",
            "symbol": "GOODUSDT",
            "paper_entry_mode": "winner_scale_in_probe",
            "opened_at": iso(utc_now() - timedelta(hours=1)),
            "unrealized_pnl_pct": -5.8,
            "max_unrealized_pnl_pct": -0.5,
            "scale_in_evidence": {"target_sprint_scale_in": True},
        },
        utc_now(),
        {"dynamic_exit_enabled": True, "target_sprint_loss_cut_pct": -5.5, "scale_in_loss_cut_pct": -7.0},
    )
    assert sprint_loss_exit == "target_sprint_loss_cut" and sprint_loss_detail["loss_cut_pct"] == -5.5, (sprint_loss_exit, sprint_loss_detail)
    scale_loss_exit, scale_loss_detail = dynamic_exit_decision(
        {
            "paper_trade_id": "paper-scale-loss",
            "symbol": "GOODUSDT",
            "paper_entry_mode": "winner_scale_in_probe",
            "opened_at": iso(utc_now() - timedelta(hours=1)),
            "unrealized_pnl_pct": -7.2,
            "max_unrealized_pnl_pct": 1.0,
            "scale_in_evidence": {},
        },
        utc_now(),
        {"dynamic_exit_enabled": True, "target_sprint_loss_cut_pct": -5.5, "scale_in_loss_cut_pct": -7.0},
    )
    assert scale_loss_exit == "scale_in_loss_cut" and scale_loss_detail["loss_cut_pct"] == -7.0, (scale_loss_exit, scale_loss_detail)
    exploratory_loss_exit, exploratory_loss_detail = dynamic_exit_decision(
        {
            "paper_trade_id": "paper-exploratory-loss",
            "symbol": "HOTUSDT",
            "paper_entry_mode": "info_exploratory_probe",
            "opened_at": iso(utc_now() - timedelta(minutes=45)),
            "unrealized_pnl_pct": -3.4,
            "max_unrealized_pnl_pct": 0.2,
        },
        utc_now(),
        {
            "dynamic_exit_enabled": True,
            "exploratory_capital_protection_enabled": True,
            "exploratory_capital_protection_min_hold_minutes": 30,
            "exploratory_capital_protection_loss_cut_pct": -3.25,
            "exploratory_capital_protection_max_favorable_pnl_pct": 1.0,
        },
    )
    assert (
        exploratory_loss_exit == "exploratory_capital_protection_loss_cut"
        and exploratory_loss_detail["loss_cut_pct"] == -3.25
    ), (exploratory_loss_exit, exploratory_loss_detail)
    cooldown_ok, cooldown_detail = post_loss_reentry_cooldown(
        "HOTUSDT",
        "info_exploratory_probe",
        {
            "closed_trades": [
                {
                    "paper_trade_id": "paper-recent-loss",
                    "symbol": "HOTUSDT",
                    "paper_entry_mode": "info_exploratory_probe",
                    "exit_reason": "exploratory_capital_protection_loss_cut",
                    "closed_at": iso(utc_now() - timedelta(hours=1)),
                }
            ]
        },
        {
            "post_loss_reentry_cooldown_enabled": True,
            "post_loss_reentry_cooldown_hours": 6,
            "post_loss_reentry_cooldown_modes": ["info_exploratory_probe"],
            "post_loss_reentry_cooldown_exit_reasons": ["exploratory_capital_protection_loss_cut"],
        },
        utc_now(),
    )
    assert not cooldown_ok and cooldown_detail["decision"] == "block", (cooldown_ok, cooldown_detail)
    elapsed_cooldown_ok, elapsed_cooldown_detail = post_loss_reentry_cooldown(
        "HOTUSDT",
        "info_exploratory_probe",
        {
            "closed_trades": [
                {
                    "paper_trade_id": "paper-old-loss",
                    "symbol": "HOTUSDT",
                    "paper_entry_mode": "info_exploratory_probe",
                    "exit_reason": "exploratory_capital_protection_loss_cut",
                    "closed_at": iso(utc_now() - timedelta(hours=7)),
                }
            ]
        },
        {
            "post_loss_reentry_cooldown_enabled": True,
            "post_loss_reentry_cooldown_hours": 6,
            "post_loss_reentry_cooldown_modes": ["info_exploratory_probe"],
            "post_loss_reentry_cooldown_exit_reasons": ["exploratory_capital_protection_loss_cut"],
        },
        utc_now(),
    )
    assert elapsed_cooldown_ok and elapsed_cooldown_detail["decision"] == "cooldown_elapsed", (elapsed_cooldown_ok, elapsed_cooldown_detail)
    cooldown_violation, violation_detail = open_position_cooldown_violation(
        {
            "paper_trade_id": "paper-bad-reentry",
            "symbol": "HOTUSDT",
            "paper_entry_mode": "info_exploratory_probe",
            "opened_at": iso(utc_now() - timedelta(minutes=30)),
        },
        {
            "closed_trades": [
                {
                    "paper_trade_id": "paper-recent-loss",
                    "symbol": "HOTUSDT",
                    "paper_entry_mode": "info_exploratory_probe",
                    "exit_reason": "exploratory_capital_protection_loss_cut",
                    "closed_at": iso(utc_now() - timedelta(hours=1)),
                }
            ]
        },
        {
            "post_loss_reentry_cooldown_enabled": True,
            "post_loss_reentry_cooldown_hours": 6,
            "post_loss_reentry_cooldown_modes": ["info_exploratory_probe"],
            "post_loss_reentry_cooldown_exit_reasons": ["exploratory_capital_protection_loss_cut"],
        },
        utc_now(),
    )
    assert cooldown_violation and violation_detail["decision"] == "block", (cooldown_violation, violation_detail)
    weak_quality_ok, weak_quality_detail = recent_loss_quality_gate(
        {
            "symbol": "HOTUSDT",
            "train_summary": {"final_capital": 480.0},
            "oos_summary": {
                "trade_count": 7,
                "win_rate_pct": 50.0,
                "net_return_pct": 30.0,
                "max_drawdown_pct": -24.0,
            },
        },
        "info_exploratory_probe",
        {
            "closed_trades": [
                {
                    "paper_trade_id": "paper-recent-loss",
                    "symbol": "HOTUSDT",
                    "paper_entry_mode": "info_exploratory_probe",
                    "exit_reason": "exploratory_capital_protection_loss_cut",
                    "closed_at": iso(utc_now() - timedelta(hours=7)),
                }
            ]
        },
        {
            "recent_loss_quality_gate_enabled": True,
            "recent_loss_quality_gate_lookback_hours": 48,
            "recent_loss_quality_gate_modes": ["info_exploratory_probe", "validation_probe"],
            "recent_loss_quality_gate_exit_reasons": ["exploratory_capital_protection_loss_cut"],
            "recent_loss_quality_gate_min_info_score": 45,
            "recent_loss_quality_gate_min_oos_trades": 8,
            "recent_loss_quality_gate_min_oos_win_rate_pct": 60,
            "recent_loss_quality_gate_min_oos_net_return_pct": 50,
            "recent_loss_quality_gate_min_oos_drawdown_pct": -20,
            "recent_loss_quality_gate_min_train_final_capital": 500,
        },
        {"info_pressure_score": 35},
        utc_now(),
    )
    assert not weak_quality_ok and weak_quality_detail["decision"] == "block_recent_loss_requires_quality_upgrade", (
        weak_quality_ok,
        weak_quality_detail,
    )
    strong_quality_ok, strong_quality_detail = recent_loss_quality_gate(
        {
            "symbol": "HOTUSDT",
            "train_summary": {"final_capital": 560.0},
            "oos_summary": {
                "trade_count": 12,
                "win_rate_pct": 70.0,
                "net_return_pct": 88.0,
                "max_drawdown_pct": -12.0,
            },
        },
        "info_exploratory_probe",
        {
            "closed_trades": [
                {
                    "paper_trade_id": "paper-recent-loss",
                    "symbol": "HOTUSDT",
                    "paper_entry_mode": "info_exploratory_probe",
                    "exit_reason": "exploratory_capital_protection_loss_cut",
                    "closed_at": iso(utc_now() - timedelta(hours=7)),
                }
            ]
        },
        {
            "recent_loss_quality_gate_enabled": True,
            "recent_loss_quality_gate_lookback_hours": 48,
            "recent_loss_quality_gate_modes": ["info_exploratory_probe", "validation_probe"],
            "recent_loss_quality_gate_exit_reasons": ["exploratory_capital_protection_loss_cut"],
            "recent_loss_quality_gate_min_info_score": 45,
            "recent_loss_quality_gate_min_oos_trades": 8,
            "recent_loss_quality_gate_min_oos_win_rate_pct": 60,
            "recent_loss_quality_gate_min_oos_net_return_pct": 50,
            "recent_loss_quality_gate_min_oos_drawdown_pct": -20,
            "recent_loss_quality_gate_min_train_final_capital": 500,
        },
        {"info_pressure_score": 62},
        utc_now(),
    )
    assert strong_quality_ok and strong_quality_detail["decision"] == "allow_quality_upgraded_after_recent_loss", (
        strong_quality_ok,
        strong_quality_detail,
    )
    loss_rotated = rank_candidates_for_selection(
        [
            {
                "symbol": "WLDUSDT",
                "stage": "paper_only",
                "train_summary": {"net_return_pct": 25.0},
                "oos_summary": {
                    "trade_count": 12,
                    "win_rate_pct": 70.0,
                    "net_return_pct": 90.0,
                    "final_capital": 920.0,
                    "max_drawdown_pct": -12.0,
                },
            },
            {
                "symbol": "NEARUSDT",
                "stage": "paper_only",
                "train_summary": {"net_return_pct": 20.0},
                "oos_summary": {
                    "trade_count": 12,
                    "win_rate_pct": 68.0,
                    "net_return_pct": 82.0,
                    "final_capital": 890.0,
                    "max_drawdown_pct": -12.0,
                },
            },
        ],
        {
            "cash_usd": 460.0,
            "equity_usd": 500.0,
            "closed_trades": [
                {
                    "paper_trade_id": "paper-wld-loss",
                    "symbol": "WLDUSDT",
                    "paper_entry_mode": "info_exploratory_probe",
                    "outcome": "failed",
                    "exit_reason": "exploratory_capital_protection_loss_cut",
                    "realized_pnl_usd": -1.0,
                    "realized_pnl_pct": -4.0,
                    "closed_at": iso(utc_now() - timedelta(hours=2)),
                }
            ],
        },
        {
            "WLDUSDT": {"info_pressure_score": 90},
            "NEARUSDT": {"info_pressure_score": 70},
        },
        {
            "recent_loss_rotation_enabled": True,
            "recent_loss_rotation_lookback_hours": 72,
            "recent_loss_rotation_penalty_points_per_loss": 80,
            "recent_loss_rotation_max_penalty_points": 120,
            "recent_loss_rotation_idle_cash_ratio_pct": 70,
            "recent_loss_rotation_unfailed_symbol_bonus_points": 18,
            "recent_loss_rotation_prefer_unfailed_symbols_when_cash_idle": True,
        },
        utc_now(),
    )
    assert loss_rotated[0]["symbol"] == "NEARUSDT" and loss_rotated[1]["selection_rank"]["recent_loss_profile"]["loss_count"] == 1, loss_rotated
    strategy_loss_rotated = rank_candidates_for_selection(
        [
            {
                "symbol": "WLDUSDT",
                "interval": "1h",
                "strategy": {"family": "momentum"},
                "stage": "paper_forward_candidate_current_signal",
                "train_summary": {"net_return_pct": 28.0},
                "validation_summary": {"net_return_pct": 12.0, "win_rate_pct": 60.0},
                "selection_score": 20.0,
                "holdout_used_for_selection": False,
                "oos_summary": {
                    "trade_count": 14,
                    "win_rate_pct": 72.0,
                    "net_return_pct": 95.0,
                    "final_capital": 960.0,
                    "max_drawdown_pct": -10.0,
                },
            },
            {
                "symbol": "NEARUSDT",
                "interval": "1h",
                "strategy": {"family": "breakout"},
                "stage": "paper_forward_candidate_current_signal",
                "train_summary": {"net_return_pct": 24.0},
                "validation_summary": {"net_return_pct": 10.0, "win_rate_pct": 58.0},
                "selection_score": 18.0,
                "holdout_used_for_selection": False,
                "oos_summary": {
                    "trade_count": 14,
                    "win_rate_pct": 69.0,
                    "net_return_pct": 86.0,
                    "final_capital": 910.0,
                    "max_drawdown_pct": -12.0,
                },
            },
        ],
        {
            "cash_usd": 460.0,
            "equity_usd": 500.0,
            "closed_trades": [
                {
                    "paper_trade_id": "paper-other-momentum-loss",
                    "symbol": "OTHERUSDT",
                    "strategy_family": "1h momentum",
                    "paper_entry_mode": "strict_strategy_gate",
                    "outcome": "failed",
                    "exit_reason": "stop",
                    "realized_pnl_usd": -5.0,
                    "realized_pnl_pct": -8.0,
                    "closed_at": iso(utc_now() - timedelta(hours=3)),
                }
            ],
        },
        {
            "WLDUSDT": {"info_pressure_score": 85},
            "NEARUSDT": {"info_pressure_score": 72},
        },
        {
            "recent_loss_rotation_enabled": True,
            "recent_loss_rotation_lookback_hours": 72,
            "recent_loss_rotation_penalty_points_per_loss": 35,
            "recent_loss_rotation_max_penalty_points": 120,
            "recent_loss_rotation_idle_cash_ratio_pct": 70,
            "recent_loss_rotation_unfailed_symbol_bonus_points": 0,
            "recent_loss_rotation_prefer_unfailed_symbols_when_cash_idle": True,
            "recent_strategy_loss_rotation_enabled": True,
            "recent_strategy_loss_rotation_lookback_hours": 96,
            "recent_strategy_loss_rotation_exact_penalty_points": 160,
            "recent_strategy_loss_rotation_family_interval_penalty_points": 80,
            "recent_strategy_loss_rotation_family_mode_penalty_points": 60,
            "recent_strategy_loss_rotation_family_penalty_points": 20,
            "recent_strategy_loss_rotation_max_penalty_points": 180,
        },
        utc_now(),
    )
    assert (
        strategy_loss_rotated[0]["symbol"] == "NEARUSDT"
        and strategy_loss_rotated[1]["selection_rank"]["recent_strategy_loss_profile"]["loss_count"] == 1
        and strategy_loss_rotated[1]["selection_rank"]["recent_strategy_loss_profile"]["penalty_points"] == 160
    ), strategy_loss_rotated
    attribution_run = {
        "selected_candidates": [],
        "scan": {"top_candidates": strategy_loss_rotated},
    }
    attribution_rows = candidate_attribution_summary(attribution_run, limit=2)
    attribution_table = render_candidate_attribution_table(attribution_run)
    assert (
        attribution_rows
        and attribution_rows[1]["strategy_loss_penalty_points"] == 160
        and any("Strategy Penalty" in line for line in attribution_table)
    ), (attribution_rows, attribution_table)
    recovery_plan = {
        "new_sample_policy": "no_new_samples_from_retired_or_cooldown_groups",
        "retired_entry_modes": [
            {"name": "validation_probe", "status": "retire_from_new_samples", "reason": "unit retired mode"}
        ],
        "cooldown_entry_modes": [],
        "eligible_entry_modes": [
            {"name": "info_exploratory_probe", "status": "eligible_small_paper_only", "closed_count": 9}
        ],
        "retired_strategy_families": [
            {"name": "1d relative_strength", "status": "retire_from_new_samples", "reason": "unit retired family"}
        ],
        "cooldown_strategy_families": [],
        "eligible_strategy_families": [
            {"name": "1d momentum", "status": "eligible_small_paper_only", "closed_count": 4}
        ],
        "weak_intervals": [],
        "weak_symbols": [],
    }
    recovery_block_ok, recovery_block_detail = validation_recovery_plan_gate(
        {"symbol": "GOODUSDT", "interval": "1d", "strategy": {"family": "momentum"}},
        "validation_probe",
        {
            "validation_recovery_plan_gate_enabled": True,
            "validation_recovery_plan": recovery_plan,
            "validation_recovery_plan_block_statuses": ["retire_from_new_samples", "cooldown_until_retested"],
            "validation_recovery_plan_block_groups": ["entry_mode", "strategy_family", "interval", "symbol"],
        },
    )
    assert not recovery_block_ok and recovery_block_detail["matched_group"] == "entry_mode", recovery_block_detail
    recovery_family_ok, recovery_family_detail = validation_recovery_plan_gate(
        {"symbol": "BADUSDT", "interval": "1d", "strategy": {"family": "relative_strength"}},
        "info_exploratory_probe",
        {
            "validation_recovery_plan_gate_enabled": True,
            "validation_recovery_plan": recovery_plan,
            "validation_recovery_plan_block_statuses": ["retire_from_new_samples", "cooldown_until_retested"],
            "validation_recovery_plan_block_groups": ["entry_mode", "strategy_family", "interval", "symbol"],
        },
    )
    assert not recovery_family_ok and recovery_family_detail["matched_group"] == "strategy_family", recovery_family_detail
    recovery_allow_ok, recovery_allow_detail = validation_recovery_plan_gate(
        {"symbol": "GOODUSDT", "interval": "1d", "strategy": {"family": "momentum"}},
        "info_exploratory_probe",
        {
            "validation_recovery_plan_gate_enabled": True,
            "validation_recovery_plan": recovery_plan,
            "validation_recovery_plan_block_statuses": ["retire_from_new_samples", "cooldown_until_retested"],
            "validation_recovery_plan_block_groups": ["entry_mode", "strategy_family", "interval", "symbol"],
        },
    )
    assert recovery_allow_ok and recovery_allow_detail["decision"] == "allow_validation_recovery_plan", recovery_allow_detail
    weak_interval_plan = {
        "new_sample_policy": "no_new_samples_from_retired_or_cooldown_groups",
        "retired_entry_modes": [],
        "cooldown_entry_modes": [],
        "retired_strategy_families": [],
        "cooldown_strategy_families": [],
        "weak_intervals": [
            {"name": "1h", "status": "cooldown_until_retested", "reason": "unit weak interval"}
        ],
        "weak_symbols": [
            {"name": "BADUSDT", "status": "cooldown_until_retested", "reason": "unit weak symbol"}
        ],
    }
    quality_scout_interval_ok, quality_scout_interval_detail = validation_recovery_plan_gate(
        {"symbol": "GOODUSDT", "interval": "1h", "strategy": {"family": "momentum"}},
        "current_signal_quality_scout_probe",
        {
            "validation_recovery_plan_gate_enabled": True,
            "validation_recovery_plan": weak_interval_plan,
            "validation_recovery_plan_block_statuses": ["retire_from_new_samples", "cooldown_until_retested"],
            "validation_recovery_plan_block_groups": ["entry_mode", "strategy_family", "interval", "symbol"],
            "validation_recovery_plan_allow_quality_scout_weak_interval_retest": True,
        },
    )
    assert (
        quality_scout_interval_ok
        and quality_scout_interval_detail["decision"] == "allow_quality_scout_weak_interval_retest"
    ), quality_scout_interval_detail
    quality_scout_symbol_ok, quality_scout_symbol_detail = validation_recovery_plan_gate(
        {"symbol": "BADUSDT", "interval": "1h", "strategy": {"family": "momentum"}},
        "current_signal_quality_scout_probe",
        {
            "validation_recovery_plan_gate_enabled": True,
            "validation_recovery_plan": weak_interval_plan,
            "validation_recovery_plan_block_statuses": ["retire_from_new_samples", "cooldown_until_retested"],
            "validation_recovery_plan_block_groups": ["entry_mode", "strategy_family", "interval", "symbol"],
            "validation_recovery_plan_allow_quality_scout_weak_interval_retest": True,
        },
    )
    assert not quality_scout_symbol_ok and quality_scout_symbol_detail["matched_group"] == "symbol", quality_scout_symbol_detail
    recovery_scan = {
        "top_candidates": [
            {
                "symbol": "GOODUSDT",
                "interval": "1d",
                "strategy": {"family": "momentum", "stop_pct": -8.0, "take_pct": 20.0},
                "stage": "paper_only",
                "train_summary": {"final_capital": 560.0, "net_return_pct": 20.0},
                "oos_summary": {
                    "trade_count": 12,
                    "win_rate_pct": 65.0,
                    "final_capital": 720.0,
                    "net_return_pct": 44.0,
                    "max_drawdown_pct": -10.0,
                },
            }
        ]
    }
    recovery_candidates, recovery_rejections = choose_candidates(
        recovery_scan,
        {"cash_usd": 450.0, "equity_usd": 500.0, "open_positions": [], "closed_trades": []},
        market,
        {},
        {
            "max_open_positions": 3,
            "max_new_positions_per_run": 1,
            "default_initial_capital_usd": 500.0,
            "validation_probe_enabled": True,
            "validation_probe_allowed_stages": ["paper_only"],
            "validation_probe_min_oos_trades": 10,
            "validation_probe_min_oos_win_rate_pct": 50.0,
            "validation_probe_min_oos_final_capital": 500.0,
            "validation_probe_min_oos_net_return_pct": 0.0,
            "validation_probe_min_oos_drawdown_pct": -35.0,
            "validation_probe_min_train_final_capital": 0.0,
            "exploratory_probe_enabled": False,
            "exploratory_allowed_stages": [],
            "validation_recovery_plan_gate_enabled": True,
            "validation_recovery_plan": recovery_plan,
            "validation_recovery_plan_block_statuses": ["retire_from_new_samples", "cooldown_until_retested"],
            "validation_recovery_plan_block_groups": ["entry_mode", "strategy_family", "interval", "symbol"],
        },
        {"by_symbol": {"GOODUSDT": {"info_pressure_score": 0}}},
        now=utc_now(),
    )
    assert not recovery_candidates and recovery_rejections[0]["reason"] == "validation_recovery_plan_block", (
        recovery_candidates,
        recovery_rejections,
    )
    recovery_queue_state = {
        "enabled": True,
        "status": "ok",
        "path": "active-alpha-paper-monitor/experiments/test-strategy-recovery-optimizer.json",
        "eligible_retest_queue": [
            {
                "symbol": "GOODUSDT",
                "interval": "1d",
                "strategy_family": "momentum",
                "paper_entry_mode": "strict_strategy_gate",
                "stage": "target_research_pass_current_signal",
                "current_signal": True,
                "score": 120.0,
            }
        ],
    }
    queue_settings = {
        "strategy_recovery_queue_ranking_enabled": True,
        "strategy_recovery_optimizer_state": recovery_queue_state,
        "strategy_recovery_queue_symbol_interval_family_bonus_points": 28,
        "strategy_recovery_queue_exact_mode_bonus_points": 12,
        "strategy_recovery_queue_current_signal_bonus_points": 8,
        "strategy_recovery_queue_score_scale": 0.05,
        "strategy_recovery_queue_max_bonus_points": 55,
        "strategy_recovery_queue_allow_symbols_outside_default_universe": True,
        "strategy_recovery_queue_max_symbols": 8,
        "validation_probe_enabled": False,
        "exploratory_probe_enabled": False,
        "exploratory_allowed_stages": [],
    }
    queued_candidate = {
        "symbol": "GOODUSDT",
        "interval": "1d",
        "strategy": {"family": "momentum"},
        "stage": "target_research_pass_current_signal",
        "train_summary": {"net_return_pct": 50.0, "final_capital": 750.0},
        "oos_summary": {"trade_count": 12, "win_rate_pct": 65.0, "final_capital": 900.0, "net_return_pct": 80.0, "max_drawdown_pct": -12.0},
    }
    queue_profile = strategy_recovery_queue_profile(queued_candidate, queue_settings, {"info_pressure_score": 0})
    assert queue_profile["decision"] == "matched_recovery_retest_queue" and queue_profile["bonus_points"] > 0, queue_profile
    prefiltered = prefilter_scan_symbols(["BTCUSDT"], {"open_positions": []}, {"rows": []}, queue_settings)
    assert "GOODUSDT" in prefiltered, prefiltered
    ranked_with_queue = candidate_selection_rank(
        queued_candidate,
        {"cash_usd": 450.0, "equity_usd": 500.0, "open_positions": [], "closed_trades": []},
        {"GOODUSDT": {"info_pressure_score": 0}},
        queue_settings,
        utc_now(),
    )
    assert ranked_with_queue["strategy_recovery_queue_bonus_points"] > 0, ranked_with_queue
    with tempfile.TemporaryDirectory(prefix="active_alpha_kline_audit_") as tmp_cache:
        now_for_audit = utc_now()
        interval_ms = 60 * 60_000
        fresh_t = int((now_for_audit - timedelta(minutes=90)).timestamp() * 1000)
        stale_t = int((now_for_audit - timedelta(hours=10)).timestamp() * 1000)
        open_t = int((now_for_audit - timedelta(minutes=5)).timestamp() * 1000)
        fresh_rows = [{"t": fresh_t, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 1.0, "ct": fresh_t + interval_ms - 1, "qv": 1.0, "n": 1}]
        stale_rows = [{"t": stale_t, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 1.0, "ct": stale_t + interval_ms - 1, "qv": 1.0, "n": 1}]
        open_rows = [{"t": open_t, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 1.0, "ct": open_t + interval_ms - 1, "qv": 1.0, "n": 1}]
        Path(tmp_cache, f"FRESHUSDT_1h_{fresh_t}_{fresh_t}.json").write_text(json.dumps(fresh_rows), encoding="utf-8")
        Path(tmp_cache, f"STALEUSDT_1h_{stale_t}_{stale_t}.json").write_text(json.dumps(stale_rows), encoding="utf-8")
        Path(tmp_cache, f"OPENUSDT_1h_{open_t}_{open_t}.json").write_text(json.dumps(open_rows), encoding="utf-8")
        cache_audit = audit_kline_cache(
            tmp_cache,
            ["FRESHUSDT", "STALEUSDT", "OPENUSDT", "MISSINGUSDT"],
            ["1h"],
            now_for_audit,
            max_lag_by_interval={"1h": 240},
        )
        assert (
            cache_audit["status"] == "missing"
            and cache_audit["verified_count"] == 2
            and cache_audit["stale_count"] == 1
            and cache_audit["missing_count"] == 1
            and cache_audit["open_bar_count"] == 1
            and cache_audit["latest_close_at"] <= now_for_audit.isoformat()
        ), cache_audit
    missing_context_open, missing_context_error = open_paper_position(
        {"cash_usd": 100.0, "open_positions": [], "closed_trades": [], "paper_orders": []},
        {},
        {"dynamic_scan_pool_profile": {}},
        utc_now(),
        "self-test-missing-context",
        "paper-auto-self-test",
    )
    assert missing_context_open is None and str(missing_context_error).startswith("market_context_incomplete:"), (
        missing_context_open,
        missing_context_error,
    )
    short_pos = {
        "paper_trade_id": "paper-short-test",
        "symbol": "FASTUSDT",
        "paper_entry_mode": "info_exploratory_probe",
        "strategy_family": "15m squeeze_breakout",
        "opened_at": iso(utc_now() - timedelta(minutes=45)),
        "unrealized_pnl_pct": 2.0,
        "max_unrealized_pnl_pct": 4.0,
    }
    short_exit, short_detail = dynamic_exit_decision(
        short_pos,
        utc_now(),
        {
            "dynamic_exit_enabled": True,
            "short_horizon_profit_protection_trigger_pct": 3.5,
            "short_horizon_profit_trailing_giveback_pct": 1.5,
            "short_horizon_break_even_floor_pct": 0.4,
            "short_horizon_profit_protection_min_hold_minutes": 15,
            "time_decay_exit_enabled": True,
        },
    )
    assert short_exit == "trailing_profit_protection" and short_detail["trailing_floor_pct"] == 2.5, (short_exit, short_detail)
    exploratory_profit_pos = {
        "paper_trade_id": "paper-exploratory-profit-test",
        "symbol": "HOTUSDT",
        "paper_entry_mode": "info_exploratory_probe",
        "strategy_family": "1h momentum",
        "opened_at": iso(utc_now() - timedelta(minutes=45)),
        "unrealized_pnl_pct": 2.2,
        "max_unrealized_pnl_pct": 3.6,
    }
    exploratory_profit_exit, exploratory_profit_detail = dynamic_exit_decision(
        exploratory_profit_pos,
        utc_now(),
        {
            "dynamic_exit_enabled": True,
            "exploratory_profit_protection_enabled": True,
            "exploratory_profit_protection_trigger_pct": 3.0,
            "exploratory_profit_trailing_giveback_pct": 1.25,
            "exploratory_break_even_floor_pct": 0.5,
            "exploratory_profit_protection_min_hold_minutes": 15,
            "time_decay_exit_enabled": True,
        },
    )
    assert (
        exploratory_profit_exit == "trailing_profit_protection"
        and exploratory_profit_detail["trailing_floor_pct"] == 2.35
        and exploratory_profit_pos["risk_state"]["exploratory_profit_protection"]
    ), (exploratory_profit_exit, exploratory_profit_detail, exploratory_profit_pos)
    pool_ranked = [
        {"symbol": "WLDUSDT", "bucket": "high_beta_ai", "score": 99, "social_long_score": 5, "social_risk_score": 0},
        {"symbol": "SUIUSDT", "bucket": "high_beta_l1", "score": 97, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "INJUSDT", "bucket": "high_beta_defi", "score": 95, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "FETUSDT", "bucket": "high_beta_ai", "score": 93, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "BTCUSDT", "bucket": "core_store_of_value", "score": 90, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "ETHUSDT", "bucket": "core_yield_beta", "score": 88, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "BNBUSDT", "bucket": "core_exchange", "score": 86, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "TRXUSDT", "bucket": "defensive_cashflow_chain", "score": 84, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "LINKUSDT", "bucket": "infrastructure", "score": 82, "social_long_score": 2, "social_risk_score": 0},
        {"symbol": "AAVEUSDT", "bucket": "defi_bluechip", "score": 80, "social_long_score": 0, "social_risk_score": 0},
        {"symbol": "DOGEUSDT", "bucket": "meme_liquid", "score": 78, "social_long_score": 3, "social_risk_score": 0},
        {"symbol": "PEPEUSDT", "bucket": "meme_liquid", "score": 76, "social_long_score": 0, "social_risk_score": 0},
    ]
    risk_off_pool, risk_off_policy = select_symbols_by_dynamic_pool_policy(
        pool_ranked,
        [],
        [],
        [],
        12,
        {"market_regime": "risk_off_rebound_watch", "sentiment_state": "neutral_or_missing_social"},
    )
    risk_on_pool, risk_on_policy = select_symbols_by_dynamic_pool_policy(
        pool_ranked,
        [],
        [],
        [],
        12,
        {"market_regime": "risk_on_momentum", "sentiment_state": "positive_catalyst_cluster"},
    )
    risk_alert_pool, risk_alert_policy = select_symbols_by_dynamic_pool_policy(
        pool_ranked,
        [],
        [],
        [],
        12,
        {"market_regime": "risk_on_momentum", "sentiment_state": "risk_alert_cluster"},
    )
    assert (
        risk_off_policy["selected_counts"]["core_defensive"] >= 3
        and risk_off_policy["selected_counts"]["high_beta"] <= 3
        and "BTCUSDT" in risk_off_pool
    ), (risk_off_pool, risk_off_policy)
    assert risk_on_policy["selected_counts"]["high_beta"] >= 5, (risk_on_pool, risk_on_policy)
    assert (
        risk_alert_policy["selected_counts"]["high_beta"] <= 3
        and risk_alert_policy["selected_counts"]["core_defensive"] >= 4
        and risk_alert_policy["sentiment_overlay"] == "risk_alert_contract_high_beta_and_raise_core_floor"
    ), (risk_alert_pool, risk_alert_policy)
    assert dynamic_symbol_bucket("BICOUSDT", 41.0, 34_000_000, 172_000) == "high_beta_dynamic"
    assert dynamic_pool_family("high_beta_dynamic") == "high_beta"
    assert not is_dynamic_pool_eligible_usdt_symbol("RLUSDUSDT")
    budget_test_settings = {
        "market_fetch_wall_clock_seconds": 1,
        "market_fetch_min_request_start_seconds": 5,
        "market_request_timeout_seconds": 1,
        "market_fetch_attempts": 1,
        "info_fetch_wall_clock_seconds": 1,
        "info_fetch_min_request_start_seconds": 5,
        "info_request_timeout_seconds": 1,
    }
    budget_market, budget_cg_prices, budget_errors = fetch_market(["BTCUSDT", "ETHUSDT"], budget_test_settings)
    budget_info = build_info_signals(["BTCUSDT", "ETHUSDT"], budget_market, budget_test_settings)
    assert any(
        item.get("name") == "market_fetch_wall_clock_budget" and item.get("status") == "budget_exhausted"
        for item in budget_errors
    ), budget_errors
    assert budget_cg_prices == {}, budget_cg_prices
    assert any(
        item.get("name") == "coingecko_simple_price" and item.get("status") == "skipped_budget"
        for item in budget_errors
    ), budget_errors
    assert budget_info.get("budget_status", {}).get("status") == "budget_exhausted", budget_info
    assert all(
        item.get("status") == "skipped_budget"
        for item in budget_info.get("symbol_errors", [])
    ), budget_info
    ledger = {"paper_orders": []}
    order = record_paper_order(ledger, utc_now(), "BUY", "GOODUSDT", good, 1.0, 100.0, "paper-test", "self-test", "unit_test")
    assert order["simulated_api"] and not order["live_orders_enabled"], order
    sizing_candidate = {
        "paper_entry_mode": "info_exploratory_probe",
        "info_signal": {"info_pressure_score": 45},
        "oos_summary": {"win_rate_pct": 75.0, "final_capital": 900.0, "max_drawdown_pct": -8.0},
        "train_summary": {"net_return_pct": -5.0},
    }
    sizing = candidate_sizing_decision(
        sizing_candidate,
        {"cash_usd": 450.0, "equity_usd": 500.0, "open_positions": []},
        {
            "dynamic_sizing_enabled": True,
            "min_trade_notional_usd": 25.0,
            "mid_probe_notional_usd": 50.0,
            "accelerated_probe_notional_usd": 75.0,
            "max_single_position_pct_of_equity": 30.0,
            "max_total_open_exposure_pct_of_equity": 75.0,
            "min_cash_reserve_usd": 50.0,
            "max_single_trade_notional_usd": 150.0,
            "high_cash_ratio_threshold_pct": 70.0,
            "mid_probe_min_info_score": 30.0,
            "mid_probe_min_oos_win_rate_pct": 60.0,
            "mid_probe_min_oos_final_capital": 650.0,
            "mid_probe_min_oos_drawdown_pct": -25.0,
            "accelerated_probe_min_info_score": 40.0,
            "accelerated_probe_min_oos_win_rate_pct": 70.0,
            "accelerated_probe_min_oos_final_capital": 800.0,
            "accelerated_probe_min_oos_drawdown_pct": -15.0,
        },
    )
    assert sizing["tier"] == "accelerated_probe" and sizing["planned_notional_usd"] == 75.0, sizing
    risk_reduced_sizing = candidate_sizing_decision(
        sizing_candidate,
        {"cash_usd": 450.0, "equity_usd": 500.0, "open_positions": []},
        {
            "dynamic_sizing_enabled": True,
            "min_trade_notional_usd": 25.0,
            "mid_probe_notional_usd": 50.0,
            "accelerated_probe_notional_usd": 75.0,
            "max_single_position_pct_of_equity": 30.0,
            "max_total_open_exposure_pct_of_equity": 75.0,
            "min_cash_reserve_usd": 50.0,
            "max_single_trade_notional_usd": 150.0,
            "high_cash_ratio_threshold_pct": 70.0,
            "mid_probe_min_info_score": 30.0,
            "mid_probe_min_oos_win_rate_pct": 60.0,
            "mid_probe_min_oos_final_capital": 650.0,
            "mid_probe_min_oos_drawdown_pct": -25.0,
            "accelerated_probe_min_info_score": 40.0,
            "accelerated_probe_min_oos_win_rate_pct": 70.0,
            "accelerated_probe_min_oos_final_capital": 800.0,
            "accelerated_probe_min_oos_drawdown_pct": -15.0,
            "paper_risk_size_multiplier": 0.5,
            "paper_risk_max_new_entry_notional_usd": 40.0,
        },
    )
    assert risk_reduced_sizing["planned_notional_usd"] == 37.5, risk_reduced_sizing
    assert "portfolio_risk_control_size_reduction" in risk_reduced_sizing["reason"], risk_reduced_sizing
    validation_sizing = candidate_sizing_decision(
        {
            "paper_entry_mode": "validation_probe",
            "info_signal": {"info_pressure_score": 0},
            "oos_summary": {"win_rate_pct": 55.0, "final_capital": 520.0, "max_drawdown_pct": -20.0},
            "train_summary": {"net_return_pct": 1.0},
        },
        {"cash_usd": 450.0, "equity_usd": 500.0, "open_positions": []},
        {
            "dynamic_sizing_enabled": True,
            "min_trade_notional_usd": 25.0,
            "validation_probe_notional_usd": 25.0,
            "max_single_position_pct_of_equity": 30.0,
            "max_total_open_exposure_pct_of_equity": 75.0,
            "min_cash_reserve_usd": 50.0,
            "max_single_trade_notional_usd": 150.0,
        },
    )
    assert validation_sizing["tier"] == "validation_probe" and validation_sizing["planned_notional_usd"] == 25.0, validation_sizing
    pressure_sizing = candidate_sizing_decision(
        {
            "paper_entry_mode": "info_exploratory_probe",
            "info_signal": {"info_pressure_score": 55},
            "oos_summary": {"win_rate_pct": 55.0, "final_capital": 950.0, "net_return_pct": 120.0, "max_drawdown_pct": -18.0},
            "train_summary": {"net_return_pct": 35.0},
            "entry_mode_learning_gate": {
                "decision": "allow",
                "stats": {"win_rate_pct": 70.0, "realized_return_on_notional_pct": 9.0},
            },
        },
        {"initial_capital_usd": 500.0, "cash_usd": 450.0, "equity_usd": 500.0, "open_positions": []},
        {
            "dynamic_sizing_enabled": True,
            "min_trade_notional_usd": 25.0,
            "mid_probe_notional_usd": 50.0,
            "accelerated_probe_notional_usd": 75.0,
            "max_single_position_pct_of_equity": 30.0,
            "max_total_open_exposure_pct_of_equity": 75.0,
            "min_cash_reserve_usd": 50.0,
            "max_single_trade_notional_usd": 150.0,
            "high_cash_ratio_threshold_pct": 95.0,
            "mid_probe_min_info_score": 60.0,
            "mid_probe_min_oos_win_rate_pct": 60.0,
            "mid_probe_min_oos_final_capital": 1000.0,
            "mid_probe_min_oos_drawdown_pct": -15.0,
            "accelerated_probe_min_info_score": 70.0,
            "accelerated_probe_min_oos_win_rate_pct": 70.0,
            "accelerated_probe_min_oos_final_capital": 1200.0,
            "accelerated_probe_min_oos_drawdown_pct": -10.0,
            "target_pressure_enabled": True,
            "target_pressure_notional_usd": 75.0,
            "target_pressure_min_gap_to_target_pct": 40.0,
            "target_pressure_min_cash_ratio_pct": 70.0,
            "target_pressure_allowed_entry_modes": ["info_exploratory_probe"],
            "target_pressure_min_entry_mode_win_rate_pct": 60.0,
            "target_pressure_min_entry_mode_return_pct": 5.0,
            "target_pressure_min_info_score": 50.0,
            "target_pressure_min_oos_win_rate_pct": 50.0,
            "target_pressure_min_oos_net_return_pct": 80.0,
            "target_pressure_min_oos_drawdown_pct": -30.0,
            "target_pressure_min_train_net_return_pct": 20.0,
        },
    )
    assert pressure_sizing["tier"] == "target_pressure_min_probe" and pressure_sizing["planned_notional_usd"] == 75.0, pressure_sizing
    scale_candidate = {"symbol": "GOODUSDT"}
    scale_ok, scale_reason, scale_details = scale_in_eligibility(
        scale_candidate,
        {
            "cash_usd": 400.0,
            "equity_usd": 500.0,
            "open_positions": [
                {
                    "symbol": "GOODUSDT",
                    "paper_trade_id": "paper-good",
                    "notional_usd": 25.0,
                    "unrealized_pnl_pct": 8.0,
                    "max_unrealized_pnl_pct": 10.0,
                    "risk_state": {"profit_protection_armed": True},
                }
            ],
        },
        {
            "scale_in_enabled": True,
            "scale_in_min_info_score": 45.0,
            "scale_in_min_existing_pnl_pct": 4.0,
            "scale_in_min_existing_max_pnl_pct": 6.0,
            "scale_in_max_symbol_exposure_pct_of_equity": 20.0,
            "scale_in_max_open_positions_per_symbol": 2,
            "min_trade_notional_usd": 25.0,
        },
        {"info_pressure_score": 50.0},
    )
    assert scale_ok and scale_reason == "scale_in_allowed" and scale_details["protection_armed"], (scale_ok, scale_reason, scale_details)
    sprint_ok, sprint_reason, sprint_details = scale_in_eligibility(
        scale_candidate,
        {
            "cash_usd": 360.0,
            "equity_usd": 500.0,
            "open_positions": [
                {
                    "symbol": "GOODUSDT",
                    "paper_trade_id": "paper-good-1",
                    "notional_usd": 50.0,
                    "unrealized_pnl_pct": 8.0,
                    "max_unrealized_pnl_pct": 10.0,
                    "risk_state": {"profit_protection_armed": True},
                },
                {
                    "symbol": "GOODUSDT",
                    "paper_trade_id": "paper-good-2",
                    "notional_usd": 50.0,
                    "unrealized_pnl_pct": 2.0,
                    "max_unrealized_pnl_pct": 3.0,
                    "risk_state": {"profit_protection_armed": False},
                },
            ],
        },
        {
            "scale_in_enabled": True,
            "scale_in_min_info_score": 45.0,
            "scale_in_min_existing_pnl_pct": 4.0,
            "scale_in_min_existing_max_pnl_pct": 6.0,
            "scale_in_max_symbol_exposure_pct_of_equity": 20.0,
            "scale_in_max_open_positions_per_symbol": 2,
            "min_trade_notional_usd": 25.0,
            "target_sprint_scale_in_enabled": True,
            "target_sprint_min_cash_ratio_pct": 65.0,
            "target_sprint_min_info_score": 35.0,
            "target_sprint_min_best_pnl_pct": 6.0,
            "target_sprint_max_symbol_exposure_pct_of_equity": 30.0,
            "target_sprint_max_open_positions_per_symbol": 3,
        },
        {"info_pressure_score": 38.0},
    )
    assert sprint_ok and sprint_reason == "scale_in_allowed" and sprint_details["target_sprint_scale_in"], (sprint_ok, sprint_reason, sprint_details)
    return {
        "status": "ok",
        "good_quote": good,
        "thin_quote": thin,
        "strategy_sample_size": len(sampled),
        "strategy_sample_families": sorted(sampled_families),
        "dynamic_exit_reason": exit_reason,
        "target_sprint_loss_exit": sprint_loss_detail,
        "scale_in_loss_exit": scale_loss_detail,
        "short_horizon_dynamic_exit_reason": short_exit,
        "paper_order": order,
        "historical_barrier_replay": {
            "exit_reason": barrier_replay["exit_reason"],
            "triggered_at": barrier_replay["triggered_at"],
            "ambiguous_same_bar_policy": ambiguous_replay["intrabar_policy"],
            "closed_trade_count": len(barrier_ledger["closed_trades"]),
        },
        "dynamic_sizing": sizing,
        "risk_reduced_sizing": risk_reduced_sizing,
        "validation_probe_sizing": validation_sizing,
        "scale_in_eligibility": scale_details,
        "target_sprint_scale_in": sprint_details,
        "monthly_baseline_persistence": {
            "baseline_record": baseline_record,
            "legacy_list_normalized": True,
            "current_month_self_test": current_month,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--intervals", default="")
    ap.add_argument("--cache-dir", default="")
    ap.add_argument("--now", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline-fixture", action="store_true")
    ap.add_argument("--allow-outside-window", action="store_true")
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--enforce-window", action="store_true", default=True)
    ap.add_argument("--loop-kind", choices=["sunday", "daily", "fast"], default="sunday")
    ap.add_argument("--external-agent-outputs-json", help="optional externally collected 6+ subagent output JSON")
    ap.add_argument("--compact-output", action="store_true")
    ap.add_argument("--ignore-validation-capacity-gate", action="store_true", help="debug only: allow direct scans to bypass validation capacity gating")
    ap.add_argument("--market-fetch-wall-clock-seconds", type=float, default=None, help="Stop market/orderbook fetch after this many seconds and continue with degraded data.")
    ap.add_argument("--kline-fetch-wall-clock-seconds", type=float, default=None, help="Stop K-line prefetch after this many seconds and continue with a degraded report.")
    ap.add_argument("--info-fetch-wall-clock-seconds", type=float, default=None, help="Stop optional information-source fetch after this many seconds and continue with degraded data.")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    run_loop(args)


if __name__ == "__main__":
    main()
