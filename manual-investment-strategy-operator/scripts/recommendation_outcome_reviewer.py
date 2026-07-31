#!/usr/bin/env python3
"""Generate outcome-review drafts for pending recommendations.

The script does not place trades and does not mutate the ledger by default.
It turns structured recommendation fields into review candidates so the manual
skill can keep learning without silently changing strategy rules.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"
UA = "Mozilla/5.0 (Codex manual-investment-strategy-operator; outcome review only)"


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def parse_review_days(time_window: Any, default_days: int = 30) -> int:
    if not time_window:
        return default_days
    text = str(time_window).lower()
    numbers = [int(match) for match in re.findall(r"\d+", text)]
    if not numbers:
        return default_days
    days = max(numbers)
    if "trading" in text:
        days = int(round(days * 1.4))
    return max(days, 1)


def fallback_due_at(record: dict[str, Any]) -> dt.datetime | None:
    generated = parse_time(record.get("generated_at"))
    if generated is None:
        return None
    return generated + dt.timedelta(days=parse_review_days(record.get("time_window")))


def due_at(record: dict[str, Any]) -> dt.datetime | None:
    for field in ["latest_exit_or_review_date", "entry_deadline"]:
        value = parse_time(record.get(field))
        if value is not None:
            return value
    return fallback_due_at(record)


def entry_deadline(record: dict[str, Any]) -> dt.datetime | None:
    return parse_time(record.get("entry_deadline"))


def generated_at(record: dict[str, Any]) -> dt.datetime | None:
    return parse_time(record.get("generated_at"))


def parse_price_numbers(text: Any) -> list[float]:
    if not text:
        return []
    text = str(text).replace(",", "")
    backtick_numbers = re.findall(r"`(-?\d+(?:\.\d+)?)`", text)
    raw = backtick_numbers if backtick_numbers else re.findall(r"(?<![A-Za-z])(-?\d+(?:\.\d+)?)(?![A-Za-z])", text)
    values: list[float] = []
    for item in raw:
        try:
            values.append(float(item))
        except ValueError:
            continue
    return values


def number_range(text: Any) -> tuple[float, float] | None:
    values = parse_price_numbers(text)
    if len(values) < 2:
        return None
    return min(values), max(values)


def first_price(text: Any) -> float | None:
    values = parse_price_numbers(text)
    return values[0] if values else None


def fetch_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_binance_klines(symbol: str, start: dt.datetime, end: dt.datetime, interval: str = "1h") -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "symbol": symbol.upper(),
        "interval": interval,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "limit": 1000,
    })
    data = fetch_json(f"https://api.binance.com/api/v3/klines?{params}")
    rows = []
    for item in data:
        rows.append({
            "time": dt.datetime.fromtimestamp(item[0] / 1000, tz=dt.timezone.utc),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
        })
    return rows


def fetch_yahoo_chart(symbol: str, start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "1d",
    })
    data = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}")
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    rows = []
    for index, ts in enumerate(timestamps):
        high = (quote.get("high") or [None])[index]
        low = (quote.get("low") or [None])[index]
        close = (quote.get("close") or [None])[index]
        open_ = (quote.get("open") or [None])[index]
        if high is None or low is None or close is None:
            continue
        rows.append({
            "time": dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc),
            "open": float(open_ if open_ is not None else close),
            "high": float(high),
            "low": float(low),
            "close": float(close),
        })
    return rows


def fetch_history(record: dict[str, Any], start: dt.datetime, end: dt.datetime) -> tuple[list[dict[str, Any]], str | None]:
    symbol = str(record.get("symbol") or "").upper()
    if not symbol or "/" in symbol:
        return [], "unsupported_symbol_for_price_history"
    try:
        if symbol.endswith("USDT"):
            return fetch_binance_klines(symbol, start, end), None
        return fetch_yahoo_chart(symbol, start, end), None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def range_touched(rows: list[dict[str, Any]], low: float, high: float) -> tuple[bool, int | None]:
    for index, row in enumerate(rows):
        if row.get("low") is None or row.get("high") is None:
            continue
        if float(row["low"]) <= high and float(row["high"]) >= low:
            return True, index
    return False, None


def level_touched(rows: list[dict[str, Any]], level: float, direction: str) -> tuple[bool, int | None]:
    for index, row in enumerate(rows):
        if direction == "above" and float(row.get("high", 0.0)) >= level:
            return True, index
        if direction == "below" and float(row.get("low", 10**18)) <= level:
            return True, index
    return False, None


def review_record(record: dict[str, Any], as_of: dt.datetime, fetch_market_data: bool) -> dict[str, Any]:
    rec_id = record.get("recommendation_id")
    generated = generated_at(record)
    due = due_at(record)
    entry_by = entry_deadline(record)
    if generated is None:
        return {
            "recommendation_id": rec_id,
            "symbol": record.get("symbol"),
            "status": "cannot_review",
            "recommended_outcome_status": None,
            "reason": "generated_at_unparseable",
        }

    if due is not None and as_of < due:
        return {
            "recommendation_id": rec_id,
            "symbol": record.get("symbol"),
            "status": "not_due",
            "recommended_outcome_status": None,
            "due_at": due.isoformat(),
            "days_until_due": (due - as_of).days,
        }

    entry_range = number_range(record.get("entry_range"))
    target_range = number_range(record.get("target_range"))
    stop_level = first_price(record.get("stop_or_invalid") or record.get("full_exit_or_invalidation"))
    should_fetch = fetch_market_data and generated is not None and as_of > generated
    rows: list[dict[str, Any]] = []
    fetch_error = None
    if should_fetch:
        rows, fetch_error = fetch_history(record, generated, as_of)

    if should_fetch and not rows:
        return {
            "recommendation_id": rec_id,
            "symbol": record.get("symbol"),
            "status": "review_due_market_data_missing",
            "recommended_outcome_status": None,
            "due_at": due.isoformat() if due else None,
            "fetch_error": fetch_error,
            "reason": "market history unavailable; human review required",
        }

    if not rows:
        return {
            "recommendation_id": rec_id,
            "symbol": record.get("symbol"),
            "status": "review_due_no_market_fetch",
            "recommended_outcome_status": "expired",
            "due_at": due.isoformat() if due else None,
            "reason": "review window elapsed; run with --fetch-market-data before applying outcome",
        }

    entry_hit = True
    entry_idx = 0
    if entry_range:
        entry_hit, entry_idx = range_touched(rows, entry_range[0], entry_range[1])
    if not entry_hit:
        return {
            "recommendation_id": rec_id,
            "symbol": record.get("symbol"),
            "status": "review_draft",
            "recommended_outcome_status": "not_triggered",
            "due_at": due.isoformat() if due else None,
            "entry_range": record.get("entry_range"),
            "reason": "entry range did not trade during review window",
        }

    post_entry = rows[entry_idx or 0 :]
    if target_range:
        target_hit, target_idx = level_touched(post_entry, target_range[0], "above")
    else:
        target_hit, target_idx = False, None
    stop_hit, stop_idx = (False, None)
    if stop_level is not None:
        stop_hit, stop_idx = level_touched(post_entry, stop_level, "below")

    if target_hit and (not stop_hit or (target_idx is not None and stop_idx is not None and target_idx <= stop_idx)):
        outcome = "hit"
        reason = "target range touched after entry before stop/invalidation"
    elif stop_hit:
        outcome = "failed"
        reason = "stop/invalidation touched after entry before target"
    else:
        outcome = "expired"
        reason = "entry touched, but no numeric target/stop resolution before review date"

    first = rows[0]
    last = rows[-1]
    actual_return_pct = None
    try:
        actual_return_pct = (float(last["close"]) / float(first["open"]) - 1.0) * 100.0
    except Exception:
        pass
    return {
        "recommendation_id": rec_id,
        "symbol": record.get("symbol"),
        "status": "review_draft",
        "recommended_outcome_status": outcome,
        "due_at": due.isoformat() if due else None,
        "entry_touched": entry_hit,
        "target_touched": target_hit,
        "stop_touched": stop_hit,
        "actual_return_pct_window": actual_return_pct,
        "history_rows": len(rows),
        "reason": reason,
    }


def build_review_panel(ledger: dict[str, Any], as_of: dt.datetime, fetch_market_data: bool, max_items: int) -> dict[str, Any]:
    pending = [item for item in ledger.get("recommendations", []) if item.get("outcome_status") == "pending"]
    reviews = [review_record(item, as_of, fetch_market_data) for item in pending]
    due = [item for item in reviews if item.get("status") not in {"not_due"}]
    actionable = [item for item in due if item.get("recommended_outcome_status")]
    upcoming = [item for item in reviews if item.get("status") == "not_due"]
    return {
        "generated_at": utc_now(),
        "as_of": as_of.isoformat(),
        "fetch_market_data": fetch_market_data,
        "pending_count": len(pending),
        "due_or_reviewable_count": len(due),
        "draft_review_count": len(actionable),
        "upcoming_count": len(upcoming),
        "draft_reviews": actionable[:max_items],
        "due_unresolved": [item for item in due if not item.get("recommended_outcome_status")][:max_items],
        "upcoming_reviews": sorted(upcoming, key=lambda item: item.get("due_at") or "")[:max_items],
        "note": "Draft reviews are not applied automatically; human confirmation is required before ledger mutation.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate recommendation outcome-review drafts")
    parser.add_argument("--path", default=str(DEFAULT_LEDGER))
    parser.add_argument("--as-of", help="ISO-8601 timestamp; defaults to now")
    parser.add_argument("--fetch-market-data", action="store_true", help="Fetch public historical prices for due recommendations")
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    as_of = parse_time(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    if as_of is None:
        raise ValueError("--as-of must be ISO-8601")
    ledger = load_json(args.path)
    panel = build_review_panel(ledger, as_of, args.fetch_market_data, args.max_items)
    if args.format == "json":
        print(json.dumps(panel, ensure_ascii=False, indent=2))
    else:
        print("# Recommendation Outcome Review Drafts")
        print()
        print(f"- pending_count: `{panel['pending_count']}`")
        print(f"- due_or_reviewable_count: `{panel['due_or_reviewable_count']}`")
        print(f"- draft_review_count: `{panel['draft_review_count']}`")
        print(f"- fetch_market_data: `{panel['fetch_market_data']}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
