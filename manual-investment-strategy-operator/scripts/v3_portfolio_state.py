#!/usr/bin/env python3
"""Deterministic, evidence-backed portfolio state for the V3 research pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


SOURCE_PRIORITY: dict[str, int] = {
    "legacy_ledger": 100,
    "current_override": 200,
    "account_export": 300,
    "screenshot": 300,
    "user_cash_transfer_confirmation": 400,
    "user_trade_confirmation": 400,
}


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


def _same_value(left: Any, right: Any) -> bool:
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    return left == right


@dataclass(frozen=True)
class PortfolioFieldObservationV2:
    """One candidate value for one canonical portfolio field."""

    key: str
    value: Any
    as_of: str
    source: str
    source_kind: str
    confidence: float
    evidence_id: str

    def validate(self) -> None:
        if not self.key or "." not in self.key:
            raise ValueError("key:canonical_path_required")
        _parse_iso(self.as_of, "as_of")
        if not self.source:
            raise ValueError("source:required")
        if self.source_kind not in SOURCE_PRIORITY:
            raise ValueError(f"source_kind:unsupported:{self.source_kind}")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("confidence:number_required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence:out_of_range")
        if not self.evidence_id:
            raise ValueError("evidence_id:required")


@dataclass(frozen=True)
class PortfolioConflictV2:
    key: str
    selected_evidence_id: str
    rejected_evidence_id: str
    selected_value: Any
    rejected_value: Any
    reason: str


@dataclass(frozen=True)
class ResolvedPortfolioFieldV2:
    value: Any
    as_of: str
    source: str
    source_kind: str
    confidence: float
    evidence_id: str


@dataclass(frozen=True)
class PortfolioStateV2:
    """A resolved portfolio view with provenance preserved per field."""

    state_id: str
    as_of: str
    fields: Mapping[str, ResolvedPortfolioFieldV2]
    conflicts: tuple[PortfolioConflictV2, ...]

    @classmethod
    def resolve(
        cls,
        *,
        state_id: str,
        as_of: str,
        observations: Iterable[PortfolioFieldObservationV2],
    ) -> "PortfolioStateV2":
        if not state_id:
            raise ValueError("state_id:required")
        cutoff = _parse_iso(as_of, "as_of")
        grouped: dict[str, list[PortfolioFieldObservationV2]] = {}
        seen_evidence_ids: set[str] = set()
        for observation in observations:
            observation.validate()
            observed_at = _parse_iso(observation.as_of, "observation.as_of")
            if observed_at > cutoff:
                raise ValueError(f"{observation.key}:evidence_after_state_cutoff")
            if observation.evidence_id in seen_evidence_ids:
                raise ValueError(f"evidence_id:duplicate:{observation.evidence_id}")
            seen_evidence_ids.add(observation.evidence_id)
            grouped.setdefault(observation.key, []).append(observation)

        resolved: dict[str, ResolvedPortfolioFieldV2] = {}
        conflicts: list[PortfolioConflictV2] = []
        for key, candidates in sorted(grouped.items()):
            winner = max(
                candidates,
                key=lambda item: (
                    SOURCE_PRIORITY[item.source_kind],
                    _parse_iso(item.as_of, "observation.as_of").timestamp(),
                    item.confidence,
                    item.evidence_id,
                ),
            )
            resolved[key] = ResolvedPortfolioFieldV2(
                value=winner.value,
                as_of=winner.as_of,
                source=winner.source,
                source_kind=winner.source_kind,
                confidence=float(winner.confidence),
                evidence_id=winner.evidence_id,
            )
            for candidate in candidates:
                if candidate.evidence_id == winner.evidence_id or _same_value(candidate.value, winner.value):
                    continue
                reason = (
                    "higher_source_precedence"
                    if SOURCE_PRIORITY[winner.source_kind] > SOURCE_PRIORITY[candidate.source_kind]
                    else "newer_evidence_at_same_precedence"
                )
                conflicts.append(
                    PortfolioConflictV2(
                        key=key,
                        selected_evidence_id=winner.evidence_id,
                        rejected_evidence_id=candidate.evidence_id,
                        selected_value=winner.value,
                        rejected_value=candidate.value,
                        reason=reason,
                    )
                )
        return cls(state_id=state_id, as_of=as_of, fields=resolved, conflicts=tuple(conflicts))

    def get(self, key: str, default: Any = None) -> Any:
        field = self.fields.get(key)
        return default if field is None else field.value

    def field(self, key: str) -> ResolvedPortfolioFieldV2:
        try:
            return self.fields[key]
        except KeyError as exc:
            raise KeyError(f"portfolio_field_not_found:{key}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "PortfolioStateV2",
            "state_id": self.state_id,
            "as_of": self.as_of,
            "fields": {key: asdict(value) for key, value in sorted(self.fields.items())},
            "conflicts": [asdict(item) for item in self.conflicts],
        }


def current_zero_cash_baseline(
    *, as_of: str, evidence_id_prefix: str = "current-position-override"
) -> tuple[PortfolioFieldObservationV2, ...]:
    """Return the currently confirmed zero balances as explicit override evidence."""

    values = {
        "holdings.NIGHT.quantity": 0.0,
        "holdings.ENA.quantity": 0.0,
        "cash.crypto.USDT": 0.0,
        "cash.us_equity.USD": 0.0,
    }
    return tuple(
        PortfolioFieldObservationV2(
            key=key,
            value=value,
            as_of=as_of,
            source="current_position_overrides.json",
            source_kind="current_override",
            confidence=1.0,
            evidence_id=f"{evidence_id_prefix}:{key}",
        )
        for key, value in values.items()
    )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path}:json_object_required")
    return value


def observations_from_legacy_ledger(
    ledger: Mapping[str, Any], *, source: str
) -> list[PortfolioFieldObservationV2]:
    """Convert the legacy ledger into low-precedence observations."""

    ledger_as_of = str(ledger.get("as_of") or "1970-01-01T00:00:00+00:00")
    observations: list[PortfolioFieldObservationV2] = []
    for index, holding in enumerate(ledger.get("holdings") or []):
        if not isinstance(holding, dict) or not holding.get("symbol"):
            continue
        symbol = str(holding["symbol"])
        observed_at = str(holding.get("last_verified_at") or ledger_as_of)
        for field_name in (
            "quantity",
            "liquid_quantity",
            "locked_quantity",
            "avg_cost",
            "cost_basis_status",
        ):
            if field_name not in holding:
                continue
            observations.append(
                PortfolioFieldObservationV2(
                    key=f"holdings.{symbol}.{field_name}",
                    value=holding[field_name],
                    as_of=observed_at,
                    source=source,
                    source_kind="legacy_ledger",
                    confidence=0.45,
                    evidence_id=f"legacy:{index}:{symbol}:{field_name}",
                )
            )
        staking = holding.get("staking")
        if isinstance(staking, dict):
            for field_name in (
                "estimated_apy",
                "lock_status",
                "unlock_available_at",
                "provider_risk",
                "count_as_immediate_liquidity",
            ):
                if field_name not in staking:
                    continue
                observations.append(
                    PortfolioFieldObservationV2(
                        key=f"holdings.{symbol}.staking.{field_name}",
                        value=staking[field_name],
                        as_of=observed_at,
                        source=source,
                        source_kind="legacy_ledger",
                        confidence=0.4,
                        evidence_id=f"legacy:{index}:{symbol}:staking:{field_name}",
                    )
                )

    cash_rails = ledger.get("cash_rails") or {}
    crypto_rail = cash_rails.get("crypto_rail") or {}
    equity_rail = cash_rails.get("us_equity_rail") or {}
    legacy_cash = {
        "cash.crypto.USDT": crypto_rail.get("cash_or_stablecoin_value"),
        "cash.us_equity.USD": equity_rail.get("cash_usd"),
    }
    for key, value in legacy_cash.items():
        if value is None:
            continue
        observations.append(
            PortfolioFieldObservationV2(
                key=key,
                value=value,
                as_of=ledger_as_of,
                source=source,
                source_kind="legacy_ledger",
                confidence=0.4,
                evidence_id=f"legacy:cash:{key}",
            )
        )
    return observations


def observations_from_current_overrides(
    overrides: Mapping[str, Any], *, source: str
) -> list[PortfolioFieldObservationV2]:
    """Convert the user/screenshot override layer into authoritative observations."""

    observed_at = str(overrides.get("as_of") or "")
    observations: list[PortfolioFieldObservationV2] = []
    for symbol, holding in sorted((overrides.get("holdings") or {}).items()):
        if not isinstance(holding, dict) or "quantity" not in holding:
            continue
        reason = str(holding.get("reason") or "")
        confidence = 0.75 if "inferred" in reason.casefold() else 1.0
        observations.append(
            PortfolioFieldObservationV2(
                key=f"holdings.{symbol}.quantity",
                value=holding["quantity"],
                as_of=observed_at,
                source=source,
                source_kind="current_override",
                confidence=confidence,
                evidence_id=f"override:{symbol}:quantity",
            )
        )
    cash_rails = overrides.get("cash_rails") or {}
    cash_map = {
        "cash.crypto.USDT": cash_rails.get("crypto_usdt_available"),
        "cash.us_equity.USD": cash_rails.get("us_equity_cash_usd"),
    }
    for key, value in cash_map.items():
        if value is None:
            continue
        observations.append(
            PortfolioFieldObservationV2(
                key=key,
                value=value,
                as_of=observed_at,
                source=source,
                source_kind="current_override",
                confidence=1.0,
                evidence_id=f"override:cash:{key}",
            )
        )
    return observations


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read append-only user confirmations; malformed lines remain non-authoritative."""

    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def observations_from_user_confirmed_states(
    records: Iterable[Mapping[str, Any]], *, source: str
) -> list[PortfolioFieldObservationV2]:
    """Use only the latest confirmed user state per rail at the highest user-confirmed priority.

    A dialogue confirmation is allowed to update the rail the user explicitly named. It must
    never manufacture or overwrite fields in the other rail.
    """

    latest_by_rail: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if str(record.get("status") or "") != "confirmed":
            continue
        rail = str(record.get("rail") or "")
        payload = record.get("payload")
        if rail not in {"crypto", "us_equity"} or not isinstance(payload, Mapping):
            continue
        as_of = str(payload.get("as_of") or "")
        if not as_of:
            continue
        prior = latest_by_rail.get(rail)
        if prior is None or str((prior.get("payload") or {}).get("as_of") or "") < as_of:
            latest_by_rail[rail] = record

    observations: list[PortfolioFieldObservationV2] = []
    for rail, record in sorted(latest_by_rail.items()):
        payload = record["payload"]
        as_of = str(payload["as_of"])
        record_id = str(record.get("id") or f"dialogue-{rail}-{as_of}")
        holdings = payload.get("holdings") or {}
        if isinstance(holdings, Mapping):
            for symbol, quantity in sorted(holdings.items()):
                if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
                    continue
                observations.append(
                    PortfolioFieldObservationV2(
                        key=f"holdings.{str(symbol).upper()}.quantity",
                        value=float(quantity),
                        as_of=as_of,
                        source=source,
                        source_kind="user_trade_confirmation",
                        confidence=1.0,
                        evidence_id=f"dialogue:{record_id}:holdings:{str(symbol).upper()}",
                    )
                )
        if "settled_cash_usd" in payload:
            cash_key = "cash.us_equity.USD" if rail == "us_equity" else "cash.crypto.USDT"
            cash_value = payload.get("settled_cash_usd")
            if isinstance(cash_value, (int, float)) and not isinstance(cash_value, bool):
                observations.append(
                    PortfolioFieldObservationV2(
                        key=cash_key,
                        value=float(cash_value),
                        as_of=as_of,
                        source=source,
                        source_kind="user_trade_confirmation",
                        confidence=1.0,
                        evidence_id=f"dialogue:{record_id}:{cash_key}",
                    )
                )
    return observations


def resolve_portfolio_state_from_files(
    *,
    overrides_path: Path,
    legacy_ledger_path: Path,
    execution_receipts_path: Path | None = None,
    dca_contributions_path: Path | None = None,
    user_confirmed_states_path: Path | None = None,
    as_of: str | None = None,
    state_id: str | None = None,
) -> PortfolioStateV2:
    """Resolve canonical state from legacy data, overrides, and confirmed receipts."""

    overrides = _load_json(overrides_path)
    legacy = _load_json(legacy_ledger_path)
    receipts = []
    latest_receipt_at = None
    if execution_receipts_path is not None and execution_receipts_path.exists():
        from v3_execution_receipts import (
            latest_receipt_timestamp,
            load_execution_receipts,
            observations_from_execution_receipts,
        )

        receipts = load_execution_receipts(execution_receipts_path)
        latest_receipt_at = latest_receipt_timestamp(receipts)
    contributions = []
    latest_contribution_at = None
    if dca_contributions_path is not None and dca_contributions_path.exists():
        from v3_dca_contributions import (
            latest_contribution_timestamp,
            load_dca_contributions,
            observations_from_dca_contributions,
        )

        contributions = load_dca_contributions(dca_contributions_path)
        latest_contribution_at = latest_contribution_timestamp(contributions)
    user_confirmed_states = _load_jsonl(user_confirmed_states_path) if user_confirmed_states_path else []
    latest_user_state_at = max(
        (
            str((record.get("payload") or {}).get("as_of") or "")
            for record in user_confirmed_states
            if str(record.get("status") or "") == "confirmed"
        ),
        default=None,
    )
    source_cutoffs = [
        str(overrides.get("as_of") or ""),
        str(legacy.get("as_of") or ""),
        str(latest_receipt_at or ""),
        str(latest_contribution_at or ""),
        str(latest_user_state_at or ""),
    ]
    populated_cutoffs = [value for value in source_cutoffs if value]
    cutoff = as_of or max(
        populated_cutoffs,
        key=lambda value: _parse_iso(value, "source_as_of"),
    )
    observations = observations_from_legacy_ledger(
        legacy, source=str(legacy_ledger_path)
    )
    observations.extend(
        observations_from_current_overrides(
            overrides, source=str(overrides_path)
        )
    )
    if execution_receipts_path is not None and receipts:
        observations.extend(
            observations_from_execution_receipts(
                receipts, source=str(execution_receipts_path)
            )
        )
    if dca_contributions_path is not None and contributions:
        observations.extend(
            observations_from_dca_contributions(
                contributions, source=str(dca_contributions_path)
            )
        )
    if user_confirmed_states_path is not None and user_confirmed_states:
        observations.extend(
            observations_from_user_confirmed_states(
                user_confirmed_states, source=str(user_confirmed_states_path)
            )
        )
    if state_id is None:
        seed = json.dumps(
            {
                "cutoff": cutoff,
                "overrides": str(overrides_path),
                "legacy": str(legacy_ledger_path),
                "execution_receipts": (
                    str(execution_receipts_path)
                    if execution_receipts_path is not None
                    else None
                ),
                "dca_contributions": (
                    str(dca_contributions_path)
                    if dca_contributions_path is not None
                    else None
                ),
                "user_confirmed_states": (
                    str(user_confirmed_states_path)
                    if user_confirmed_states_path is not None
                    else None
                ),
            },
            sort_keys=True,
        ).encode("utf-8")
        state_id = f"portfolio-{hashlib.sha256(seed).hexdigest()[:16]}"
    return PortfolioStateV2.resolve(
        state_id=state_id, as_of=cutoff, observations=observations
    )


def portfolio_state_from_payload(payload: Mapping[str, Any]) -> PortfolioStateV2:
    """Validate a serialized PortfolioStateV2 without re-resolving evidence."""

    if payload.get("schema_version") != "PortfolioStateV2":
        raise ValueError("schema_version:PortfolioStateV2_required")
    fields = payload.get("fields")
    conflicts = payload.get("conflicts")
    if not isinstance(fields, dict) or not isinstance(conflicts, list):
        raise ValueError("portfolio_state:fields_object_and_conflicts_array_required")
    try:
        state = PortfolioStateV2(
            state_id=str(payload["state_id"]),
            as_of=str(payload["as_of"]),
            fields={
                str(key): ResolvedPortfolioFieldV2(**value)
                for key, value in fields.items()
            },
            conflicts=tuple(PortfolioConflictV2(**item) for item in conflicts),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"portfolio_state:invalid:{exc}") from exc
    _parse_iso(state.as_of, "as_of")
    if not state.state_id:
        raise ValueError("state_id:required")
    for key, field_value in state.fields.items():
        PortfolioFieldObservationV2(
            key=key,
            value=field_value.value,
            as_of=field_value.as_of,
            source=field_value.source,
            source_kind=field_value.source_kind,
            confidence=field_value.confidence,
            evidence_id=field_value.evidence_id,
        ).validate()
    return state


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Build PortfolioStateV2.")
    parser.add_argument(
        "--overrides",
        default=str(
            root
            / "manual-investment-strategy-operator"
            / "config"
            / "current_position_overrides.json"
        ),
    )
    parser.add_argument(
        "--user-confirmed-states",
        default=str(
            root
            / "manual-investment-strategy-operator"
            / "runtime"
            / "user_confirmed_account_states.jsonl"
        ),
    )
    parser.add_argument(
        "--legacy-ledger",
        default=str(
            root
            / "unified-longterm-alpha-investor"
            / "config"
            / "portfolio_ledger.json"
        ),
    )
    parser.add_argument(
        "--execution-receipts",
        default=str(
            root
            / "manual-investment-strategy-operator"
            / "runtime"
            / "execution_receipts.jsonl"
        ),
    )
    parser.add_argument(
        "--dca-contributions",
        default=str(
            root
            / "manual-investment-strategy-operator"
            / "runtime"
            / "dca_contributions.jsonl"
        ),
    )
    parser.add_argument("--as-of")
    parser.add_argument("--state-id")
    parser.add_argument("--output-json")
    args = parser.parse_args()
    state = resolve_portfolio_state_from_files(
        overrides_path=Path(args.overrides).expanduser().resolve(),
        legacy_ledger_path=Path(args.legacy_ledger).expanduser().resolve(),
        execution_receipts_path=Path(args.execution_receipts).expanduser().resolve(),
        dca_contributions_path=Path(args.dca_contributions).expanduser().resolve(),
        user_confirmed_states_path=Path(args.user_confirmed_states).expanduser().resolve(),
        as_of=args.as_of,
        state_id=args.state_id,
    )
    text = json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if args.output_json:
        Path(args.output_json).expanduser().resolve().write_text(
            text + "\n", encoding="utf-8"
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
