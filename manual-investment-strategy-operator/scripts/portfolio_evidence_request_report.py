#!/usr/bin/env python3
"""Generate a user-facing portfolio evidence request report.

This script converts the portfolio evidence gap package into a practical list
of screenshots/exports/CSV fields the user can provide. It is read-only: it
does not mutate ledgers, place orders, move cash, fetch private account data,
or authorize trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
EXPERIMENTS = MANUAL_ROOT / "experiments"
REPORTS = MANUAL_ROOT / "reports"
TEMPLATES = MANUAL_ROOT / "import_templates"


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


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def artifact_key(path: Path) -> tuple[dt.datetime, float, str]:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        payload = {}
    generated_at = parse_time(payload.get("generated_at") if isinstance(payload, dict) else None)
    return (
        generated_at or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc),
        path.stat().st_mtime,
        path.name,
    )


def newest(pattern: str) -> Path | None:
    matches = [Path(item) for item in glob.glob(str(EXPERIMENTS / pattern))]
    if not matches:
        return None
    return max(matches, key=artifact_key)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return "<br>".join(str(item) for item in value)
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def gap_by_id(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("gap_id")): item
        for item in package.get("evidence_gaps") or []
        if isinstance(item, dict) and item.get("gap_id")
    }


def template_columns(template_name: str) -> list[str]:
    path = TEMPLATES / template_name
    if not path.exists():
        return []
    first = path.read_text(encoding="utf-8").splitlines()[0] if path.read_text(encoding="utf-8").splitlines() else ""
    return [item.strip() for item in first.split(",") if item.strip()]


def request_item(
    request_id: str,
    priority: str,
    title: str,
    why: str,
    accepted_evidence: list[str],
    fields_to_capture: list[str],
    template_file: str,
    blocks_until_done: str,
    plain_example: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "priority": priority,
        "title": title,
        "why_it_matters": why,
        "accepted_evidence": accepted_evidence,
        "fields_to_capture": fields_to_capture,
        "template_file": template_file,
        "template_columns": template_columns(template_file),
        "blocks_until_done": blocks_until_done,
        "plain_example": plain_example,
    }


def build_request(package: dict[str, Any], source_path: Path) -> dict[str, Any]:
    gaps = gap_by_id(package)
    cost_lceth = gaps.get("cost_basis_lcETH") or {}
    lceth_terms = gaps.get("lceth_staking_redemption_terms") or {}
    cash_rail = gaps.get("us_equity_cash_rail_verification") or {}
    ada = gaps.get("cost_basis_ADA") or {}
    sol = gaps.get("cost_basis_SOL") or {}
    crypto_cash = gaps.get("crypto_cash_floor_below_policy") or {}

    requests = [
        request_item(
            "lceth_coinbase_cost_and_redeem",
            "P0",
            "Coinbase lcETH 成本、质押和赎回信息",
            "lcETH 是当前最大 crypto 仓位之一；没有成本、兑换比例和赎回延迟，就无法准确判断 ETH/lcETH 是拖慢项、核心仓还是可调整仓。",
            list(dict.fromkeys((cost_lceth.get("accepted_evidence") or []) + (lceth_terms.get("accepted_evidence") or []))),
            [
                "11.2138 lcETH 的获得时间、成本或 ETH 转换记录",
                "lcETH 与底层 ETH 的兑换比例",
                "可赎回的底层 ETH 数量",
                "预计赎回/解锁天数",
                "即时赎回费率或平台费用",
                "质押奖励数量和时间",
            ],
            "lceth_staking_lot_import_template.csv",
            "完整 lcETH PnL、流动性判断、ETH/lcETH 新增 DCA 都保持降级",
            "Coinbase 页面或导出里看到 lcETH receipt、redeem preview、staking rewards 时，按 acquisition / reward / redeem_preview 分行填写。",
        ),
        request_item(
            "us_equity_cash_rail",
            "P0",
            "美股券商现金和 buying power",
            "美股和 crypto 是两条独立资金通道；没有券商现金和 buying power 的时间戳，就不能精确建议战术仓换入金额。",
            cash_rail.get("accepted_evidence") or [],
            [
                "settled cash 美元金额",
                "buying power 美元金额",
                "unsettled cash",
                "pending orders",
                "是否开通 margin / shorting / options",
                "截图或导出时间戳",
            ],
            "us_equity_cash_rail_template.csv",
            "美股战术 sizing 只能 conditional/watch，不能强 execute_now",
            "券商账户余额页截图即可，但要看得到时间、现金、购买力和权限状态。",
        ),
        request_item(
            "ada_sol_missing_lots",
            "P1",
            "ADA / SOL 历史成交与质押奖励 lot",
            "ADA 和 SOL 是长期 DCA 重点候选；当前只知道部分 Binance 成交，缺历史余额和奖励 lot 会影响收益率、DCA 权重和换仓判断。",
            list(dict.fromkeys((ada.get("accepted_evidence") or []) + (sol.get("accepted_evidence") or []))),
            [
                "ADA 缺失数量估计：" + str(ada.get("missing_quantity_estimate") or ""),
                "SOL 缺失数量估计：" + str(sol.get("missing_quantity_estimate") or ""),
                "每笔买入/转入/奖励时间",
                "成交价格或来源市价",
                "手续费和手续费币种",
                "Binance / Ledger / wallet 来源文件名",
            ],
            "crypto_lot_import_template.csv",
            "ADA/SOL 完整成本收益和税 lot 轮动保持 partial_cost_aware_only",
            "Binance trade export、Ledger staking reward、钱包转账记录都可以。没有成交价的转入先标 data_quality_status=degraded。",
        ),
        request_item(
            "crypto_cash_and_open_orders",
            "P1",
            "Crypto 现金、稳定币和未成交限价单",
            "DCA 资金是否已经进入 crypto 通道，会直接影响本月还能不能追加买入，以及应该现价买还是等限价。",
            crypto_cash.get("accepted_evidence") or [],
            [
                "USDT / USDC 可用余额",
                "锁在限价单里的金额",
                "最近已成交订单",
                "未成交订单价格和数量",
                "截图或导出时间戳",
            ],
            "crypto_lot_import_template.csv",
            "Crypto DCA 金额只能按 conditional 处理，不能假设有现金",
            "交易所余额页 + open orders + recent fills 三张截图通常就够。",
        ),
    ]

    return {
        "generated_at": utc_now(),
        "status": "ok",
        "request_version": "portfolio-evidence-request-v1",
        "read_only": True,
        "live_orders_enabled": False,
        "mutates_ledgers": False,
        "does_not_authorize_trades": True,
        "source_package": str(source_path),
        "summary": {
            "portfolio_evidence_gap_count": (package.get("summary") or {}).get("evidence_gap_count"),
            "blocking_gap_count": (package.get("summary") or {}).get("blocking_gap_count"),
            "p0_gap_ids": (package.get("summary") or {}).get("p0_gap_ids") or [],
            "p1_gap_ids": (package.get("summary") or {}).get("p1_gap_ids") or [],
            "full_cost_aware_pnl_allowed": (package.get("readiness") or {}).get("full_cost_aware_pnl_allowed"),
            "us_equity_cash_verified": (package.get("readiness") or {}).get("us_equity_cash_verified"),
            "lceth_account_specific_redemption_verified": (package.get("readiness") or {}).get("lceth_account_specific_redemption_verified"),
            "max_allowed_effect": (package.get("readiness") or {}).get("max_allowed_effect"),
        },
        "user_requests": requests,
        "do_not_provide": [
            "钱包助记词、私钥、seed phrase",
            "交易所 API secret、提现权限、2FA code",
            "完整身份证件或不必要的个人敏感信息",
            "任何可以直接转走资产的权限信息",
        ],
        "after_user_provides_evidence": [
            "先运行 goal_evidence_import_validator.py 做只读验证。",
            "再运行 goal_evidence_import_preview.py 生成 proposed ledger updates。",
            "人工确认后才允许更新账本；更新后重新跑 cost_basis 和 manual report。",
        ],
        "plain_term_notes": {
            "lot": "一笔资产来源记录，例如买入、奖励、转入或兑换。",
            "settled cash": "券商已经结算、可以真实使用的现金。",
            "buying power": "券商显示的可买入额度，可能包含未结算或融资因素，所以要看权限状态。",
            "redeem preview": "赎回预览，用来确认 lcETH 能换回多少 ETH、多久到账、费用多少。",
            "data_quality_status": "数据质量状态；verified 是可靠，degraded 是可用但不完整。",
        },
    }


def render_markdown(request: dict[str, Any]) -> str:
    summary = request.get("summary") or {}
    rows = [
        ["证据缺口数", summary.get("portfolio_evidence_gap_count")],
        ["阻断缺口数", summary.get("blocking_gap_count")],
        ["P0 缺口", summary.get("p0_gap_ids")],
        ["P1 缺口", summary.get("p1_gap_ids")],
        ["完整成本收益可用", summary.get("full_cost_aware_pnl_allowed")],
        ["美股现金已验证", summary.get("us_equity_cash_verified")],
        ["lcETH 赎回已验证", summary.get("lceth_account_specific_redemption_verified")],
        ["完成前最大影响", summary.get("max_allowed_effect")],
    ]
    request_rows = [
        [
            item.get("priority"),
            item.get("title"),
            item.get("fields_to_capture"),
            item.get("template_file"),
            item.get("blocks_until_done"),
        ]
        for item in request.get("user_requests") or []
    ]
    template_rows = [
        [
            item.get("template_file"),
            item.get("template_columns"),
            item.get("plain_example"),
        ]
        for item in request.get("user_requests") or []
    ]
    return "\n\n".join([
        "# Portfolio Evidence Request Report",
        "这是一份只读资料请求清单，不抓账户、不下单、不转账、不改账本。目标是把后续 DCA、美股战术 sizing 和完整收益口径做准。",
        "## 一页摘要",
        markdown_table(["项目", "值"], rows),
        "## 需要你补的资料",
        markdown_table(["优先级", "资料", "需要看见的字段", "填写模板", "不补齐会阻断什么"], request_rows),
        "## 模板和填写示例",
        markdown_table(["模板", "字段", "怎么填"], template_rows),
        "## 不要提供这些敏感信息",
        "\n".join(f"- {item}" for item in request.get("do_not_provide") or []),
        "## 你提供资料之后系统怎么处理",
        "\n".join(f"- {item}" for item in request.get("after_user_provides_evidence") or []),
        "## 术语小注",
        "\n".join(f"- `{key}`：{value}" for key, value in (request.get("plain_term_notes") or {}).items()),
    ]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a user-facing evidence request report")
    parser.add_argument("--portfolio-gap-json")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    source = Path(args.portfolio_gap_json) if args.portfolio_gap_json else newest("portfolio_evidence_gap_package_*.json")
    if not source or not source.exists():
        payload = {
            "generated_at": utc_now(),
            "status": "failed",
            "read_only": True,
            "live_orders_enabled": False,
            "mutates_ledgers": False,
            "errors": ["portfolio_gap_package_missing"],
        }
    else:
        payload = build_request(load_json(source), source)

    if args.output_json:
        write_json(Path(args.output_json), payload)
        if payload.get("status") == "ok":
            payload.setdefault("written_outputs", {})["json"] = str(args.output_json)
    if args.output_md:
        text = render_markdown(payload) if payload.get("status") == "ok" else json.dumps(payload, ensure_ascii=False, indent=2)
        write_text(Path(args.output_md), text)
        if payload.get("status") == "ok":
            payload.setdefault("written_outputs", {})["markdown"] = str(args.output_md)
            if args.output_json:
                write_json(Path(args.output_json), payload)

    if args.format == "markdown" and payload.get("status") == "ok":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
