#!/usr/bin/env python3
"""Deterministic request-to-horizon routing for V3 investment research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable


class ResearchMode(str, Enum):
    LONGTERM_DCA = "longterm_dca"
    TACTICAL_1_7D = "tactical_1_7d"
    EVENT_TRADE_1_3W = "event_trade_1_3w"
    EXISTING_POSITION_REVIEW = "existing_position_review"
    DAILY_DUAL_WINDOW = "daily_dual_window"


MODE_KEYWORDS: dict[ResearchMode, tuple[str, ...]] = {
    ResearchMode.LONGTERM_DCA: (
        "dca",
        "长期",
        "定投",
        "五年",
        "十年",
        "质押",
        "long term",
    ),
    ResearchMode.TACTICAL_1_7D: (
        "短线",
        "短进短出",
        "一周",
        "7天",
        "7 天",
        "1周",
        "1 周",
        "tactical",
    ),
    ResearchMode.EVENT_TRADE_1_3W: (
        "财报",
        "fomc",
        "事件交易",
        "催化剂",
        "解锁",
        "监管",
        "earnings",
    ),
    ResearchMode.EXISTING_POSITION_REVIEW: (
        "持有吗",
        "继续持有",
        "持仓复盘",
        "现有仓位",
        "该卖吗",
        "review position",
    ),
    ResearchMode.DAILY_DUAL_WINDOW: (
        "晨报",
        "晚报",
        "08:30",
        "23:30",
        "每日双时段",
        "daily dual",
    ),
}


@dataclass(frozen=True)
class RequestSpecV2:
    request_id: str
    mode: ResearchMode
    query: str
    requested_at: str

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.request_id:
            errors.append("request_id:required")
        if not self.query.strip():
            errors.append("query:required")
        try:
            parsed = datetime.fromisoformat(self.requested_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            errors.append("requested_at:invalid_iso_datetime")
        else:
            if parsed.tzinfo is None:
                errors.append("requested_at:timezone_required")
        if not isinstance(self.mode, ResearchMode):
            errors.append("mode:ResearchMode_required")
        return errors

    def validate(self) -> None:
        errors = self.validation_errors()
        if errors:
            raise ValueError(";".join(errors))

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        payload["request_mode"] = self.mode.value
        payload.pop("mode", None)
        payload["schema_version"] = "request-spec-v2"
        return payload


def detected_modes(query: str) -> tuple[ResearchMode, ...]:
    lowered = query.casefold()
    return tuple(
        mode
        for mode in ResearchMode
        if any(keyword.casefold() in lowered for keyword in MODE_KEYWORDS[mode])
    )


def route_request(
    *,
    request_id: str,
    query: str,
    requested_at: str,
    explicit_mode: str | ResearchMode | None = None,
) -> RequestSpecV2:
    """Route exactly one request; ambiguous multi-horizon prompts must be split."""

    if not request_id or not query.strip() or not requested_at:
        raise ValueError("request_id_query_requested_at:required")
    if explicit_mode is not None:
        try:
            mode = explicit_mode if isinstance(explicit_mode, ResearchMode) else ResearchMode(explicit_mode)
        except ValueError as exc:
            raise ValueError(f"explicit_mode:unsupported:{explicit_mode}") from exc
        spec = RequestSpecV2(
            request_id=request_id, mode=mode, query=query, requested_at=requested_at
        )
        spec.validate()
        return spec

    modes = detected_modes(query)
    if not modes:
        raise ValueError("mode:undetermined_explicit_mode_required")
    if len(modes) > 1:
        raise ValueError("mode:ambiguous_split_request_required")
    spec = RequestSpecV2(
        request_id=request_id, mode=modes[0], query=query, requested_at=requested_at
    )
    spec.validate()
    return spec


def split_request(
    *,
    request_id: str,
    query: str,
    requested_at: str,
    modes: Iterable[str | ResearchMode] | None = None,
) -> tuple[RequestSpecV2, ...]:
    """Split a multi-horizon prompt into independent, uniquely identified specs."""

    resolved_modes = (
        tuple(ResearchMode(item) if not isinstance(item, ResearchMode) else item for item in modes)
        if modes is not None
        else detected_modes(query)
    )
    if not resolved_modes:
        raise ValueError("mode:undetermined_explicit_mode_required")
    if len(set(resolved_modes)) != len(resolved_modes):
        raise ValueError("mode:duplicate")
    specs = tuple(
        RequestSpecV2(
            request_id=f"{request_id}:{mode.value}",
            mode=mode,
            query=query,
            requested_at=requested_at,
        )
        for mode in resolved_modes
    )
    for spec in specs:
        spec.validate()
    return specs
