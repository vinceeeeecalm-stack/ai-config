#!/usr/bin/env python3
"""Prefetch role-native evidence for the next Research Committee run.

This script does not create conclusions, does not modify ledgers, and does not
authorize trades. It only gathers public/read-only evidence into a cache that
real subagents can inspect before producing their independent JSON outputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
MANUAL_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = MANUAL_ROOT.parent
ACTIVE_ROOT = WORKSPACE_ROOT / "active-alpha-paper-monitor"
PRIVATE_TMP = Path("/private/tmp")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run_json_command(label: str, cmd: list[str], timeout: int, artifact_path: Path) -> dict[str, Any]:
    started_at = utc_now()
    env = dict(os.environ)
    env.setdefault("PYTHONPYCACHEPREFIX", str(PRIVATE_TMP / "pycache"))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        payload = {
            "status": "timeout",
            "label": label,
            "started_at": started_at,
            "finished_at": utc_now(),
            "cmd": cmd,
            "timeout_seconds": timeout,
            "stdout_tail": (exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        }
        write_json(artifact_path, payload)
        return {"status": "timeout", "artifact_path": str(artifact_path), "error": f"timeout after {timeout}s"}

    if result.returncode != 0:
        payload = {
            "status": "failed",
            "label": label,
            "started_at": started_at,
            "finished_at": utc_now(),
            "cmd": cmd,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        }
        write_json(artifact_path, payload)
        return {
            "status": "failed",
            "artifact_path": str(artifact_path),
            "returncode": result.returncode,
            "error": (result.stderr or result.stdout)[-500:],
        }

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        payload = {
            "status": "parse_failed",
            "label": label,
            "started_at": started_at,
            "finished_at": utc_now(),
            "cmd": cmd,
            "error": str(exc),
            "stdout_tail": result.stdout[-3000:],
            "stderr_tail": result.stderr[-1000:],
        }
        write_json(artifact_path, payload)
        return {"status": "parse_failed", "artifact_path": str(artifact_path), "error": str(exc)}

    write_json(artifact_path, payload)
    return {"status": "ok", "artifact_path": str(artifact_path), "payload": payload}


def macro_summary(payload: dict[str, Any]) -> dict[str, Any]:
    treasury = payload.get("treasury_yield_curve") or {}
    cpi = payload.get("cpi") or {}
    regime = payload.get("macro_regime") or {}
    return {
        "generated_at": payload.get("generated_at"),
        "data_quality": payload.get("data_quality"),
        "classification": regime.get("classification"),
        "risk_score_points": regime.get("risk_score_points"),
        "dca_pace": regime.get("dca_pace"),
        "yield_10y_pct": treasury.get("yield_10y_pct"),
        "yield_2y_pct": treasury.get("yield_2y_pct"),
        "yield_10y_minus_2y_bps": treasury.get("yield_10y_minus_2y_bps"),
        "cpi_yoy_pct": cpi.get("yoy_pct"),
        "cpi_period": cpi.get("latest_period"),
        "missing_data": payload.get("missing_data") or [],
        "source_errors": payload.get("source_errors") or [],
    }


def crypto_summary(payload: dict[str, Any]) -> dict[str, Any]:
    market_quality = payload.get("market_quality") or {}
    rows = {}
    verified_count = 0
    for symbol, item in market_quality.items():
        liquidity = item.get("liquidity") or {}
        if item.get("status") == "verified":
            verified_count += 1
        rows[symbol] = {
            "status": item.get("status"),
            "binance_price": item.get("binance_price"),
            "coingecko_price": item.get("coingecko_price"),
            "diff_bps": item.get("binance_vs_coingecko_diff_bps"),
            "binance_24h_pct": item.get("binance_24h_pct"),
            "binance_kline_7d_pct": item.get("binance_kline_7d_pct"),
            "binance_kline_30d_pct": item.get("binance_kline_30d_pct"),
            "quote_volume_usd": item.get("binance_24h_quote_volume_usd"),
            "spread_bps": liquidity.get("bid_ask_spread_bps"),
            "ask_depth_1pct_usd": liquidity.get("ask_depth_1pct_usd"),
            "ask_depth_2pct_usd": liquidity.get("ask_depth_2pct_usd"),
            "buy_slippage": liquidity.get("buy_slippage") or [],
            "missing_or_degraded": item.get("missing_or_degraded") or [],
        }
    source_status = {}
    for key, source in (payload.get("sources") or {}).items():
        if isinstance(source, dict) and "status" in source:
            source_status[key] = source.get("status")
        elif isinstance(source, dict):
            source_status[key] = "per_symbol"
    return {
        "generated_at": payload.get("generated_at"),
        "verified_market_quality_count": verified_count,
        "symbols": rows,
        "source_status": source_status,
        "decision_rule": payload.get("decision_rule"),
    }


def us_equity_summary(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for item in payload.get("candidates") or []:
        candidates.append(
            {
                "symbol": item.get("candidate_symbol") or item.get("symbol"),
                "rank": item.get("rank"),
                "last_price": item.get("last_price") or item.get("regular_market_price"),
                "open_scan_score_points": item.get("open_scan_score_points"),
                "entry_zone": item.get("entry_zone"),
                "target_price": item.get("target_price"),
                "forecast_probability_pct": item.get("forecast_probability_pct"),
                "execution_readiness_score": item.get("execution_readiness_score"),
                "why_better_than_current_tactical_position": item.get("why_better_than_current_tactical_position"),
            }
        )
    return {
        "created_at": payload.get("created_at"),
        "scan_status": payload.get("scan_status"),
        "data_quality_status": payload.get("data_quality_status"),
        "current_tactical_position": payload.get("current_tactical_position"),
        "deployable_tactical_position": payload.get("deployable_tactical_position"),
        "candidate_count": len(payload.get("candidates") or []),
        "failed_core_sources": payload.get("failed_core_sources") or [],
        "errors": payload.get("errors") or [],
        "candidates": candidates,
        "max_allowed_action": payload.get("max_allowed_action"),
    }


def build_cache(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M')}-research-committee-evidence-prefetch"
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else PRIVATE_TMP / f"{run_id}-research-evidence-cache-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    macro_result = run_json_command(
        "macro_regime_snapshot",
        [sys.executable, str(MANUAL_ROOT / "scripts" / "macro_regime_snapshot.py"), "--format", "json"],
        args.timeout_seconds,
        artifact_dir / "macro_regime_snapshot.json",
    )
    crypto_result = run_json_command(
        "crypto_multisource_alpha_snapshot",
        [
            sys.executable,
            str(ACTIVE_ROOT / "scripts" / "run_multisource_alpha_snapshot.py"),
            "--symbols",
            args.crypto_symbols,
        ],
        args.timeout_seconds,
        artifact_dir / "crypto_multisource_alpha_snapshot.json",
    )
    us_cmd = [
        sys.executable,
        str(ACTIVE_ROOT / "scripts" / "us_open_dynamic_scanner.py"),
        "--json-only",
        "--top",
        str(args.us_top),
        "--max-symbols",
        str(args.us_max_symbols),
    ]
    if args.current_tactical_symbol:
        us_cmd.extend(["--current-tactical-symbol", args.current_tactical_symbol])
    us_result = run_json_command(
        "us_open_dynamic_scanner",
        us_cmd,
        args.timeout_seconds,
        artifact_dir / "us_open_dynamic_scanner.json",
    )

    artifacts = {
        "macro_regime_snapshot": macro_result,
        "crypto_multisource_alpha_snapshot": crypto_result,
        "us_open_dynamic_scanner": us_result,
    }
    role_contexts = {
        "macro_regime_agent": {
            "status": macro_result["status"],
            "artifact_path": macro_result["artifact_path"],
            "evidence_summary": macro_summary(macro_result.get("payload") or {}) if macro_result["status"] == "ok" else {},
            "max_role_action": "watch",
            "note": "Macro evidence affects DCA pace and risk posture only; it cannot trigger a single-asset buy.",
        },
        "crypto_market_agent": {
            "status": crypto_result["status"],
            "artifact_path": crypto_result["artifact_path"],
            "evidence_summary": crypto_summary(crypto_result.get("payload") or {}) if crypto_result["status"] == "ok" else {},
            "max_role_action": "watch",
            "note": "Market quality can repair price/liquidity evidence; paper/live promotion still depends on separate gates.",
        },
        "onchain_defi_agent": {
            "status": crypto_result["status"],
            "artifact_path": crypto_result["artifact_path"],
            "evidence_summary": {
                "defillama_source_status": (((crypto_result.get("payload") or {}).get("sources") or {}).get("defillama_chains") or {}).get("status"),
                "coingecko_trending_status": (((crypto_result.get("payload") or {}).get("sources") or {}).get("coingecko_trending") or {}).get("status"),
            },
            "max_role_action": "watch",
            "note": "This helps TVL/trending context but does not repair wallet-specific lcETH lots or NIGHT holder-class gaps by itself.",
        },
        "social_news_agent": {
            "status": crypto_result["status"],
            "artifact_path": crypto_result["artifact_path"],
            "evidence_summary": {
                "reddit_context_status": (((crypto_result.get("payload") or {}).get("sources") or {}).get("reddit_context") or {}).get("status"),
                "coingecko_trending_status": (((crypto_result.get("payload") or {}).get("sources") or {}).get("coingecko_trending") or {}).get("status"),
            },
            "max_role_action": "watch",
            "note": "Reddit/trending are unverified social inputs; official/key-person gaps must remain visible unless independently sourced.",
        },
        "us_equity_alpha_agent": {
            "status": us_result["status"],
            "artifact_path": us_result["artifact_path"],
            "evidence_summary": us_equity_summary(us_result.get("payload") or {}) if us_result["status"] == "ok" else {},
            "max_role_action": "watch",
            "note": "US scanner evidence supports discovery only; broker/NBBO plus sample-tier, conservative-EV, realtime-signal and account-risk gates still block execute_now.",
        },
    }
    ok_count = sum(1 for item in artifacts.values() if item.get("status") == "ok")
    return {
        "run_id": run_id,
        "generated_at": utc_now(),
        "cache_version": "research-committee-role-native-evidence-cache-v1",
        "source_skill": "manual-investment-strategy-operator",
        "private_api_keys_logged": False,
        "live_orders_enabled": False,
        "does_not_authorize_trades": True,
        "artifact_dir": str(artifact_dir),
        "artifact_paths": [item["artifact_path"] for item in artifacts.values()],
        "prefetch_status": "ok" if ok_count == len(artifacts) else "partial" if ok_count else "failed",
        "artifacts": artifacts,
        "role_contexts": role_contexts,
        "usage_contract": {
            "subagents_must_inspect_sources": True,
            "status_ok_requires_actual_source_use": True,
            "missing_items_must_remain_in_missing_data": True,
            "no_execute_now": True,
            "manual_arbiter_required": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefetch public role-native evidence for Research Committee subagents")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--crypto-symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,ADAUSDT,NEARUSDT,INJUSDT,FETUSDT")
    parser.add_argument("--current-tactical-symbol", default="")
    parser.add_argument("--us-top", type=int, default=3)
    parser.add_argument("--us-max-symbols", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--artifact-dir", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--format", choices=["json"], default="json")
    args = parser.parse_args()

    cache = build_cache(args)
    output = Path(args.output) if args.output else PRIVATE_TMP / f"{cache['run_id']}-research-committee-role-native-evidence-cache.json"
    write_json(output, cache)
    cache["cache_path"] = str(output)
    write_json(output, cache)
    print(json.dumps(cache, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
