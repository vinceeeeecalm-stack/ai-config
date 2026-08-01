#!/usr/bin/env python3
"""Append-only, human-confirmed execution receipts for PortfolioStateV2."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from v3_portfolio_state import PortfolioFieldObservationV2


VALID_RAILS = {"crypto", "us_equity"}
VALID_SIDES = {"buy", "sell"}
VALID_SETTLEMENT_STATUSES = {"settled", "unsettled"}


def _parse_iso(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}:required_iso_datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name}:invalid_iso_datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name}:timezone_required")
    return parsed


def _positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name}:number_required")
    number = float(value)
    if number <= 0:
        raise ValueError(f"{field_name}:must_be_positive")
    return number


def _non_negative_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name}:number_required")
    number = float(value)
    if number < 0:
        raise ValueError(f"{field_name}:must_be_non_negative")
    return number


@dataclass(frozen=True)
class ExecutionReceiptV2:
    schema_version: str
    receipt_id: str
    source: str
    recommendation_id: str
    rail: str
    asset_class: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    fee_currency: str
    executed_at: str
    post_trade_quantity: float
    post_trade_cash: float | None
    settlement_status: str
    settled_at: str
    evidence_ref: str
    notes: str
    human_confirmed: bool

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ExecutionReceiptV2":
        if payload.get("schema_version") != "ExecutionReceiptV2":
            raise ValueError("schema_version:ExecutionReceiptV2_required")
        rail = str(payload.get("rail") or "")
        asset_class = str(payload.get("asset_class") or "")
        expected_asset_class = "crypto" if rail == "crypto" else "us_equity"
        if rail not in VALID_RAILS:
            raise ValueError(f"rail:unsupported:{rail}")
        if asset_class != expected_asset_class:
            raise ValueError("asset_class:must_match_rail")
        side = str(payload.get("side") or "")
        if side not in VALID_SIDES:
            raise ValueError(f"side:unsupported:{side}")
        settlement_status = str(payload.get("settlement_status") or "")
        if settlement_status not in VALID_SETTLEMENT_STATUSES:
            raise ValueError(
                f"settlement_status:unsupported:{settlement_status}"
            )
        if payload.get("human_confirmed") is not True:
            raise ValueError("human_confirmed:true_required")

        executed_at = str(payload.get("executed_at") or "")
        _parse_iso(executed_at, "executed_at")
        settled_at = str(payload.get("settled_at") or "")
        if settled_at:
            _parse_iso(settled_at, "settled_at")
        if settlement_status == "unsettled" and settled_at:
            raise ValueError("settled_at:not_allowed_while_unsettled")

        post_trade_cash_raw = payload.get("post_trade_cash")
        post_trade_cash = (
            None
            if post_trade_cash_raw is None
            else _non_negative_number(post_trade_cash_raw, "post_trade_cash")
        )
        if settlement_status == "unsettled" and post_trade_cash is not None:
            raise ValueError("post_trade_cash:not_allowed_while_unsettled")

        receipt = cls(
            schema_version="ExecutionReceiptV2",
            receipt_id=str(payload.get("receipt_id") or "").strip(),
            source=str(payload.get("source") or "").strip(),
            recommendation_id=str(payload.get("recommendation_id") or "").strip(),
            rail=rail,
            asset_class=asset_class,
            symbol=str(payload.get("symbol") or "").strip().upper(),
            side=side,
            quantity=_positive_number(payload.get("quantity"), "quantity"),
            price=_positive_number(payload.get("price"), "price"),
            fee=_non_negative_number(payload.get("fee"), "fee"),
            fee_currency=str(payload.get("fee_currency") or "").strip().upper(),
            executed_at=executed_at,
            post_trade_quantity=_non_negative_number(
                payload.get("post_trade_quantity"), "post_trade_quantity"
            ),
            post_trade_cash=post_trade_cash,
            settlement_status=settlement_status,
            settled_at=settled_at,
            evidence_ref=str(payload.get("evidence_ref") or "").strip(),
            notes=str(payload.get("notes") or "").strip(),
            human_confirmed=True,
        )
        receipt.validate()
        return receipt

    def validate(self) -> None:
        for field_name in (
            "receipt_id",
            "source",
            "symbol",
            "fee_currency",
            "evidence_ref",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name}:required")
        if any(character.isspace() for character in self.receipt_id):
            raise ValueError("receipt_id:whitespace_not_allowed")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_execution_receipts(path: Path) -> list[ExecutionReceiptV2]:
    if not path.exists():
        return []
    receipts: list[ExecutionReceiptV2] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            receipt = ExecutionReceiptV2.from_payload(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path}:line_{line_number}:{exc}") from exc
        if receipt.receipt_id in seen:
            raise ValueError(f"receipt_id:duplicate:{receipt.receipt_id}")
        seen.add(receipt.receipt_id)
        receipts.append(receipt)
    return receipts


def append_execution_receipt(
    path: Path, payload: Mapping[str, Any]
) -> ExecutionReceiptV2:
    """Validate and append one immutable receipt while holding an exclusive lock."""

    receipt = ExecutionReceiptV2.from_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing_ids: set[str] = set()
        for line_number, line in enumerate(handle.read().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                existing = ExecutionReceiptV2.from_payload(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{path}:line_{line_number}:{exc}") from exc
            if existing.receipt_id in existing_ids:
                raise ValueError(f"receipt_id:duplicate:{existing.receipt_id}")
            existing_ids.add(existing.receipt_id)
        if receipt.receipt_id in existing_ids:
            raise ValueError(f"receipt_id:duplicate:{receipt.receipt_id}")
        handle.seek(0, os.SEEK_END)
        handle.write(
            json.dumps(
                receipt.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return receipt


def observations_from_execution_receipts(
    receipts: Iterable[ExecutionReceiptV2], *, source: str
) -> list[PortfolioFieldObservationV2]:
    """Convert receipts into highest-precedence holdings and settled cash evidence."""

    observations: list[PortfolioFieldObservationV2] = []
    for receipt in receipts:
        receipt.validate()
        observations.append(
            PortfolioFieldObservationV2(
                key=f"holdings.{receipt.symbol}.quantity",
                value=receipt.post_trade_quantity,
                as_of=receipt.executed_at,
                source=source,
                source_kind="user_trade_confirmation",
                confidence=1.0,
                evidence_id=f"receipt:{receipt.receipt_id}:holding",
            )
        )
        if (
            receipt.settlement_status == "settled"
            and receipt.post_trade_cash is not None
        ):
            cash_key = (
                "cash.crypto.USDT"
                if receipt.rail == "crypto"
                else "cash.us_equity.USD"
            )
            observations.append(
                PortfolioFieldObservationV2(
                    key=cash_key,
                    value=receipt.post_trade_cash,
                    as_of=receipt.settled_at or receipt.executed_at,
                    source=source,
                    source_kind="user_trade_confirmation",
                    confidence=1.0,
                    evidence_id=f"receipt:{receipt.receipt_id}:cash",
                )
            )
    return observations


def latest_receipt_timestamp(
    receipts: Iterable[ExecutionReceiptV2],
) -> str | None:
    timestamps = [
        receipt.settled_at or receipt.executed_at for receipt in receipts
    ]
    if not timestamps:
        return None
    return max(timestamps, key=lambda value: _parse_iso(value, "receipt_timestamp"))


def main() -> int:
    parser = argparse.ArgumentParser(description="ExecutionReceiptV2 ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    append_parser = subparsers.add_parser("append")
    append_parser.add_argument("--ledger", required=True)
    append_parser.add_argument("--payload-json", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--ledger", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--ledger", required=True)

    args = parser.parse_args()
    ledger = Path(args.ledger).expanduser().resolve()
    if args.command == "append":
        payload = json.loads(args.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload_json:object_required")
        receipt = append_execution_receipt(ledger, payload)
        print(
            json.dumps(
                {"status": "appended", "receipt": receipt.to_dict()},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    elif args.command == "list":
        print(
            json.dumps(
                [item.to_dict() for item in load_execution_receipts(ledger)],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        receipts = load_execution_receipts(ledger)
        print(
            json.dumps(
                {"status": "valid", "receipt_count": len(receipts)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
