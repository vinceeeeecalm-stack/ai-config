#!/usr/bin/env python3
"""
Review the dedicated US-equity paper ledger with public Yahoo intraday bars.

This module is simulation-only.  It never connects to a broker and never
creates a real order.  Frozen entries, targets, stops, quantities and time
stops are read from the ledger and are not moved by the reviewer.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ACTIVE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ACTIVE_ROOT / "paper_trades" / "us_equity_paper_ledger.json"
DEFAULT_REPORT = ACTIVE_ROOT / "reports" / "current-us-equity-paper-review.md"
NEW_YORK_TZ = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0 (Codex US equity paper reviewer; simulation only)"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_yahoo_intraday(symbol: str, *, timeout: int = 15) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "range": "5d",
            "interval": "5m",
            "includePrePost": "false",
            "events": "div,splits",
        }
    )
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?{query}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo returned no intraday bars for {symbol}")
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows = []
    now = utc_now()
    for index, timestamp in enumerate(result.get("timestamp") or []):
        values = {
            key: (quote.get(key) or [None])[index]
            for key in ("open", "high", "low", "close", "volume")
        }
        if any(values[key] is None for key in ("high", "low", "close")):
            continue
        started_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        local = started_at.astimezone(NEW_YORK_TZ)
        closed = started_at + timedelta(minutes=5) <= now
        regular = (
            (local.hour, local.minute) >= (9, 30)
            and (local.hour, local.minute) < (16, 0)
        )
        if not regular or not closed:
            continue
        rows.append(
            {
                "started_at": iso_utc(started_at),
                "ended_at": iso_utc(started_at + timedelta(minutes=5)),
                "session_date": local.date().isoformat(),
                "open": values["open"],
                "high": float(values["high"]),
                "low": float(values["low"]),
                "close": float(values["close"]),
                "volume": float(values["volume"] or 0),
            }
        )
    return rows


def evaluate_position(position: dict, bars: list[dict], *, now: datetime) -> dict:
    entry = position["entry_order"]
    fill_time = parse_iso(entry["fill_time_assumption"])
    fill_price = float(entry["fill_price_usd"])
    quantity = float(position["quantity_shares"])
    post_fill = [
        bar
        for bar in bars
        if parse_iso(bar["started_at"]) >= fill_time
    ]
    if not post_fill:
        raise RuntimeError(f"No closed post-fill bars for {position['paper_trade_id']}")

    high_bar = max(post_fill, key=lambda bar: bar["high"])
    low_bar = min(post_fill, key=lambda bar: bar["low"])
    latest = post_fill[-1]
    target_1 = float(position["position_plan"]["target_1"]["price_usd"])
    target_2 = float(position["position_plan"]["target_2"]["price_usd"])
    stop_price = float(position["position_plan"]["price_stop"]["price_usd"])
    time_stop = parse_iso(position["position_plan"]["time_stop"]["timestamp"])

    target_1_bar = next((bar for bar in post_fill if bar["high"] >= target_1), None)
    target_2_bar = next((bar for bar in post_fill if bar["high"] >= target_2), None)

    session_closes: dict[str, dict] = {}
    for bar in post_fill:
        session_closes[bar["session_date"]] = bar
    completed_session_closes = []
    now_ny = now.astimezone(NEW_YORK_TZ)
    for session_date, bar in session_closes.items():
        local_date = datetime.fromisoformat(session_date).date()
        if local_date < now_ny.date() or (
            local_date == now_ny.date()
            and (now_ny.hour, now_ny.minute) >= (16, 5)
        ):
            completed_session_closes.append(bar)
    stop_bar = next(
        (bar for bar in completed_session_closes if bar["close"] <= stop_price),
        None,
    )

    first_events = [
        ("target_1", parse_iso(target_1_bar["ended_at"])) if target_1_bar else None,
        ("target_2", parse_iso(target_2_bar["ended_at"])) if target_2_bar else None,
        ("stop", parse_iso(stop_bar["ended_at"])) if stop_bar else None,
    ]
    first_events = sorted((item for item in first_events if item), key=lambda item: item[1])
    first_result = first_events[0][0] if first_events else "neither"
    latest_price = latest["close"]
    time_stop_due = now >= time_stop

    return {
        "as_of": latest["ended_at"],
        "price_usd": round(latest_price, 4),
        "market_value_usd": round(latest_price * quantity, 6),
        "unrealized_pnl_usd": round((latest_price - fill_price) * quantity, 6),
        "unrealized_roi_pct": round((latest_price / fill_price - 1) * 100, 6),
        "post_fill_mfe_price_usd": round(high_bar["high"], 4),
        "post_fill_mfe_at": high_bar["ended_at"],
        "post_fill_mfe_usd": round((high_bar["high"] - fill_price) * quantity, 6),
        "post_fill_mfe_pct": round((high_bar["high"] / fill_price - 1) * 100, 6),
        "post_fill_mae_price_usd": round(low_bar["low"], 4),
        "post_fill_mae_at": low_bar["ended_at"],
        "post_fill_mae_usd": round((low_bar["low"] - fill_price) * quantity, 6),
        "post_fill_mae_pct": round((low_bar["low"] / fill_price - 1) * 100, 6),
        "target_1_hit": target_1_bar is not None,
        "target_1_first_hit_at": target_1_bar["ended_at"] if target_1_bar else None,
        "target_2_hit": target_2_bar is not None,
        "target_2_first_hit_at": target_2_bar["ended_at"] if target_2_bar else None,
        "stop_hit": stop_bar is not None,
        "stop_first_hit_at": stop_bar["ended_at"] if stop_bar else None,
        "first_result": first_result,
        "time_stop_due": time_stop_due,
        "window_status": (
            "stop_resolved"
            if stop_bar
            else "target_2_resolved"
            if target_2_bar
            else "time_stop_due"
            if time_stop_due
            else "ongoing"
        ),
        "data_quality": "public_yahoo_closed_5m_regular_session_bars",
        "bar_count_post_fill": len(post_fill),
        "stop_rule_preserved": "daily_regular_session_close_below_or_equal_157.50",
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def render_report(ledger: dict, reviews: list[dict], *, now: datetime) -> str:
    lines = [
        f"# US Equity Paper Review | {iso_utc(now)}",
        "",
        "> Simulation only. No brokerage connection and no real order.",
        "",
        "| Symbol | Entry | Last | ROI | MFE | MAE | T1 | T2 | Close stop | Time stop | Status |",
        "|---|---:|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    by_id = {item["paper_trade_id"]: item for item in reviews}
    for position in ledger.get("open_positions") or []:
        review = by_id.get(position["paper_trade_id"]) or {}
        mark = review.get("mark") or position.get("mark") or {}
        lines.append(
            f"| {position.get('symbol')} | {position.get('entry_order', {}).get('fill_price_usd')} | "
            f"{mark.get('price_usd')} | {mark.get('unrealized_roi_pct')}% | "
            f"{mark.get('post_fill_mfe_pct')}% | {mark.get('post_fill_mae_pct')}% | "
            f"{mark.get('target_1_hit')} | {mark.get('target_2_hit')} | "
            f"{mark.get('stop_hit')} | {mark.get('time_stop_due')} | "
            f"{mark.get('window_status')} |"
        )
    lines.extend(
        [
            "",
            "- Frozen levels are unchanged; this reviewer only refreshes the mark and outcome evidence.",
            "- A price stop is evaluated only on a completed regular-session close.",
            "- If a target and stop are unresolved inside the same bar, the ledger policy remains stop-first.",
            "",
        ]
    )
    return "\n".join(lines)


def review_ledger(
    ledger: dict,
    *,
    now: datetime,
    bars_by_symbol: dict[str, list[dict]],
) -> tuple[dict, list[dict]]:
    reviews = []
    for position in ledger.get("open_positions") or []:
        mark = evaluate_position(
            position,
            bars_by_symbol[position["symbol"]],
            now=now,
        )
        position["mark"] = mark
        reviews.append(
            {
                "paper_trade_id": position["paper_trade_id"],
                "symbol": position["symbol"],
                "mark": mark,
            }
        )
        event_id = (
            f"paper-review-{position['paper_trade_id']}-"
            f"{mark['as_of'].replace(':', '').replace('-', '')}"
        )
        if not any(event.get("event_id") == event_id for event in ledger.get("events") or []):
            ledger.setdefault("events", []).append(
                {
                    "event_id": event_id,
                    "paper_trade_id": position["paper_trade_id"],
                    "event_type": "paper_market_review",
                    "recorded_at": iso_utc(now),
                    "market_as_of": mark["as_of"],
                    "details": (
                        f"Closed 5-minute-bar paper review: last={mark['price_usd']}, "
                        f"MFE={mark['post_fill_mfe_pct']}%, MAE={mark['post_fill_mae_pct']}%, "
                        f"T1={mark['target_1_hit']}, T2={mark['target_2_hit']}, "
                        f"close-stop={mark['stop_hit']}; frozen levels unchanged."
                    ),
                }
            )
    ledger["updated_at"] = iso_utc(now)
    return ledger, reviews


def self_test() -> None:
    position = {
        "paper_trade_id": "test",
        "entry_order": {
            "fill_price_usd": 100,
            "fill_time_assumption": "2026-07-28T14:10:00Z",
        },
        "quantity_shares": 10,
        "position_plan": {
            "target_1": {"price_usd": 110},
            "target_2": {"price_usd": 120},
            "price_stop": {"price_usd": 90},
            "time_stop": {"timestamp": "2026-08-07T20:00:00Z"},
        },
    }
    bars = [
        {
            "started_at": "2026-07-28T14:10:00Z",
            "ended_at": "2026-07-28T14:15:00Z",
            "session_date": "2026-07-28",
            "high": 111,
            "low": 95,
            "close": 105,
        },
        {
            "started_at": "2026-07-28T19:55:00Z",
            "ended_at": "2026-07-28T20:00:00Z",
            "session_date": "2026-07-28",
            "high": 106,
            "low": 88,
            "close": 95,
        },
    ]
    mark = evaluate_position(
        position,
        bars,
        now=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
    )
    assert mark["target_1_hit"] is True
    assert mark["stop_hit"] is False, "intraday low must not trigger a close-only stop"
    assert mark["post_fill_mfe_pct"] == 11
    assert mark["post_fill_mae_pct"] == -12
    print("us_equity_paper_reviewer self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    ledger_path = Path(args.ledger)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    now = utc_now()
    symbols = {
        position["symbol"]
        for position in ledger.get("open_positions") or []
    }
    bars_by_symbol = {
        symbol: fetch_yahoo_intraday(symbol, timeout=args.timeout)
        for symbol in symbols
    }
    ledger, reviews = review_ledger(
        ledger,
        now=now,
        bars_by_symbol=bars_by_symbol,
    )
    if not args.no_write:
        atomic_write_json(ledger_path, ledger)
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            render_report(ledger, reviews, now=now),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": "reviewed",
                "paper_only": True,
                "live_orders_enabled": False,
                "reviewed_at": iso_utc(now),
                "ledger_path": str(ledger_path),
                "report_path": str(args.report),
                "reviews": reviews,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
