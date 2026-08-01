#!/usr/bin/env python3
"""Append-only DCA contribution records for settled owner-confirmed cash transfers."""

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


def _positive(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name}:number_required")
    number = float(value)
    if number <= 0:
        raise ValueError(f"{field_name}:must_be_positive")
    return number


def _non_negative(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name}:number_required")
    number = float(value)
    if number < 0:
        raise ValueError(f"{field_name}:must_be_non_negative")
    return number


@dataclass(frozen=True)
class DCAContributionV2:
    schema_version: str
    contribution_id: str
    source_transfer_id: str
    source: str
    source_profit_month: str
    store_closing_id: str
    rail: str
    amount_cny: float
    destination_amount: float
    destination_currency: str
    fx_rate_cny_per_destination_unit: float
    post_transfer_cash: float
    transferred_at: str
    settled_at: str
    evidence_ref: str
    notes: str
    human_confirmed: bool

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DCAContributionV2":
        if payload.get("schema_version") != "DCAContributionV2":
            raise ValueError("schema_version:DCAContributionV2_required")
        if payload.get("human_confirmed") is not True:
            raise ValueError("human_confirmed:true_required")
        rail = str(payload.get("rail") or "")
        if rail not in {"crypto", "us_equity"}:
            raise ValueError(f"rail:unsupported:{rail}")
        destination_currency = str(
            payload.get("destination_currency") or ""
        ).upper()
        expected_currency = "USDT" if rail == "crypto" else "USD"
        if destination_currency != expected_currency:
            raise ValueError("destination_currency:must_match_rail")
        transferred_at = str(payload.get("transferred_at") or "")
        settled_at = str(payload.get("settled_at") or "")
        transferred_time = _parse_iso(transferred_at, "transferred_at")
        settled_time = _parse_iso(settled_at, "settled_at")
        if settled_time < transferred_time:
            raise ValueError("settled_at:cannot_precede_transfer")

        record = cls(
            schema_version="DCAContributionV2",
            contribution_id=str(payload.get("contribution_id") or "").strip(),
            source_transfer_id=str(
                payload.get("source_transfer_id") or ""
            ).strip(),
            source=str(payload.get("source") or "").strip(),
            source_profit_month=str(
                payload.get("source_profit_month") or ""
            ).strip(),
            store_closing_id=str(payload.get("store_closing_id") or "").strip(),
            rail=rail,
            amount_cny=_positive(payload.get("amount_cny"), "amount_cny"),
            destination_amount=_positive(
                payload.get("destination_amount"), "destination_amount"
            ),
            destination_currency=destination_currency,
            fx_rate_cny_per_destination_unit=_positive(
                payload.get("fx_rate_cny_per_destination_unit"),
                "fx_rate_cny_per_destination_unit",
            ),
            post_transfer_cash=_non_negative(
                payload.get("post_transfer_cash"), "post_transfer_cash"
            ),
            transferred_at=transferred_at,
            settled_at=settled_at,
            evidence_ref=str(payload.get("evidence_ref") or "").strip(),
            notes=str(payload.get("notes") or "").strip(),
            human_confirmed=True,
        )
        record.validate()
        return record

    def validate(self) -> None:
        for field_name in (
            "contribution_id",
            "source_transfer_id",
            "source",
            "source_profit_month",
            "store_closing_id",
            "evidence_ref",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name}:required")
        if self.source != "self_operated_store_realized_profit":
            raise ValueError("source:unsupported")
        if not __import__("re").fullmatch(
            r"\d{4}-\d{2}", self.source_profit_month
        ):
            raise ValueError("source_profit_month:yyyy_mm_required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_dca_contributions(path: Path) -> list[DCAContributionV2]:
    if not path.exists():
        return []
    records: list[DCAContributionV2] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = DCAContributionV2.from_payload(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path}:line_{line_number}:{exc}") from exc
        if record.contribution_id in seen:
            raise ValueError(
                f"contribution_id:duplicate:{record.contribution_id}"
            )
        seen.add(record.contribution_id)
        records.append(record)
    return records


def append_dca_contribution(
    path: Path, payload: Mapping[str, Any]
) -> DCAContributionV2:
    record = DCAContributionV2.from_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing_ids: set[str] = set()
        for line_number, line in enumerate(handle.read().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                existing = DCAContributionV2.from_payload(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{path}:line_{line_number}:{exc}") from exc
            existing_ids.add(existing.contribution_id)
        if record.contribution_id in existing_ids:
            raise ValueError(
                f"contribution_id:duplicate:{record.contribution_id}"
            )
        handle.seek(0, os.SEEK_END)
        handle.write(
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return record


def observations_from_dca_contributions(
    records: Iterable[DCAContributionV2], *, source: str
) -> list[PortfolioFieldObservationV2]:
    observations: list[PortfolioFieldObservationV2] = []
    for record in records:
        record.validate()
        key = (
            "cash.crypto.USDT"
            if record.rail == "crypto"
            else "cash.us_equity.USD"
        )
        observations.append(
            PortfolioFieldObservationV2(
                key=key,
                value=record.post_transfer_cash,
                as_of=record.settled_at,
                source=source,
                source_kind="user_cash_transfer_confirmation",
                confidence=1.0,
                evidence_id=f"contribution:{record.contribution_id}:cash",
            )
        )
    return observations


def latest_contribution_timestamp(
    records: Iterable[DCAContributionV2],
) -> str | None:
    timestamps = [record.settled_at for record in records]
    if not timestamps:
        return None
    return max(
        timestamps, key=lambda value: _parse_iso(value, "contribution_timestamp")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="DCAContributionV2 ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("append", "list", "validate"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--ledger", required=True)
        if command == "append":
            command_parser.add_argument("--payload-json", required=True)
    args = parser.parse_args()
    ledger = Path(args.ledger).expanduser().resolve()
    if args.command == "append":
        payload = json.loads(args.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload_json:object_required")
        record = append_dca_contribution(ledger, payload)
        print(
            json.dumps(
                {"status": "appended", "contribution": record.to_dict()},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    elif args.command == "list":
        print(
            json.dumps(
                [item.to_dict() for item in load_dca_contributions(ledger)],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        records = load_dca_contributions(ledger)
        print(
            json.dumps(
                {"status": "valid", "contribution_count": len(records)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
