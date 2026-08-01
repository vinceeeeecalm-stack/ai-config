#!/usr/bin/env python3
"""Public-data-only BTC hourly Up/Down paper-trading pipeline.

This module is deliberately isolated from the project's sports and generic paper
ledgers.  It has no wallet, signing, order-placement, account, or withdrawal
code.  A cycle is always shadow-only until both the forward-capture and model
gates have passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "btc_hourly_paper_policy.json"
DATA_DIR = ROOT / "data" / "btc_hourly_paper"
REPORT_DIR = ROOT / "reports" / "btc_hourly_paper"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
BINANCE_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com")
SAFE = {"paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "real_money_execution_authorized": False}
PROTECTED = (ROOT / "data" / "paper_ledger.json", ROOT / "data" / "sports_100usd_paper_ledger.json")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush(); os.fsync(handle.fileno())


def sha(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except ValueError:
        return None


def public_json(url: str, timeout: int = 15) -> Any:
    allowed = (GAMMA + "/", CLOB + "/") + tuple(host + "/api/v3/" for host in BINANCE_HOSTS)
    if not url.startswith(allowed):
        raise ValueError(f"non-public or non-allowlisted URL: {url}")
    if any(token in url.lower() for token in ("/order", "/cancel", "/balance", "/account", "/withdraw", "/auth", "/api-key")):
        raise ValueError(f"private/trading endpoint blocked: {url}")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "polymarket-btc-hourly-paper/1"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_binance(path: str, params: dict[str, Any]) -> Any:
    encoded = urlencode(params)
    errors = []
    for host in BINANCE_HOSTS:
        try:
            return public_json(f"{host}{path}?{encoded}")
        except Exception as exc:  # fallbacks are public read-only hosts
            errors.append(f"{host}:{type(exc).__name__}")
    raise RuntimeError("all Binance public hosts failed: " + "; ".join(errors))


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = read_json(path)
    if not isinstance(policy, dict) or any(policy.get(key) is not value for key, value in SAFE.items() if key != "real_money_execution_authorized"):
        raise ValueError("unsafe or invalid BTC hourly policy")
    return policy


def lock(path: Path, stale_seconds: int = 570) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = read_json(path, {})
        created = parse_time(existing.get("created_at")) if isinstance(existing, dict) else None
        if created and (now() - created).total_seconds() <= stale_seconds:
            return {"acquired": False, "reason": "fresh_lock", "owner": existing}
        path.unlink(missing_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, json.dumps({"created_at": iso(), "pid": os.getpid()}).encode())
        finally:
            os.close(descriptor)
        return {"acquired": True}
    except FileExistsError:
        return {"acquired": False, "reason": "lock_race"}


def unlock(path: Path) -> None:
    path.unlink(missing_ok=True)


def outcomes(market: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    raw_outcomes, raw_tokens = market.get("outcomes"), market.get("clobTokenIds")
    try:
        items = raw_outcomes if isinstance(raw_outcomes, list) else json.loads(raw_outcomes or "[]")
        tokens = raw_tokens if isinstance(raw_tokens, list) else json.loads(raw_tokens or "[]")
    except (TypeError, json.JSONDecodeError):
        return {}, ["outcome_or_token_json_invalid"]
    mapped = {str(name).upper(): str(tokens[index]) for index, name in enumerate(items) if index < len(tokens)}
    failures = [] if set(("UP", "DOWN")).issubset(mapped) else ["up_down_tokens_missing"]
    return mapped, failures


def rules_clear(market: dict[str, Any], start: datetime, end: datetime) -> tuple[bool, bool, list[str]]:
    text = " ".join(str(market.get(key) or "") for key in ("question", "description", "resolutionSource", "resolutionDescription")).lower()
    failures = []
    if "binance" not in text or not re.search(r"btc\s*/?\s*usdt|bitcoin", text):
        failures.append("binance_btcusdt_rule_not_proven")
    if not market.get("resolutionSource"):
        failures.append("resolution_source_missing")
    if abs((end - start).total_seconds() - 3600) > 120:
        failures.append("not_one_hour_contract")
    up_tie = bool(re.search(r"(?:greater than or equal|greater than or equal to|>=|at or above)", text))
    # Do not mistake "otherwise ... Down" for an explicit tie-to-Down rule.
    down_tie = bool(re.search(r"(?:less than or equal|<=|down\s+(?:if|when).{0,80}(?:equal|<=))", text))
    if not up_tie or down_tie:
        failures.append("tie_rule_ambiguous")
    return not failures, up_tie, failures


def market_start(market: dict[str, Any]) -> datetime | None:
    # Gamma's startDate can be the market-record creation time; eventStartTime
    # is the actual trading-window boundary for the short-duration series.
    direct = parse_time(market.get("eventStartTime") or market.get("startTime") or market.get("startDate") or market.get("start_date"))
    if direct:
        return direct
    events = market.get("events") if isinstance(market.get("events"), list) else []
    return parse_time(events[0].get("startDate")) if events and isinstance(events[0], dict) else None


def discover_market(at: datetime, fetch: Callable[[str], Any] = public_json) -> tuple[dict[str, Any] | None, list[str]]:
    url = f"{GAMMA}/markets?" + urlencode({"active": "true", "closed": "false", "limit": 500})
    payload = fetch(url)
    rows = payload.get("markets", payload) if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
    # Gamma's broad listing is volume ordered.  A young hourly contract can be
    # absent, so use CLOB's cursor inventory as a public-data fallback.
    if not any("up or down" in str(row.get("question") or "").lower() and ("bitcoin" in str(row.get("question") or "").lower() or "btc" in str(row.get("question") or "").lower()) for row in rows if isinstance(row, dict)):
        cursor, seen, condition_ids = None, set(), []
        try:
            for _ in range(12):
                sampling_url = f"{CLOB}/sampling-markets" + ("?" + urlencode({"next_cursor": cursor}) if cursor else "")
                sampled = fetch(sampling_url); page = sampled.get("data", []) if isinstance(sampled, dict) else []
                for item in page if isinstance(page, list) else []:
                    question = str(item.get("question") or "").lower()
                    if "up or down" in question and ("bitcoin" in question or "btc" in question) and item.get("condition_id"):
                        condition_ids.append(str(item["condition_id"]))
                cursor = sampled.get("next_cursor") if isinstance(sampled, dict) else None
                if not cursor or cursor == "LTE=" or cursor in seen or condition_ids: break
                seen.add(cursor)
            if condition_ids:
                detail_url = f"{GAMMA}/markets?" + urlencode([("condition_ids", item) for item in condition_ids] + [("limit", len(condition_ids))])
                detail = fetch(detail_url); rows = detail if isinstance(detail, list) else rows
        except Exception:
            pass
    if not any("up or down" in str(row.get("question") or "").lower() and ("bitcoin" in str(row.get("question") or "").lower() or "btc" in str(row.get("question") or "").lower()) for row in rows if isinstance(row, dict)):
        try:
            event_markets = []
            for offset in range(0, 601, 100):
                events = fetch(f"{GAMMA}/events?" + urlencode({"active": "true", "closed": "false", "limit": 100, "offset": offset, "tag_id": 235}))
                for event in events if isinstance(events, list) else []:
                    if "up or down" not in str(event.get("title") or "").lower() or "bitcoin" not in str(event.get("title") or "").lower():
                        continue
                    for nested in event.get("markets", []) if isinstance(event.get("markets"), list) else []:
                        row = dict(nested); row.setdefault("eventStartTime", event.get("eventStartTime") or event.get("startTime")); row.setdefault("events", [event])
                        event_markets.append(row)
                if len(events) < 100: break
            if event_markets: rows = event_markets
        except Exception:
            pass
    candidates = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("acceptingOrders", False):
            continue
        question = str(row.get("question") or "").lower()
        mapping, mapping_failures = outcomes(row)
        start, end = market_start(row), parse_time(row.get("endDate"))
        if not ("up or down" in question and ("bitcoin" in question or "btc" in question) and not mapping_failures and start and end and start <= at < end):
            continue
        clear, tie_break_up, failures = rules_clear(row, start, end)
        candidates.append({"market": row, "start": start, "end": end, "tokens": mapping,
                           "rules_clear": clear, "tie_break_up": tie_break_up, "rule_failures": failures})
    if not candidates:
        return None, ["active_btc_hourly_market_not_found"]
    # A page can expose 5m, 15m and 1h BTC series at once.  Prefer the
    # contract which actually meets this strategy's rule contract; retain a
    # failing candidate only when no eligible contract exists so PASS remains
    # explainable rather than silent.
    candidates.sort(key=lambda item: (not item["rules_clear"], item["end"]))
    return candidates[0], candidates[0]["rule_failures"]


def levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    result = []
    for row in book.get(side, []) or []:
        try:
            price = float(row["price"] if isinstance(row, dict) else row[0]); size = float(row["size"] if isinstance(row, dict) else row[1])
            if 0 < price < 1 and size > 0: result.append((price, size))
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return sorted(result, key=lambda row: row[0], reverse=side == "bids")


def fee_usd(shares: float, price: float, rate: float) -> float:
    fee = shares * rate * price * (1 - price)
    return round(fee, 5) if fee >= 0.00001 else 0.0


def buy_for_budget(book: dict[str, Any], cash_cap: float, fee_rate: float, slippage_bps: float) -> dict[str, Any]:
    asks, bids = levels(book, "asks"), levels(book, "bids")
    if not asks or not bids or cash_cap <= 0: return {"fillable": False, "reason": "two_sided_book_missing"}
    minimum = float(book.get("min_order_size") or 0)
    def execute(notional: float) -> dict[str, Any]:
        left, raw_value, shares, fills = notional, 0.0, 0.0, []
        for price, size in asks:
            take_value = min(left, price * size); take_shares = take_value / price
            fills.append({"price": price, "shares": take_shares}); raw_value += take_value; shares += take_shares; left -= take_value
            if left <= 1e-9: break
        if left > 0.000001 or shares <= 0: return {"fillable": False, "reason": "insufficient_depth"}
        vwap = raw_value / shares; fill_price = min(.999999, vwap * (1 + slippage_bps / 10000))
        trade_value = fill_price * shares; fee = fee_usd(shares, fill_price, fee_rate)
        return {"fillable": True, "fills": fills, "shares": shares, "raw_vwap": vwap, "fill_price": fill_price,
                "trade_value": trade_value, "entry_fee_usd": fee, "total_entry_cost": trade_value + fee,
                "best_ask": asks[0][0], "best_bid": bids[0][0], "spread": asks[0][0] - bids[0][0],
                "price_impact": vwap - asks[0][0], "min_order_size": minimum}
    low, high, best = 0.0, min(cash_cap, sum(price * size for price, size in asks)), None
    for _ in range(35):
        trial = execute((low + high) / 2)
        if trial.get("fillable") and trial["total_entry_cost"] <= cash_cap:
            best, low = trial, (low + high) / 2
        else: high = (low + high) / 2
    if not best: return {"fillable": False, "reason": "cash_or_depth_insufficient"}
    if best["shares"] + 1e-9 < minimum: return {"fillable": False, "reason": "min_order_size_not_met", "min_order_size": minimum}
    return best


def kline_row(raw: list[Any]) -> dict[str, float]:
    return {"open_time": float(raw[0]), "open": float(raw[1]), "high": float(raw[2]), "low": float(raw[3]),
            "close": float(raw[4]), "quote_volume": float(raw[7]), "close_time": float(raw[6]),
            "taker_buy_quote": float(raw[10])}


def fetch_market_data(at: datetime, fetch_binance: Callable[[str, dict[str, Any]], Any] = get_binance) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        minute_raw = fetch_binance("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 360})
        hour_raw = fetch_binance("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 30})
        depth = fetch_binance("/api/v3/depth", {"symbol": "BTCUSDT", "limit": 100})
        minutes = [kline_row(row) for row in minute_raw if float(row[6]) < at.timestamp() * 1000]
        hours = [kline_row(row) for row in hour_raw if float(row[6]) < at.timestamp() * 1000]
        if len(minutes) < 40 or len(hours) < 25: return None, ["insufficient_closed_binance_klines"]
        age = at.timestamp() - minutes[-1]["close_time"] / 1000
        if age > 120: return None, ["binance_data_stale"]
        return {"minutes": minutes, "hours": hours, "depth": depth, "captured_at": iso(at), "latest_closed_bar_age_seconds": age}, []
    except Exception as exc:
        return None, [f"binance_public_fetch_failed:{type(exc).__name__}"]


def features(minutes: list[dict[str, float]], hours: list[dict[str, float]], start: datetime, checkpoint: int) -> dict[str, float]:
    target = start.timestamp() * 1000
    window = [row for row in minutes if target <= row["open_time"] < target + checkpoint * 60000]
    prior = [row for row in minutes if row["open_time"] < target]
    if len(window) < checkpoint or len(prior) < 30: raise ValueError("insufficient closed checkpoint bars")
    closes = [row["close"] for row in window]
    def ret(length: int, baseline: list[dict[str, float]] = prior) -> float:
        if length <= len(window): return closes[-1] / (window[-length]["open"] if length == len(window) else closes[-length - 1]) - 1
        return closes[-1] / baseline[-(length - len(window))]["close"] - 1
    minute_returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes)) if closes[index - 1] > 0]
    quote = sum(row["quote_volume"] for row in window); buy = sum(row["taker_buy_quote"] for row in window)
    return {"hour_return": closes[-1] / window[0]["open"] - 1, "ret_5m": ret(min(5, checkpoint)),
            "ret_10m": ret(min(10, checkpoint)), "ret_30m": ret(min(30, checkpoint)),
            "realized_vol": statistics.pstdev(minute_returns) if len(minute_returns) > 1 else 0.0,
            "quote_volume_log": math.log1p(quote), "taker_buy_ratio": buy / quote if quote else .5,
            "ret_1h": closes[-1] / hours[-2]["close"] - 1, "ret_4h": closes[-1] / hours[-5]["close"] - 1,
            "ret_24h": closes[-1] / hours[-25]["close"] - 1, "hour_utc": start.hour / 23, "weekday": start.weekday() / 6}


FEATURE_NAMES = ("hour_return", "ret_5m", "ret_10m", "ret_30m", "realized_vol", "quote_volume_log", "taker_buy_ratio", "ret_1h", "ret_4h", "ret_24h", "hour_utc", "weekday")


def sigmoid(value: float) -> float: return 1 / (1 + math.exp(-max(-35, min(35, value))))


def train_logistic(rows: list[tuple[dict[str, float], int]]) -> dict[str, Any]:
    values = [[item[0][name] for name in FEATURE_NAMES] for item in rows]; labels = [item[1] for item in rows]
    means = [sum(row[index] for row in values) / len(values) for index in range(len(FEATURE_NAMES))]
    scales = [max(1e-9, math.sqrt(sum((row[index] - means[index]) ** 2 for row in values) / len(values))) for index in range(len(FEATURE_NAMES))]
    weights = [0.0] * (len(FEATURE_NAMES) + 1); rate, l2 = .08, .02
    for _ in range(500):
        gradient = [0.0] * len(weights)
        for row, label in zip(values, labels):
            x = [1.0] + [(row[index] - means[index]) / scales[index] for index in range(len(FEATURE_NAMES))]
            error = sigmoid(sum(a * b for a, b in zip(weights, x))) - label
            for index, value in enumerate(x): gradient[index] += error * value / len(values)
        for index in range(1, len(weights)): gradient[index] += l2 * weights[index]
        weights = [weight - rate * grad for weight, grad in zip(weights, gradient)]
    return {"feature_names": list(FEATURE_NAMES), "means": means, "scales": scales, "weights": weights}


def predict(model: dict[str, Any], value: dict[str, float], support: int) -> tuple[float, float, float]:
    x = [1.0] + [(value[name] - model["means"][index]) / model["scales"][index] for index, name in enumerate(FEATURE_NAMES)]
    probability = sigmoid(sum(a * b for a, b in zip(model["weights"], x)))
    radius = 1.96 * math.sqrt(max(1e-9, probability * (1 - probability) / max(1, support)))
    return probability, max(.001, probability - radius), min(.999, probability + radius)


def log_loss(values: list[tuple[float, int]]) -> float:
    return -sum(label * math.log(max(1e-9, p)) + (1 - label) * math.log(max(1e-9, 1 - p)) for p, label in values) / len(values)


def expected_calibration_error(values: list[tuple[float, int]], bins: int = 10) -> float:
    if not values: return 1.0
    total = 0.0
    for index in range(bins):
        bucket = [(p, y) for p, y in values if min(bins - 1, int(p * bins)) == index]
        if bucket:
            total += len(bucket) / len(values) * abs(sum(p for p, _ in bucket) / len(bucket) - sum(y for _, y in bucket) / len(bucket))
    return total


def train_from_rows(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    checkpoints = {}; approved = True
    for checkpoint in policy["entry_checkpoint_minutes"]:
        group = [(row["features"][str(checkpoint)], row["label"]) for row in rows if str(checkpoint) in row.get("features", {})]
        train_end, calibration_end = int(len(group) * .6), int(len(group) * .8)
        if len(group) - calibration_end < policy["min_holdout_samples_per_checkpoint"]:
            checkpoints[str(checkpoint)] = {"status": "insufficient_holdout", "samples": len(group)}; approved = False; continue
        model = train_logistic(group[:train_end])
        holdout = [(predict(model, value, train_end)[0], label) for value, label in group[calibration_end:]]
        base = sum(label for _, label in group[:train_end]) / train_end
        brier = sum((probability - label) ** 2 for probability, label in holdout) / len(holdout)
        base_brier = sum((base - label) ** 2 for _, label in holdout) / len(holdout)
        ece = expected_calibration_error(holdout)
        min_brier_improvement = float(policy.get("min_brier_improvement", 0.0))
        ok = brier <= base_brier - min_brier_improvement and ece <= policy["max_calibration_error"]
        checkpoints[str(checkpoint)] = {"status": "approved" if ok else "research_only", "model": model, "support": len(holdout),
            "base_rate": base, "brier": brier, "base_brier": base_brier, "log_loss": log_loss(holdout), "calibration_error": ece}
        approved = approved and ok
    return {"schema_version": policy.get("model_schema_version", "btc-hourly-model-v1"), "created_at": iso(),
            "model_version": policy.get("model_version", "btc-hourly-l2-logistic-v1"),
            "approved": approved, "checkpoints": checkpoints, **SAFE}


def collect_history(base: Path, policy: dict[str, Any], at: datetime, batches: int | None = None,
                    fetch_binance: Callable[[str, dict[str, Any]], Any] = get_binance) -> dict[str, Any]:
    """Persist public 1m bars progressively; one scheduler is the only collector."""
    state_path, rows_path = base / "history-state.json", base / "history-minutes.jsonl"
    state = read_json(state_path, {})
    end = int(at.replace(second=0, microsecond=0).timestamp() * 1000) - 60000
    start = state.get("next_start_ms")
    if start is None:
        start = int((at - timedelta(days=int(policy["history_days"]) + 1)).replace(minute=0, second=0, microsecond=0).timestamp() * 1000)
    budget, fetched = batches or int(policy["history_batches_per_run"]), 0
    while fetched < budget and start < end:
        raw = fetch_binance("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "startTime": start, "endTime": end, "limit": 1000})
        if not raw: break
        rows_path.parent.mkdir(parents=True, exist_ok=True)
        with rows_path.open("a", encoding="utf-8") as handle:
            for row in raw:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            handle.flush(); os.fsync(handle.fileno())
        start = int(raw[-1][0]) + 60000; fetched += 1
        if len(raw) < 1000: break
    state = {"next_start_ms": start, "target_end_ms": end, "complete": start >= end, "updated_at": iso(at), "batches_fetched_last_run": fetched,
             "history_days": policy["history_days"], **SAFE}
    write_json(state_path, state); return state


def history_samples(rows_path: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    if not rows_path.exists(): return []
    bars = [kline_row(json.loads(line)) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    bars.sort(key=lambda row: row["open_time"])
    unique = {int(row["open_time"]): row for row in bars}; bars = [unique[key] for key in sorted(unique)]
    grouped: dict[int, list[dict[str, float]]] = {}
    for bar in bars: grouped.setdefault(int(bar["open_time"] // 3600000) * 3600000, []).append(bar)
    hourly = []
    for hour_start in sorted(grouped):
        block = sorted(grouped[hour_start], key=lambda row: row["open_time"])
        if len(block) == 60 and block[0]["open_time"] == hour_start and all(block[offset]["open_time"] == hour_start + offset * 60000 for offset in range(60)):
            hourly.append(block)
    samples = []
    for index in range(25, len(hourly)):
        block, start = hourly[index], datetime.fromtimestamp(block[0]["open_time"] / 1000, timezone.utc)
        prior_minutes = [row for prior in hourly[max(0, index - 2):index] for row in prior] + block
        prior_hours = [hourly[item][-1] for item in range(index - 25, index)]
        label = int(block[-1]["close"] >= block[0]["open"]) if policy["tie_break_up"] else int(block[-1]["close"] > block[0]["open"])
        values = {}
        for checkpoint in policy["entry_checkpoint_minutes"]:
            try: values[str(checkpoint)] = features(prior_minutes, prior_hours, start, checkpoint)
            except (ValueError, IndexError, ZeroDivisionError): pass
        if len(values) == len(policy["entry_checkpoint_minutes"]): samples.append({"hour_start": iso(start), "features": values, "label": label})
    return samples


def train_history(base: Path, policy: dict[str, Any]) -> dict[str, Any]:
    samples = history_samples(base / "history-minutes.jsonl", policy)
    if not samples: return {"status": "history_not_ready", "samples": 0, **SAFE}
    model = train_from_rows(samples, policy); model["training_samples"] = len(samples)
    write_json(base / "model.json", model)
    return {"status": "trained", "samples": len(samples), "approved": model["approved"], **SAFE}


def new_ledger(policy: dict[str, Any]) -> dict[str, Any]:
    accounts = {name: {"initial_equity_usd": settings["initial_equity_usd"], "cash_usd": settings["initial_equity_usd"],
                        "equity_usd": settings["initial_equity_usd"], "high_watermark_usd": settings["initial_equity_usd"], "max_drawdown_pct": 0.0}
                for name, settings in policy["accounts"].items()}
    return {"schema_version": policy.get("ledger_schema_version", "btc-hourly-paper-ledger-v1"), "created_at": iso(), "updated_at": iso(), "accounts": accounts,
            "open_positions": [], "closed_positions": [], "paper_orders": [], "events": [], **SAFE}


def load_ledger(path: Path, policy: dict[str, Any]) -> dict[str, Any]:
    value = read_json(path, new_ledger(policy));
    if any(value.get(key) is not setting for key, setting in SAFE.items()): raise ValueError("unsafe BTC ledger")
    return value


def all_in_cost_per_share(fill: dict[str, Any]) -> float:
    return fill["total_entry_cost"] / fill["shares"]


def phase(state: dict[str, Any], model: dict[str, Any] | None, at: datetime, policy: dict[str, Any]) -> str:
    started = parse_time(state.get("shadow_started_at")) or at
    complete = sum(1 for item in state.get("hourly_coverage", {}).values() if item.get("resolved"))
    expected_per_market = len(policy.get("shadow_checkpoint_minutes", policy["checkpoint_minutes"]))
    expected = sum(expected_per_market for item in state.get("hourly_coverage", {}).values() if item.get("resolved"))
    captured = sum(len(item.get("checkpoints", [])) for item in state.get("hourly_coverage", {}).values() if item.get("resolved"))
    coverage = captured / expected if expected else 0.0
    if (at - started).total_seconds() < policy["shadow_hours"] * 3600 or complete < policy["shadow_min_complete_hours"] or coverage < policy["shadow_min_checkpoint_coverage"]:
        return "shadow"
    return "paper" if model and model.get("approved") else "shadow_model_unapproved"


def terminal_outcome(market: dict[str, Any]) -> str | None:
    mapping, failures = outcomes(market)
    if failures or not market.get("closed"): return None
    raw = market.get("outcomePrices")
    try: prices = raw if isinstance(raw, list) else json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError): return None
    raw_outcomes = market.get("outcomes")
    try: names = raw_outcomes if isinstance(raw_outcomes, list) else json.loads(raw_outcomes or "[]")
    except (TypeError, json.JSONDecodeError): return None
    winners = [str(names[index]).upper() for index, price in enumerate(prices) if index < len(names) and float(price) >= .999]
    return winners[0] if len(winners) == 1 and winners[0] in mapping else None


def settle_open(ledger: dict[str, Any], at: datetime, fetch: Callable[[str], Any] = public_json) -> list[dict[str, Any]]:
    results = []
    for position in list(ledger["open_positions"]):
        try:
            rows = fetch(f"{GAMMA}/markets?" + urlencode([( "condition_ids", position["condition_id"]), ("limit", 1)]))
            market = rows[0] if isinstance(rows, list) and rows else None; winner = terminal_outcome(market or {})
            if not winner: continue
            raw = get_binance("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1h", "startTime": int(parse_time(position["market_start"]).timestamp()*1000), "limit": 1})
            if not raw: continue
            candle = kline_row(raw[0]); computed = "UP" if candle["close"] >= candle["open"] else "DOWN"
            if computed != winner:
                position["settlement_status"] = "DISPUTED_PENDING"; position["settlement_dispute"] = {"gamma": winner, "binance": computed}; continue
            won = position["side"] == winner; payout = position["shares"] if won else 0.0
            account = ledger["accounts"][position["account"]]; account["cash_usd"] += payout
            closed = {**position, "status": "closed", "closed_at": iso(at), "settlement_status": "settled", "resolution": winner,
                      "binance_open": candle["open"], "binance_close": candle["close"], "payout_usd": payout,
                      "net_pnl_usd": payout - position["total_entry_cost"], "trade_roi": (payout - position["total_entry_cost"]) / position["total_entry_cost"]}
            ledger["open_positions"].remove(position); ledger["closed_positions"].append(closed)
            ledger["paper_orders"].append({"paper_order_id": position["paper_trade_id"] + "-SETTLE", "paper_trade_id": position["paper_trade_id"], "type": "RESOLUTION", "status": "FILLED", "average_fill_price": 1.0 if won else 0.0, "executed_shares": position["shares"], "notional_usd": payout, "commission_usd": 0.0, **SAFE})
            results.append(closed)
        except Exception as exc:
            results.append({"paper_trade_id": position["paper_trade_id"], "status": "settlement_fetch_failed", "error": type(exc).__name__})
    update_equity(ledger); return results


def update_equity(ledger: dict[str, Any]) -> None:
    for name, account in ledger["accounts"].items():
        marked = sum(float(position.get("mark_to_bid_usd", position["total_entry_cost"])) for position in ledger["open_positions"] if position["account"] == name)
        account["equity_usd"] = account["cash_usd"] + marked; account["high_watermark_usd"] = max(account["high_watermark_usd"], account["equity_usd"])
        account["max_drawdown_pct"] = min(account["max_drawdown_pct"], account["equity_usd"] / account["high_watermark_usd"] - 1)


def refresh_coverage(state: dict[str, Any], at: datetime, fetch: Callable[[str], Any]) -> None:
    """A shadow hour counts as complete only after its terminal outcome is public."""
    for condition_id, row in state.get("hourly_coverage", {}).items():
        if row.get("resolved") or (parse_time(row.get("end")) or at) >= at:
            continue
        try:
            payload = fetch(f"{GAMMA}/markets?" + urlencode([("condition_ids", condition_id), ("limit", 1)]))
            market = payload[0] if isinstance(payload, list) and payload else None
            winner = terminal_outcome(market or {})
            if winner:
                row.update({"resolved": True, "resolution": winner, "resolved_at": iso(at)})
        except Exception as exc:
            row["resolution_error"] = type(exc).__name__


def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    proportion = wins / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    return max(0.0, (centre - radius) / denominator)


def recommendation_readiness(audit: dict[str, Any], model: dict[str, Any] | None,
                             policy: dict[str, Any] | None) -> dict[str, Any]:
    """Return a paper-evidence status; this is never an order or live permission."""
    settings = (policy or {}).get("recommendation_gate", {})
    minimum = int(settings.get("min_closed_paper_trades", 30))
    min_wilson = float(settings.get("min_wilson_lower_bound", 0.55))
    wins, closed = int(audit["wins"]), int(audit["closed_trades"])
    lower = wilson_lower_bound(wins, closed)
    blockers = []
    if closed < minimum:
        blockers.append("insufficient_forward_paper_trades")
    if lower is None or lower < min_wilson:
        blockers.append("forward_win_rate_confidence_too_low")
    if float(audit["net_pnl_usd"]) <= 0:
        blockers.append("forward_net_pnl_not_positive")
    if not bool((model or {}).get("approved")):
        blockers.append("model_not_fully_approved")
    return {"status": "calibrated_paper_candidate" if not blockers else "paper_only",
            "closed_trade_minimum": minimum, "win_rate_wilson_95_lower": lower,
            "minimum_wilson_lower": min_wilson, "blockers": blockers, **SAFE}


def audit_ledger(ledger: dict[str, Any], state: dict[str, Any], model: dict[str, Any] | None,
                 policy: dict[str, Any] | None = None) -> dict[str, Any]:
    closed = ledger.get("closed_positions", [])
    pnl = sum(float(row.get("net_pnl_usd", 0)) for row in closed)
    cost = sum(float(row.get("total_entry_cost", 0)) for row in closed)
    wins = sum(float(row.get("net_pnl_usd", 0)) > 0 for row in closed)
    scored = [(float(row["model_probability_at_entry"]), int(str(row.get("resolution")) == str(row.get("side")))) for row in closed if row.get("model_probability_at_entry") is not None]
    brier = sum((p - y) ** 2 for p, y in scored) / len(scored) if scored else None
    order_index = {row.get("paper_trade_id"): row for row in ledger.get("paper_orders", []) if row.get("type") == "MARKET" and row.get("status") == "FILLED"}
    mismatches = []
    for row in [*ledger.get("open_positions", []), *closed]:
        order = order_index.get(row.get("paper_trade_id"))
        if not order or abs(float(order.get("average_fill_price", 0)) * float(order.get("executed_shares", 0)) + float(order.get("commission_usd", 0)) - float(row.get("total_entry_cost", -1))) > .00002:
            mismatches.append(row.get("paper_trade_id"))
    resolved = [row for row in state.get("hourly_coverage", {}).values() if row.get("resolved")]
    expected_per_market = len((policy or {}).get("shadow_checkpoint_minutes", (policy or {}).get("checkpoint_minutes", [0, 10, 20, 30, 40, 50])))
    expected = expected_per_market * len(resolved); captured = sum(len(row.get("checkpoints", [])) for row in resolved)
    audit = {"schema_version": (policy or {}).get("audit_schema_version", "btc-hourly-paper-audit-v1"), "created_at": iso(), "closed_trades": len(closed), "wins": wins,
            "direction_accuracy": wins / len(closed) if closed else None, "net_pnl_usd": pnl, "trade_roi": pnl / cost if cost else None,
            "brier": brier, "open_positions": len(ledger.get("open_positions", [])), "accounts": ledger.get("accounts"),
            "resolved_hours": len(resolved), "checkpoint_coverage": captured / expected if expected else 0.0,
            "model_approved": bool((model or {}).get("approved")), "integrity_ok": not mismatches,
            "integrity_mismatches": mismatches, **SAFE}
    audit["recommendation_readiness"] = recommendation_readiness(audit, model, policy)
    return audit


def checkpoint_for_market(at: datetime, start: datetime, end: datetime, policy: dict[str, Any]) -> int | None:
    """Return the latest configured checkpoint already observable at ``at``."""
    elapsed_minutes = (at - start).total_seconds() / 60
    if elapsed_minutes < 0 or at >= end:
        return None
    valid = [int(value) for value in policy["checkpoint_minutes"] if int(value) <= elapsed_minutes]
    return max(valid) if valid else None


def preflight(base: Path = DATA_DIR) -> dict[str, Any]:
    try:
        policy = load_policy(); self_check = self_test(); ledger = read_json(base / "ledger.json")
        ledger_safe = ledger is None or all(ledger.get(key) is value for key, value in SAFE.items())
        result = {"status": "ok" if ledger_safe and self_check["status"] == "pass" else "blocked", "policy_version": policy["version"],
                  "self_test": self_check["status"], "ledger_safe": ledger_safe, "protected_artifacts": {str(path): file_hash(path) for path in PROTECTED}, **SAFE}
        return result
    except Exception as exc:
        return {"status": "blocked", "error": f"{type(exc).__name__}:{exc}", **SAFE}


def mark_positions(ledger: dict[str, Any], books: dict[str, dict[str, Any]]) -> None:
    for position in ledger["open_positions"]:
        book = books.get(position["token_id"]); bids = levels(book or {}, "bids")
        if bids:
            price = bids[0][0]; position["mark_to_bid_usd"] = max(0.0, position["shares"] * price - fee_usd(position["shares"], price, position["fee_rate"]))
            position["marked_at"] = iso()
    update_equity(ledger)


def model_decision(model: dict[str, Any] | None, feature_row: dict[str, float], checkpoint: int) -> dict[str, Any]:
    record = (model or {}).get("checkpoints", {}).get(str(checkpoint), {})
    if record.get("status") != "approved": return {"status": "model_not_approved", "reasons": [record.get("status", "model_missing")]}
    probability, lower, upper = predict(record["model"], feature_row, int(record["support"]))
    return {"status": "ok", "p_up": probability, "p_down": 1 - probability, "p_up_low": lower, "p_up_high": upper,
            "p_down_low": 1 - upper, "p_down_high": 1 - lower, "base_rate": record["base_rate"], "model_version": model["model_version"]}


def choose_entry(decision: dict[str, Any], books: dict[str, dict[str, Any]], tokens: dict[str, str], market: dict[str, Any], policy: dict[str, Any], ledger: dict[str, Any], expires_at: datetime) -> dict[str, Any]:
    if decision.get("status") != "ok": return {"action": "PASS", "reasons": decision.get("reasons", [decision.get("status")])}
    if any(item["condition_id"] == market["conditionId"] for item in ledger["open_positions"]): return {"action": "PASS", "reasons": ["position_already_open"]}
    if (expires_at - now()).total_seconds() / 60 < policy["min_minutes_to_expiry"]: return {"action": "PASS", "reasons": ["too_close_to_expiry"]}
    fee_rate = market.get("fee_rate")
    if fee_rate is None: return {"action": "PASS", "reasons": ["fee_schedule_missing"]}
    candidates = []
    for side in ("UP", "DOWN"):
        account_fills = {}; failures = []
        for name, settings in policy["accounts"].items():
            account = ledger["accounts"][name]; cap = account["equity_usd"] * settings["max_equity_fraction"]
            if settings["max_notional_usd"] is not None: cap = min(cap, settings["max_notional_usd"])
            cap = min(cap, account["cash_usd"])
            fill = buy_for_budget(books[tokens[side]], cap, fee_rate, policy["extra_entry_slippage_bps"])
            if not fill.get("fillable"): failures.append(f"{name}:{fill.get('reason')}")
            else: account_fills[name] = fill
        if not account_fills: candidates.append({"side": side, "failures": failures}); continue
        reference = next(iter(account_fills.values()))
        probability, lower = decision[f"p_{side.lower()}"], decision[f"p_{side.lower()}_low"]
        cost = all_in_cost_per_share(reference); edge, lower_edge = probability - cost, lower - cost
        if reference["spread"] > policy["max_spread_per_share"]: failures.append("spread_too_wide")
        if edge < policy["min_point_edge_per_share"]: failures.append("point_edge_too_small")
        if lower_edge < policy["min_lower_edge_per_share"]: failures.append("lower_edge_not_positive")
        candidates.append({"side": side, "fills": account_fills, "cost_per_share": cost, "edge": edge, "lower_edge": lower_edge, "failures": failures})
    eligible = [row for row in candidates if not row.get("failures")]
    return max(eligible, key=lambda row: row["edge"]) if eligible else {"action": "PASS", "reasons": sorted({reason for row in candidates for reason in row.get("failures", [])}) or ["no_eligible_side"], "candidates": candidates}


def create_positions(entry: dict[str, Any], market: dict[str, Any], start: datetime, end: datetime, checkpoint: int,
                     decision: dict[str, Any], ledger: dict[str, Any], trade_prefix: str = "btc-hourly") -> list[str]:
    opened = []
    for account, fill in entry["fills"].items():
        account_state = ledger["accounts"][account]; trade_id = sha([market["conditionId"], checkpoint, account, iso()])[:24]
        position = {"paper_trade_id": f"{trade_prefix}-{trade_id}", "account": account, "condition_id": market["conditionId"], "market_id": market.get("id"),
                    "question": market.get("question"), "side": entry["side"], "token_id": entry["token_id"], "market_start": iso(start), "market_end": iso(end),
                    "opened_at": iso(), "checkpoint_minute": checkpoint, "entry_price": fill["fill_price"], "raw_vwap": fill["raw_vwap"], "shares": fill["shares"],
                    "fills": fill["fills"], "trade_value": fill["trade_value"], "entry_fee_usd": fill["entry_fee_usd"], "total_entry_cost": fill["total_entry_cost"],
                    "fee_rate": market["fee_rate"], "spread_at_entry": fill["spread"], "price_impact_at_entry": fill["price_impact"], "min_order_size": fill["min_order_size"],
                    "model_probability_at_entry": decision[f"p_{entry['side'].lower()}"], "confidence_low_at_entry": decision[f"p_{entry['side'].lower()}_low"],
                    "model_version": decision["model_version"], "status": "open", **SAFE}
        ledger["open_positions"].append(position); account_state["cash_usd"] -= fill["total_entry_cost"]
        ledger["paper_orders"].append({"paper_order_id": position["paper_trade_id"] + "-BUY", "paper_trade_id": position["paper_trade_id"], "type": "MARKET", "status": "FILLED", "side": "BUY", "outcome_side": entry["side"], "token_id": entry["token_id"], "average_fill_price": fill["fill_price"], "executed_shares": fill["shares"], "notional_usd": fill["trade_value"], "commission_usd": fill["entry_fee_usd"], "book_vwap": fill["raw_vwap"], "spread_per_share": fill["spread"], "price_impact_per_share": fill["price_impact"], **SAFE})
        opened.append(position["paper_trade_id"])
    update_equity(ledger); return opened


def report_markdown(result: dict[str, Any]) -> str:
    lines = ["# BTC Hourly Polymarket Paper", "", f"- Time: {result['created_at']}", f"- Phase: `{result['phase']}`", f"- Status: `{result['status']}`", f"- Action: `{result.get('action', 'PASS')}`", f"- Settled: {len(result.get('settled', []))}", f"- Opened: {len(result.get('opened', []))}", f"- Protected ledgers unchanged: `{str(result['protected_artifacts_unchanged']).lower()}`", ""]
    if result.get("reasons"): lines.extend(["## Reasons", "", *[f"- {reason}" for reason in result["reasons"]], ""])
    return "\n".join(lines)


def audit_markdown(audit: dict[str, Any]) -> str:
    accounts = audit.get("accounts", {})
    lines = ["# BTC Hourly Paper Daily Audit", "", f"- Generated: {audit['created_at']}", f"- Closed trades: {audit['closed_trades']}",
             f"- Net PnL: {audit['net_pnl_usd']:.6f} USDC", f"- Trade ROI: {audit['trade_roi']}", f"- Brier: {audit['brier']}",
             f"- Checkpoint coverage: {audit['checkpoint_coverage']:.2%}", f"- Ledger integrity: `{str(audit['integrity_ok']).lower()}`", "", "## Accounts", ""]
    lines.extend(f"- {name}: cash {row['cash_usd']:.6f}, equity {row['equity_usd']:.6f}, max drawdown {row['max_drawdown_pct']:.2%}" for name, row in accounts.items())
    return "\n".join(lines) + "\n"


def cycle(base: Path = DATA_DIR, at: datetime | None = None, fetch: Callable[[str], Any] = public_json,
          fetch_binance: Callable[[str, dict[str, Any]], Any] = get_binance) -> dict[str, Any]:
    at = at or now(); policy = load_policy(); run_lock = base / "cycle.lock.json"; guarded = lock(run_lock)
    if not guarded["acquired"]: return {"status": "SKIPPED_LOCKED", "created_at": iso(at), **SAFE, **guarded}
    before = {str(path): file_hash(path) for path in PROTECTED}
    try:
        state_path, ledger_path, model_path = base / "state.json", base / "ledger.json", base / "model.json"
        state = read_json(state_path, {"shadow_started_at": iso(at), "hourly_coverage": {}, "seen_checkpoints": []})
        refresh_coverage(state, at, fetch)
        history = collect_history(base, policy, at, fetch_binance=fetch_binance)
        if history.get("complete") and (not model_path.exists() or policy.get("retrain_on_each_cycle", False)):
            train_history(base, policy)
        ledger, model = load_ledger(ledger_path, policy), read_json(model_path)
        settled = settle_open(ledger, at, fetch)
        for settled_row in settled:
            if settled_row.get("status") != "settlement_fetch_failed" and settled_row.get("condition_id") in state.get("hourly_coverage", {}):
                state["hourly_coverage"][settled_row["condition_id"]]["resolved"] = settled_row.get("settlement_status") == "settled"
        market_ctx, failures = discover_market(at, fetch)
        current_phase = phase(state, model, at, policy)
        result = {"schema_version": policy.get("cycle_schema_version", "btc-hourly-paper-cycle-v1"), "created_at": iso(at), "phase": current_phase, "status": "ok", "settled": settled, "history": history, "opened": [], "action": "PASS", "reasons": list(failures), **SAFE}
        if not market_ctx:
            result["status"] = "degraded"; result["reasons"].append("market_discovery_failed")
        else:
            market, start, end, tokens = market_ctx["market"], market_ctx["start"], market_ctx["end"], market_ctx["tokens"]
            checkpoint = checkpoint_for_market(at, start, end, policy)
            if checkpoint is None:
                result["reasons"].append("before_first_checkpoint")
                checkpoint = -1
            key = f"{market['conditionId']}:{checkpoint}"
            coverage = state["hourly_coverage"].setdefault(market["conditionId"], {"start": iso(start), "end": iso(end), "checkpoints": [], "resolved": False})
            if checkpoint < 0:
                pass
            elif key in state["seen_checkpoints"]:
                result["action"] = "PASS"; result["reasons"].append("duplicate_checkpoint")
            else:
                state["seen_checkpoints"].append(key); coverage["checkpoints"].append(checkpoint)
                aux = {}; books = {}; fee_rate = None
                try:
                    clob_info = fetch(f"{CLOB}/clob-markets/{market['conditionId']}"); fee = clob_info.get("fd", {}) if isinstance(clob_info, dict) else {}
                    fee_rate = float(fee["r"]) if fee.get("r") is not None else None
                    for side, token in tokens.items(): books[token] = fetch(f"{CLOB}/book?{urlencode({'token_id': token})}")
                    aux = {"clob_market_info": clob_info, "books": books}
                except Exception as exc: result["reasons"].append(f"clob_fetch_failed:{type(exc).__name__}")
                data, data_failures = fetch_market_data(at, fetch_binance); result["reasons"].extend(data_failures)
                market = {**market, "conditionId": market.get("conditionId") or market.get("id"), "fee_rate": fee_rate}
                feature_row = None
                if data and checkpoint in policy["entry_checkpoint_minutes"]:
                    try:
                        feature_row = features(data["minutes"], data["hours"], start, checkpoint)
                    except (ValueError, IndexError, ZeroDivisionError):
                        result["reasons"].append("feature_capture_failed")
                snapshot = {"snapshot_id": key, "captured_at": iso(at), "market": market, "market_start": iso(start), "market_end": iso(end), "checkpoint_minute": checkpoint, "tokens": tokens, "rules_clear": market_ctx["rules_clear"], "rule_failures": market_ctx["rule_failures"], "clob": aux, "binance": data, "feature_row": feature_row, "information_context": {"status": "degraded_missing_verified_feed", "risk_veto": False}, **SAFE}
                snapshot["raw_evidence_sha256"] = sha(snapshot); write_json(base / "snapshots" / f"{key.replace(':', '_')}.json", snapshot)
                if not market_ctx["rules_clear"]: result["reasons"].append("PASS_RULE_AMBIGUOUS")
                if checkpoint == 0: result["reasons"].append("opening_checkpoint_observe_only")
                if checkpoint in policy["entry_checkpoint_minutes"] and market_ctx["rules_clear"] and data and len(books) == 2 and current_phase == "paper":
                    try:
                        row = feature_row or features(data["minutes"], data["hours"], start, checkpoint); decision = model_decision(model, row, checkpoint)
                        entry = choose_entry(decision, books, tokens, market, policy, ledger, end); result["decision"] = decision
                        if entry.get("action") != "PASS" and not entry.get("failures"):
                            entry["token_id"] = tokens[entry["side"]]; result["opened"] = create_positions(entry, market, start, end, checkpoint, decision, ledger, policy.get("paper_trade_prefix", "btc-hourly")); result["action"] = "PAPER_BUY"
                        else: result["reasons"].extend(entry.get("reasons", entry.get("failures", ["entry_gate_failed"])))
                    except Exception as exc: result["reasons"].append(f"decision_failed:{type(exc).__name__}")
                elif current_phase != "paper": result["reasons"].append("shadow_or_model_gate_blocks_entry")
            coverage["resolved"] = bool(settled) or at >= end
        mark_positions(ledger, books if "books" in locals() else {})
        ledger["events"].append({"at": iso(at), "type": "cycle", "action": result["action"], "opened": result["opened"], "settled": [item.get("paper_trade_id") for item in settled], **SAFE}); ledger["updated_at"] = iso(at)
        after = {str(path): file_hash(path) for path in PROTECTED}; result["protected_artifacts_unchanged"] = before == after
        if not result["protected_artifacts_unchanged"]: result["status"] = "blocked_safety"; result["reasons"].append("protected_ledger_changed")
        audit = audit_ledger(ledger, state, model, policy); result["audit"] = audit
        if not audit["integrity_ok"]: result["status"] = "blocked_safety"; result["reasons"].append("ledger_recalculation_failed")
        write_json(ledger_path, ledger); write_json(state_path, state); write_json(base / "latest-cycle.json", result); write_json(base / "latest-audit.json", audit); append_jsonl(base / "events.jsonl", result)
        REPORT_DIR.mkdir(parents=True, exist_ok=True); (REPORT_DIR / "LATEST_BTC_HOURLY_PAPER.md").write_text(report_markdown(result), encoding="utf-8")
        day = at.astimezone(ZoneInfo(policy["timezone"])).date().isoformat()
        (REPORT_DIR / f"{day}_BTC_HOURLY_PAPER_AUDIT.md").write_text(audit_markdown(audit), encoding="utf-8")
        return result
    finally: unlock(run_lock)


def self_test() -> dict[str, Any]:
    book = {"bids": [{"price": ".49", "size": "100"}], "asks": [{"price": ".50", "size": "100"}], "min_order_size": "5"}
    fill = buy_for_budget(book, 3, .07, 5); assert fill["fillable"] and fill["shares"] >= 5 and fill["total_entry_cost"] <= 3
    assert fee_usd(100, .5, .07) == 1.75
    assert not buy_for_budget(book, 1, .07, 5)["fillable"]
    fixture = {"outcomes": '["Up","Down"]', "clobTokenIds": '["u","d"]'}; assert outcomes(fixture)[0] == {"UP": "u", "DOWN": "d"}
    return {"status": "pass", "tests": ["fee_formula", "min_order", "depth_vwap", "up_down_mapping"], **SAFE}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("command", choices=("cycle", "self-test", "collect-history", "train", "audit", "preflight"))
    args = parser.parse_args()
    policy = load_policy()
    if args.command == "self-test": result = self_test()
    elif args.command == "collect-history": result = collect_history(args.data_dir, policy, now())
    elif args.command == "train": result = train_history(args.data_dir, policy)
    elif args.command == "audit": result = audit_ledger(load_ledger(args.data_dir / "ledger.json", policy), read_json(args.data_dir / "state.json", {}), read_json(args.data_dir / "model.json"), policy)
    elif args.command == "preflight": result = preflight(args.data_dir)
    else: result = cycle(args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "pass", "SKIPPED_LOCKED", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
