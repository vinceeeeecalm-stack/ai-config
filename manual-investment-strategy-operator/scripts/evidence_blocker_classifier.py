#!/usr/bin/env python3
"""Classify remaining evidence blockers after Research Committee repair.

The evidence backlog says what is missing. This classifier says what kind of
missing item it is: public retry, paid/API entitlement, account evidence,
time-gated learning, or safety-policy cap. It is read-only and never authorizes
trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
EXPERIMENTS = MANUAL_ROOT / "experiments"
REPORTS = MANUAL_ROOT / "reports"


CATEGORIES: dict[str, dict[str, Any]] = {
    "paid_or_entitled_api_needed": {
        "label": "需要付费/API 权限",
        "plain_note": "公开网页很难再补齐，需要 API key、数据订阅或服务权限。",
        "max_action": "watch_or_paper_only",
        "keywords": [
            "api key",
            "api missing",
            "requires market data/api entitlement",
            "entitlement",
            "coinglass",
            "paid x",
            "neynar",
            "market data/API entitlement",
            "401",
        ],
    },
    "user_account_or_broker_evidence_needed": {
        "label": "需要用户账号/券商/钱包证据",
        "plain_note": "这类证据只能来自你的账户截图或导出，不能靠公共网页证明。",
        "max_action": "conditional_or_watch",
        "keywords": [
            "broker-confirmed",
            "settled cash",
            "buying power",
            "account/export",
            "account-level",
            "ledger",
            "coinbase",
            "cost basis",
            "lceth",
            "redemption terms",
            "private account",
            "wallet screenshots",
            "cash rail",
        ],
    },
    "public_source_blocked_or_not_machine_readable": {
        "label": "公开源受阻或不可机器校验",
        "plain_note": "网页可能有信息，但被反爬、登录墙、PDF 解析或页面结构挡住，不能反复当作可靠机器证据。",
        "max_action": "watch",
        "keywords": [
            "blocked",
            "security checkpoint",
            "vercel",
            "timeout",
            "connection",
            "reset by peer",
            "not expose",
            "not machine",
            "machine-readable",
            "machine-captured",
            "pdf text extraction",
            "unavailable",
            "not captured",
        ],
    },
    "time_gated_learning_outcome_needed": {
        "label": "需要等待样本到期/复盘",
        "plain_note": "这不是继续搜资料能解决的，需要 paper 仓或历史建议自然到期后复盘。",
        "max_action": "paper_only",
        "keywords": [
            "paper",
            "closed paper",
            "recommendation outcomes",
            "calibration",
            "sample",
            "samples",
            "hit/failed",
            "resolved recommendation",
            "at least 10",
            "80-plus calibration",
            "due",
        ],
    },
    "safety_or_scope_cap": {
        "label": "安全/职责边界限制",
        "plain_note": "这类不是数据缺口，而是规则边界：社交不能单独触发交易、宏观不能单独授权买入、真实下单必须人工确认。",
        "max_action": "watch_or_conditional_only",
        "keywords": [
            "live_orders_disabled",
            "social_news_cannot_trigger_trade_alone",
            "macro evidence cannot authorize",
            "cannot authorize",
            "human confirmation",
            "single-source social",
            "does not verify broker cash",
            "not authorize",
        ],
    },
    "limited_public_retry_possible": {
        "label": "可有限重试公开数据",
        "plain_note": "下次正式调度可以重试一次公开行情/盘口/新闻源；若仍失败，应降级而不是长时间空跑。",
        "max_action": "watch_or_conditional_after_verification",
        "keywords": [
            "order book",
            "order books",
            "day_gainers",
            "most_actives",
            "yahoo",
            "nasdaq",
            "mexc",
            "bybit",
            "okx",
            "kraken",
            "bitget",
            "nbbo",
            "bid/ask",
            "spread",
            "market-mover",
        ],
    },
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(flatten_text(item))
        return parts
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(str(key))
            parts.extend(flatten_text(item))
        return parts
    return [str(value)]


def split_items(text: str) -> list[str]:
    chunks = re.split(r";|\n|\u3002|。", text)
    return [chunk.strip(" -\t") for chunk in chunks if chunk.strip(" -\t")]


def classify_item(text: str) -> list[str]:
    lowered = text.lower()
    matched: list[str] = []
    for category, spec in CATEGORIES.items():
        for keyword in spec["keywords"]:
            if keyword.lower() in lowered:
                matched.append(category)
                break
    if not matched:
        matched.append("manual_review")
    return matched


def collect_source_items(backlog: dict[str, Any], quality: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task in backlog.get("tasks") or backlog.get("evidence_repair_tasks") or []:
        if not isinstance(task, dict):
            continue
        agent_id = str(task.get("agent_id") or task.get("role") or "unknown_agent")
        priority = str(task.get("priority") or "P1")
        for field in ["needed", "missing_data", "failed_gates"]:
            for raw in flatten_text(task.get(field)):
                for chunk in split_items(raw):
                    items.append(
                        {
                            "agent_id": agent_id,
                            "priority": priority,
                            "field": field,
                            "text": chunk,
                        }
                    )
    for blocker in backlog.get("action_blockers") or []:
        if not isinstance(blocker, dict):
            continue
        agent_id = str(blocker.get("agent_id") or blocker.get("role") or "unknown_agent")
        priority = str(blocker.get("priority") or "P0")
        for raw in flatten_text(blocker):
            for chunk in split_items(raw):
                if len(chunk) < 8:
                    continue
                items.append(
                    {
                        "agent_id": agent_id,
                        "priority": priority,
                        "field": "action_blocker",
                        "text": chunk,
                    }
                )
    if quality:
        for row in quality.get("role_rows") or []:
            if not isinstance(row, dict):
                continue
            agent_id = str(row.get("agent_id") or row.get("role") or "unknown_agent")
            for field in ["notes", "material_missing_data", "action_blockers"]:
                for raw in flatten_text(row.get(field)):
                    for chunk in split_items(raw):
                        if len(chunk) < 8:
                            continue
                        items.append(
                            {
                                "agent_id": agent_id,
                                "priority": "P0",
                                "field": field,
                                "text": chunk,
                            }
                        )
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (item["agent_id"], item["text"].lower())
        deduped.setdefault(key, item)
    return list(deduped.values())


def build_classification(backlog: dict[str, Any], quality: dict[str, Any] | None, run_id: str) -> dict[str, Any]:
    source_items = collect_source_items(backlog, quality)
    categories: dict[str, list[dict[str, Any]]] = {name: [] for name in CATEGORIES}
    categories["manual_review"] = []
    for item in source_items:
        matches = classify_item(item["text"])
        for match in matches:
            categories.setdefault(match, []).append(item)

    category_summary = []
    for name, items in categories.items():
        spec = CATEGORIES.get(
            name,
            {
                "label": "需要人工判断分类",
                "plain_note": "自动分类没有足够把握，需要下一轮报告人工仲裁。",
                "max_action": "watch",
            },
        )
        category_summary.append(
            {
                "category": name,
                "label": spec["label"],
                "count": len(items),
                "plain_note": spec["plain_note"],
                "max_action": spec["max_action"],
                "agents": sorted({str(item.get("agent_id")) for item in items}),
                "examples": [item["text"] for item in items[:5]],
            }
        )

    retryable_count = len(categories.get("limited_public_retry_possible", []))
    hard_external_count = (
        len(categories.get("paid_or_entitled_api_needed", []))
        + len(categories.get("user_account_or_broker_evidence_needed", []))
        + len(categories.get("time_gated_learning_outcome_needed", []))
    )
    safety_count = len(categories.get("safety_or_scope_cap", []))
    public_blocked_count = len(categories.get("public_source_blocked_or_not_machine_readable", []))
    should_stop_open_loop = hard_external_count + safety_count + public_blocked_count > retryable_count
    max_current_action = (
        (backlog.get("summary") or {}).get("current_max_action")
        or (quality.get("summary") or {}).get("max_allowed_action")
        if quality
        else "watch"
    )
    return {
        "run_id": run_id,
        "generated_at": utc_now(),
        "status": "ok",
        "purpose": "classify remaining evidence blockers so future manual dispatches avoid empty public-data loops",
        "summary": {
            "source_item_count": len(source_items),
            "retryable_public_count": retryable_count,
            "public_blocked_or_not_machine_readable_count": public_blocked_count,
            "paid_or_entitled_api_needed_count": len(categories.get("paid_or_entitled_api_needed", [])),
            "user_account_or_broker_evidence_needed_count": len(
                categories.get("user_account_or_broker_evidence_needed", [])
            ),
            "time_gated_learning_outcome_needed_count": len(
                categories.get("time_gated_learning_outcome_needed", [])
            ),
            "safety_or_scope_cap_count": safety_count,
            "manual_review_count": len(categories.get("manual_review", [])),
            "should_stop_open_loop_research": should_stop_open_loop,
            "recommended_next_mode": (
                "stop_and_wait_for_due_sample_or_user/API_evidence"
                if should_stop_open_loop
                else "one_more_timeboxed_public_retry"
            ),
            "max_current_action": max_current_action or "watch",
            "execute_now_allowed": False,
        },
        "category_summary": category_summary,
        "categories": categories,
        "next_steps": [
            {
                "priority": "P0",
                "step": "Wait until the nearest paper sample is due, then run due_learning_review_pulse once.",
                "why": "Time-gated learning cannot be accelerated by repeated web searches.",
            },
            {
                "priority": "P0",
                "step": "Ask for account/broker exports only when position sizing or cash rails are needed.",
                "why": "lcETH terms, actual Ledger staking APY, and US broker cash cannot be proven from public pages.",
            },
            {
                "priority": "P1",
                "step": "On next fresh manual dispatch, retry only the limited public-data items inside the timebox.",
                "why": "Order books, market movers, and public source pages may recover, but should not block the report indefinitely.",
            },
            {
                "priority": "P1",
                "step": "Use paid/API sources only if the user provides keys or approves the data subscription route.",
                "why": "CME direct grid, CoinGlass OI/funding, X/Neynar social feeds are entitlement/key-bound.",
            },
        ],
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    category_rows = []
    for item in payload["category_summary"]:
        category_rows.append(
            [
                item["label"],
                item["count"],
                item["max_action"],
                ", ".join(item["agents"]) or "-",
                item["plain_note"],
            ]
        )
    next_rows = [[item["priority"], item["step"], item["why"]] for item in payload["next_steps"]]
    lines = [
        "# Evidence Blocker Classification",
        "",
        "这份报告把剩余证据缺口分成不同类型，避免后续手动调度反复在同一个外部限制上空跑。它只读，不抓行情、不下单、不改持仓。",
        "",
        "## 一页结论",
        "",
        markdown_table(
            ["项目", "值"],
            [
                ["来源缺口数量", summary["source_item_count"]],
                ["可限时重试公开数据", summary["retryable_public_count"]],
                ["公开源受阻/不可机器校验", summary["public_blocked_or_not_machine_readable_count"]],
                ["需要付费/API 权限", summary["paid_or_entitled_api_needed_count"]],
                ["需要用户账号/券商/钱包证据", summary["user_account_or_broker_evidence_needed_count"]],
                ["需要等待样本到期/复盘", summary["time_gated_learning_outcome_needed_count"]],
                ["安全/职责边界限制", summary["safety_or_scope_cap_count"]],
                ["建议停止开放式长循环", summary["should_stop_open_loop_research"]],
                ["下一步模式", summary["recommended_next_mode"]],
                ["当前最大动作", summary["max_current_action"]],
                ["允许 execute_now", summary["execute_now_allowed"]],
            ],
        ),
        "",
        "## 分类结果",
        "",
        markdown_table(["分类", "数量", "动作上限", "相关角色", "直白说明"], category_rows),
        "",
        "## 下一步",
        "",
        markdown_table(["优先级", "动作", "原因"], next_rows),
        "",
        "## 代表性缺口",
        "",
    ]
    for item in payload["category_summary"]:
        if not item["examples"]:
            continue
        lines.append(f"### {item['label']}")
        for example in item["examples"]:
            lines.append(f"- {example}")
        lines.append("")
    lines.extend(
        [
            "## 术语小注",
            "",
            "- `公开源受阻`：网页可能能人工看到，但脚本无法稳定抓取，所以不能作为机器校验依据。",
            "- `time-gated`：必须等时间过去，例如模拟仓到期或建议到期后，才能复盘命中/失败。",
            "- `execute_now`：可人工确认后立即真实执行；当前仍为 False。",
            "- `动作上限`：在缺口补齐前，报告最多能给到什么级别的建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backlog-json", required=True)
    parser.add_argument("--quality-json")
    parser.add_argument("--run-id", default="evidence-blocker-classification")
    parser.add_argument("--output-json", default=str(EXPERIMENTS / "evidence_blocker_classification.json"))
    parser.add_argument("--output-md", default=str(REPORTS / "evidence_blocker_classification.md"))
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    backlog = load_json(args.backlog_json)
    quality = load_json(args.quality_json) if args.quality_json else None
    payload = build_classification(backlog, quality, args.run_id)
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    write_json(output_json, payload)
    write_text(output_md, render_markdown(payload))
    result = {
        "status": payload["status"],
        "run_id": payload["run_id"],
        "summary": payload["summary"],
        "output_json": str(output_json),
        "output_md": str(output_md),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status={result['status']}")
        print(f"output_md={result['output_md']}")
        print(f"recommended_next_mode={payload['summary']['recommended_next_mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
