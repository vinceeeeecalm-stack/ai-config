#!/usr/bin/env python3
"""Append-only evidence lanes for tactical 1–7 day research and Paper trades.

Observation samples measure what the scanner saw, including rejected setups.
Trade samples measure only completed, reproducible Paper fills.  Neither lane
can authorize a formal action or contribute to live profit attribution.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class EvidenceContractError(ValueError):
    """Raised when a record would weaken evidence or lane isolation."""


OBSERVATION_FIELDS = {
    "schema_version", "observation_id", "candidate_id", "setup_id", "symbol",
    "asset_class", "request_mode", "strategy_family", "strategy_version",
    "snapshot_id", "config_digest", "source_digest", "observed_at", "price",
    "price_as_of", "rank", "accepted", "rejection_reasons", "entry_trigger_state",
    "next_check_at", "review_due_at", "evidence_ids", "historical_summary",
    "formal_action_eligible", "paper_entry_eligible", "paper_live_separated",
    "live_orders_enabled", "private_api_used",
}
TRADE_FIELDS = {
    "schema_version", "trade_sample_id", "observation_id", "candidate_id", "symbol",
    "asset_class", "request_mode", "evidence_mode", "strategy_version", "snapshot_id",
    "config_digest", "source_digest", "entry_receipt", "exit_receipt", "outcome",
    "mfe_pct", "mae_pct", "holding_hours", "net_pnl_usd", "net_roi_pct",
    "formal_action_eligible", "real_money_roi_eligible", "business_ready_eligible",
    "paper_live_separated", "live_orders_enabled", "private_api_used",
}
PAPER_RECEIPT_FIELDS = {
    "schema_version", "receipt_id", "baseline_id", "trade_id", "symbol",
    "request_mode", "evidence_mode", "strategy_version", "snapshot_id", "event",
    "quantity", "price", "fee_usd", "slippage_usd", "executed_at",
    "live_orders_enabled",
}


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceContractError("timestamp must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceContractError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise EvidenceContractError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require(record: dict[str, Any], fields: list[str]) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise EvidenceContractError("missing required fields: " + ", ".join(missing))


def _reject_unknown(record: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise EvidenceContractError(f"{label} contains non-allowlisted fields: {', '.join(unknown)}")


def _require_digest(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if not isinstance(value, str) or len(value) != 64:
        raise EvidenceContractError(f"{field} must be a 64-hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise EvidenceContractError(f"{field} must be a 64-hex digest") from exc


def _require_false(record: dict[str, Any], fields: list[str]) -> None:
    for field in fields:
        if record.get(field) is not False:
            raise EvidenceContractError(f"{field} must be false")


def validate_observation(record: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(record, OBSERVATION_FIELDS, "observation")
    _require(record, [
        "schema_version", "observation_id", "candidate_id", "setup_id", "symbol",
        "asset_class", "request_mode", "strategy_family", "strategy_version",
        "snapshot_id", "config_digest", "source_digest", "observed_at", "price",
        "price_as_of", "rank", "accepted", "rejection_reasons", "entry_trigger_state",
        "next_check_at", "review_due_at", "evidence_ids", "formal_action_eligible",
        "paper_entry_eligible", "paper_live_separated", "live_orders_enabled",
        "private_api_used",
    ])
    if record["schema_version"] != "ObservationSampleV1":
        raise EvidenceContractError("observation schema must be ObservationSampleV1")
    if record["asset_class"] != "crypto" or record["request_mode"] != "tactical_1_7d":
        raise EvidenceContractError("observation must be crypto tactical_1_7d")
    if not isinstance(record["accepted"], bool):
        raise EvidenceContractError("accepted must be boolean")
    if not isinstance(record["rejection_reasons"], list):
        raise EvidenceContractError("rejection_reasons must be an array")
    if not record["accepted"] and not record["rejection_reasons"]:
        raise EvidenceContractError("rejected observation needs a reason")
    if record["paper_live_separated"] is not True:
        raise EvidenceContractError("paper_live_separated must be true")
    _require_false(record, ["formal_action_eligible", "live_orders_enabled", "private_api_used"])
    if record["paper_entry_eligible"] not in {True, False}:
        raise EvidenceContractError("paper_entry_eligible must be boolean")
    if record["paper_entry_eligible"] and not record["accepted"]:
        raise EvidenceContractError("rejected observation cannot be Paper-entry eligible")
    if not isinstance(record["evidence_ids"], list) or not record["evidence_ids"]:
        raise EvidenceContractError("observation needs evidence_ids")
    if not isinstance(record["rank"], int) or record["rank"] < 1:
        raise EvidenceContractError("rank must be a positive integer")
    price = float(record["price"])
    if not math.isfinite(price) or price <= 0:
        raise EvidenceContractError("price must be positive and finite")
    for field in ["observed_at", "price_as_of", "next_check_at", "review_due_at"]:
        _parse_time(record[field])
    if _parse_time(record["next_check_at"]) < _parse_time(record["observed_at"]):
        raise EvidenceContractError("next_check_at cannot precede observed_at")
    if _parse_time(record["review_due_at"]) < _parse_time(record["next_check_at"]):
        raise EvidenceContractError("review_due_at cannot precede next_check_at")
    _require_digest(record, "config_digest")
    _require_digest(record, "source_digest")
    return record


def _validate_paper_receipt(receipt: dict[str, Any], expected_event: str) -> None:
    _reject_unknown(receipt, PAPER_RECEIPT_FIELDS, "Paper receipt")
    _require(receipt, [
        "schema_version", "receipt_id", "baseline_id", "trade_id", "symbol",
        "request_mode", "evidence_mode", "strategy_version", "snapshot_id", "event",
        "quantity", "price", "fee_usd", "slippage_usd", "executed_at",
        "live_orders_enabled",
    ])
    if receipt["schema_version"] != "PaperExecutionReceiptV1":
        raise EvidenceContractError("trade sample receipts must be PaperExecutionReceiptV1")
    if receipt["request_mode"] != "tactical_1_7d" or receipt["evidence_mode"] != "paper":
        raise EvidenceContractError("receipt must be isolated tactical_1_7d Paper evidence")
    if receipt["event"] != expected_event:
        raise EvidenceContractError(f"expected {expected_event} receipt")
    if receipt["live_orders_enabled"] is not False:
        raise EvidenceContractError("Paper receipt cannot enable live orders")
    for field in ["quantity", "price"]:
        value = float(receipt[field])
        if not math.isfinite(value) or value <= 0:
            raise EvidenceContractError(f"receipt {field} must be positive and finite")
    for field in ["fee_usd", "slippage_usd"]:
        value = float(receipt[field])
        if not math.isfinite(value) or value < 0:
            raise EvidenceContractError(f"receipt {field} must be non-negative and finite")
    _parse_time(receipt["executed_at"])


def validate_trade_sample(record: dict[str, Any], observation: dict[str, Any] | None = None) -> dict[str, Any]:
    _reject_unknown(record, TRADE_FIELDS, "trade sample")
    _require(record, [
        "schema_version", "trade_sample_id", "observation_id", "candidate_id", "symbol",
        "asset_class", "request_mode", "evidence_mode", "strategy_version", "snapshot_id",
        "config_digest", "source_digest", "entry_receipt", "exit_receipt", "outcome",
        "mfe_pct", "mae_pct", "holding_hours", "net_pnl_usd", "net_roi_pct",
        "formal_action_eligible", "real_money_roi_eligible", "business_ready_eligible",
        "paper_live_separated", "live_orders_enabled", "private_api_used",
    ])
    if record["schema_version"] != "TradeSampleV1":
        raise EvidenceContractError("trade schema must be TradeSampleV1")
    if record["asset_class"] != "crypto" or record["request_mode"] != "tactical_1_7d":
        raise EvidenceContractError("trade sample must be crypto tactical_1_7d")
    if record["evidence_mode"] != "paper" or record["paper_live_separated"] is not True:
        raise EvidenceContractError("trade sample must stay in the Paper lane")
    if record["outcome"] not in {"hit", "failed", "flat", "time_exit"}:
        raise EvidenceContractError("unsupported Paper trade outcome")
    for field in ["mfe_pct", "mae_pct", "holding_hours", "net_pnl_usd", "net_roi_pct"]:
        if not math.isfinite(float(record[field])):
            raise EvidenceContractError(f"{field} must be finite")
    _require_false(record, [
        "formal_action_eligible", "real_money_roi_eligible", "business_ready_eligible",
        "live_orders_enabled", "private_api_used",
    ])
    _require_digest(record, "config_digest")
    _require_digest(record, "source_digest")
    entry, exit_receipt = record["entry_receipt"], record["exit_receipt"]
    _validate_paper_receipt(entry, "entry")
    _validate_paper_receipt(exit_receipt, "exit")
    for field in ["baseline_id", "trade_id", "symbol", "strategy_version", "snapshot_id"]:
        if entry[field] != exit_receipt[field]:
            raise EvidenceContractError(f"entry/exit receipt mismatch: {field}")
    for field in ["symbol", "strategy_version", "snapshot_id"]:
        if record[field] != entry[field]:
            raise EvidenceContractError(f"trade sample/receipt mismatch: {field}")
    if abs(float(entry["quantity"]) - float(exit_receipt["quantity"])) > 1e-12:
        raise EvidenceContractError("closed trade sample requires equal entry and exit quantity")
    opened, closed = _parse_time(entry["executed_at"]), _parse_time(exit_receipt["executed_at"])
    holding_hours = (closed - opened).total_seconds() / 3600
    if holding_hours <= 0 or holding_hours > 7 * 24:
        raise EvidenceContractError("trade holding time must be within 1–7 days")
    quantity = float(entry["quantity"])
    invested = quantity * float(entry["price"])
    costs = sum(float(item[field]) for item in [entry, exit_receipt] for field in ["fee_usd", "slippage_usd"])
    expected_pnl = quantity * (float(exit_receipt["price"]) - float(entry["price"])) - costs
    expected_roi = expected_pnl / invested * 100
    if abs(float(record["holding_hours"]) - holding_hours) > 1e-6:
        raise EvidenceContractError("holding_hours does not match receipts")
    if abs(float(record["net_pnl_usd"]) - expected_pnl) > 1e-6:
        raise EvidenceContractError("net_pnl_usd does not match receipts")
    if abs(float(record["net_roi_pct"]) - expected_roi) > 1e-6:
        raise EvidenceContractError("net_roi_pct does not match receipts")
    if observation is not None:
        validate_observation(observation)
        if not observation["accepted"] or not observation["paper_entry_eligible"]:
            raise EvidenceContractError("TradeSample requires an accepted Paper-entry-eligible observation")
        for field in ["observation_id", "candidate_id", "symbol", "strategy_version", "snapshot_id", "config_digest", "source_digest"]:
            observed_value = observation[field]
            sample_value = record[field]
            if observed_value != sample_value:
                raise EvidenceContractError(f"trade sample/observation mismatch: {field}")
    return record


def append_record(path: Path, record: dict[str, Any], *, id_field: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                existing = json.loads(line)
                if existing.get(id_field) != record[id_field]:
                    continue
                if _canonical_digest(existing) == _canonical_digest(record):
                    return "NO_UPDATE"
                raise EvidenceContractError(f"append-only id collision: {record[id_field]}")
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
    return "APPENDED"


def append_observation(path: Path, record: dict[str, Any]) -> str:
    return append_record(path, validate_observation(record), id_field="observation_id")


def build_scanner_observation(
    signal: dict[str, Any],
    *,
    rank: int,
    snapshot_id: str,
    strategy_version: str,
    config_digest: str,
    source_digest: str,
    observed_at: str,
    committee_degraded: bool,
) -> dict[str, Any]:
    """Normalize one frozen scanner candidate into the observation lane."""

    observed = _parse_time(observed_at)
    history = signal.get("historical_comparison") or {}
    reasons: list[str] = []
    conservative_ev = history.get("conservative_expected_value_pct")
    if conservative_ev is None or float(conservative_ev) <= 0:
        reasons.append("positive_conservative_ev_gate_failed")
    if signal.get("stage") not in {"breakout_confirmed", "confirmed_impulse"}:
        reasons.append("current_impulse_not_confirmed")
    if not signal.get("breakout_close"):
        reasons.append("closed_breakout_not_confirmed")
    if signal.get("official_event_status") not in {"verified", "confirmed_1_7d"}:
        reasons.append("verified_1_7d_catalyst_missing")
    if committee_degraded:
        reasons.append("research_committee_degraded")
    reasons = sorted(set(reasons))
    accepted = not reasons
    evidence_ids = list(history.get("evidence_ids") or [])
    evidence_ids.extend((signal.get("risk_adjusted_path") or {}).get("evidence_ids") or [])
    evidence_ids.append(f"public-spot-snapshot:{snapshot_id}:{signal.get('symbol')}")
    evidence_ids = sorted(set(str(item) for item in evidence_ids if item))
    identity = {
        "snapshot_id": snapshot_id,
        "symbol": signal.get("symbol"),
        "setup_id": signal.get("impulse_id") or f"rank-{rank}",
        "strategy_version": strategy_version,
    }
    return validate_observation({
        "schema_version": "ObservationSampleV1",
        "observation_id": f"obs-{_canonical_digest(identity)[:20]}",
        "candidate_id": str(signal.get("symbol")),
        "setup_id": str(identity["setup_id"]),
        "symbol": str(signal.get("symbol")),
        "asset_class": "crypto",
        "request_mode": "tactical_1_7d",
        "strategy_family": "impulse_capture",
        "strategy_version": strategy_version,
        "snapshot_id": snapshot_id,
        "config_digest": config_digest,
        "source_digest": source_digest,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "price": float(signal["current_price"]),
        "price_as_of": observed.isoformat().replace("+00:00", "Z"),
        "rank": rank,
        "accepted": accepted,
        "rejection_reasons": reasons,
        "entry_trigger_state": "triggered" if signal.get("breakout_close") else "not_triggered",
        "next_check_at": (observed + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "review_due_at": (observed + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        "evidence_ids": evidence_ids,
        "historical_summary": {
            key: history.get(key)
            for key in [
                "sample_size", "out_of_sample_win_rate_interval_pct",
                "conservative_expected_value_pct", "expected_return_pct",
                "profit_factor", "max_drawdown_pct", "walk_forward_positive_windows",
                "walk_forward_total_windows",
            ]
        },
        "formal_action_eligible": False,
        "paper_entry_eligible": accepted,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    })


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_trade_sample(
    path: Path,
    record: dict[str, Any],
    *,
    observation_ledger: Path,
) -> str:
    observations = {
        item["observation_id"]: validate_observation(item)
        for item in load_records(observation_ledger)
    }
    observation = observations.get(record.get("observation_id"))
    if observation is None:
        raise EvidenceContractError("trade sample requires an existing observation")
    return append_record(
        path,
        validate_trade_sample(record, observation),
        id_field="trade_sample_id",
    )


def summarize(observation_ledger: Path, trade_ledger: Path) -> dict[str, Any]:
    observations = [validate_observation(item) for item in load_records(observation_ledger)]
    observation_by_id = {item["observation_id"]: item for item in observations}
    trades = [
        validate_trade_sample(item, observation_by_id.get(item.get("observation_id")))
        for item in load_records(trade_ledger)
    ]
    return {
        "schema_version": "TacticalEvidenceLedgerSummaryV1",
        "request_mode": "tactical_1_7d",
        "observation_count": len(observations),
        "accepted_observation_count": sum(1 for item in observations if item["accepted"]),
        "rejected_observation_count": sum(1 for item in observations if not item["accepted"]),
        "paper_trade_sample_count": len(trades),
        "live_trade_sample_count": 0,
        "real_money_roi_sample_count": 0,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "symbols": sorted({item["symbol"] for item in observations}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Append-only tactical observation/Paper evidence ledger")
    parser.add_argument("--observation-ledger", required=True)
    parser.add_argument("--trade-ledger", required=True)
    parser.add_argument("--append-observation-json")
    parser.add_argument("--append-trade-json")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    observation_path, trade_path = Path(args.observation_ledger), Path(args.trade_ledger)
    if args.append_observation_json:
        record = json.loads(Path(args.append_observation_json).read_text(encoding="utf-8"))
        print(json.dumps({"status": append_observation(observation_path, record)}, sort_keys=True))
        return 0
    if args.append_trade_json:
        record = json.loads(Path(args.append_trade_json).read_text(encoding="utf-8"))
        print(json.dumps({"status": append_trade_sample(trade_path, record, observation_ledger=observation_path)}, sort_keys=True))
        return 0
    if args.summary:
        print(json.dumps(summarize(observation_path, trade_path), ensure_ascii=False, sort_keys=True))
        return 0
    parser.error("choose --append-observation-json, --append-trade-json or --summary")


if __name__ == "__main__":
    raise SystemExit(main())
