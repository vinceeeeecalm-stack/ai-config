#!/usr/bin/env python3
"""Build a read-only backlog of strategy improvements to validate next.

This script does not fetch market data, place orders, or promote strategies.
It turns the 10x objective into a short list of strategy ideas that should be
tested through the existing research, paper, recommendation, and audit gates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_CONFIG = MANUAL_ROOT / "config" / "manual_strategy_config.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def strategy(
    strategy_id: str,
    family: str,
    objective: str,
    description: str,
    candidate_use: str,
    required_inputs: list[str],
    validation_plan: list[str],
    current_allowed_action: str,
    promotion_blockers: list[str],
    plain_note: str,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "family": family,
        "objective": objective,
        "description": description,
        "candidate_use": candidate_use,
        "required_inputs": required_inputs,
        "validation_plan": validation_plan,
        "current_allowed_action": current_allowed_action,
        "promotion_blockers": promotion_blockers,
        "plain_note": plain_note,
    }


def build_backlog(config: dict[str, Any]) -> dict[str, Any]:
    thresholds = (
        config.get("strategy_library_promotion_gate", {}).get("evidence_thresholds")
        or {}
    )
    min_paper = thresholds.get("min_closed_paper_trades", 20)
    min_reviews = thresholds.get("min_recommendation_outcome_reviews", 10)
    items = [
        strategy(
            "crypto_drawdown_weighted_dca",
            "goal_weighted_dca",
            "5y_10y_crypto_dca",
            "按资产相对历史回撤、流动性和长期 thesis 调整本月 DCA 权重。",
            "SOL/ADA/NIGHT 等长期候选的 DCA 节奏优化。",
            [
                "24h/7d/30d/1y price percentile",
                "Binance/CoinGecko/CoinMarketCap price verification",
                "macro risk regime",
                "current portfolio weights",
            ],
            [
                "写入 recommendation history",
                "按月复盘未触发/命中/失效",
                "连续 3 个 DCA 窗口后评估是否提高目标贡献",
            ],
            "conditional_action",
            ["needs_fresh_multisource_data_each_dispatch"],
            "回撤加权 DCA 是“跌到更有赔率时多买一点”，不是越跌越无脑加仓。",
        ),
        strategy(
            "staking_adjusted_relative_value",
            "staking_compound_satellite",
            "5y_10y_staking_compounding",
            "把 SOL/ADA/ETH-lcETH 的质押复利折算到达到 10x 仍需的价格倍数。",
            "决定 ADA/SOL 是否比 BTC/ETH 更值得占用新增资金。",
            [
                "verified staking APY",
                "unlock/redeem terms",
                "inflation/supply schedule",
                "asset goal contribution panel",
            ],
            [
                "每月更新 APY 与解锁条件",
                "比较 5y/10y 复利后仍需价格倍数",
                "缺 APY 或解锁证据时自动降级",
            ],
            "conditional_action",
            ["lcETH_terms_partial", "ADA_SOL_lot_history_partial"],
            "质押收益只会降低所需涨幅，不能替代资产本身上涨。",
        ),
        strategy(
            "high_convexity_tail_screen",
            "convex_tail_sleeve",
            "5y_10y_tail_upside",
            "寻找长期尾部高凸性资产，但必须先过流动性、解锁、官方路线图和生态数据。",
            "NIGHT 或其他新叙事资产的小额长期观察/尾仓。",
            [
                "spot pair discovery",
                "order book depth",
                "float/unlock schedule",
                "official roadmap",
                "developer/ecosystem activity",
            ],
            [
                "先 research_only",
                "再 paper/watch 3 个窗口",
                "只有数据 verified 才能进入小额 DCA 草案",
            ],
            "watch_or_no_deploy",
            ["needs_float_unlock_depth_evidence"],
            "高凸性是“小概率大上行”，不是把小币直接当核心仓。",
        ),
        strategy(
            "us_short_mid_trend_relay",
            "trend_rotation_relay",
            "us_tactical_50pct_month_quarter",
            "先识别 2-4 周主趋势，再给最多两档入场/出场，动态接力当前战术仓。",
            "SOXL/APLD/COIN 或新的美股高波动候选，不 hardcode 标的。",
            [
                "current deployable tactical position",
                "market movers",
                "relative strength",
                "news catalyst",
                "volume/volatility",
                "broker cash/buying power",
            ],
            [
                f"至少 {min_paper} 个 closed paper trades",
                "walk-forward target pass",
                "双80候选才进入 execute_now_candidate",
            ],
            "paper_only",
            ["paper_closed_sample_too_small", "broker_cash_degraded"],
            "战术接力是“当前仓和新候选谁更值得承载未来几周行情”，不是为了交易而交易。",
        ),
        strategy(
            "event_volume_confirmation_alpha",
            "event_news_alpha",
            "us_and_crypto_tactical_watch",
            "新闻/财报/监管/社交消息必须被价格、成交量和多源事实确认后才进入候选。",
            "发现短期爆发候选，但只能 watch/paper，不能单靠消息实盘。",
            [
                "confirmed news source",
                "price reaction window",
                "volume expansion",
                "spread/liquidity",
                "social/key person confirmation for crypto",
            ],
            [
                f"至少 {min_reviews} 个 resolved recommendation outcomes",
                "按事件类型分组复盘 hit/failed",
                "未验证传闻只能 watch",
            ],
            "watch_or_paper_only",
            ["recommendation_calibration_sample_too_small"],
            "消息只负责发现机会；是否行动还要看量价、流动性和历史同类样本。",
        ),
        strategy(
            "risk_regime_cash_timing",
            "dynamic_reserve_timing",
            "survival_and_reentry",
            "用宏观、恐慌/贪婪、波动率和资产回撤决定 DCA 是否近价买或等更优回调。",
            "减少长期闲置现金，同时保留明确入场时间窗口。",
            [
                "macro regime",
                "Fear & Greed",
                "DXY/rates/risk appetite",
                "asset drawdown bands",
                "cash rail availability",
            ],
            [
                "每次 DCA 最多两档",
                "等待现金必须有最长等待期",
                "错过机会要归因",
            ],
            "conditional_action",
            ["needs_fresh_macro_and_cash_rail"],
            "现金等待只用于更好入场，不应变成长期空仓。",
        ),
    ]
    return {
        "generated_at": utc_now(),
        "status": "ok",
        "read_only": True,
        "live_orders_enabled": False,
        "mutates_ledgers": False,
        "strategy_iteration_backlog_version": "strategy-iteration-backlog-v1",
        "summary": {
            "candidate_strategy_count": len(items),
            "max_allowed_action": "conditional_action",
            "execute_now_allowed": False,
            "reason": "strategy ideas must pass research, paper, recommendation calibration, data quality, and manual confirmation before real execution",
        },
        "candidate_strategies": items,
        "plain_notes": [
            "`strategy_iteration_backlog` 是策略改进候选清单，不是买卖清单。",
            "`conditional_action` 是条件动作草案，仍需要触发价、数据质量和人工确认。",
            "`paper_only` 是模拟验证，不动真钱。",
        ],
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return "<br>".join(str(item) for item in value)
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(cell(part) for part in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    rows = [
        [
            item.get("strategy_id"),
            item.get("family"),
            item.get("objective"),
            item.get("current_allowed_action"),
            item.get("plain_note"),
        ]
        for item in payload.get("candidate_strategies") or []
    ]
    return "\n".join([
        f"# Strategy Iteration Backlog | {payload.get('generated_at')}",
        "",
        "这是只读策略改进候选清单。它不抓行情、不下单、不修改账本。",
        "",
        markdown_table(["strategy_id", "family", "objective", "max_action", "plain_note"], rows),
        "",
        "## 术语小注",
        "",
        "- `backlog`：下一步要验证的候选清单，不是买卖清单。",
        "- `paper_only`：只做模拟验证，不动真钱。",
        "- `conditional_action`：条件满足后可人工确认的草案，不是自动执行。",
    ]) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-json", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_backlog(load_json(args.config_json))
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(payload), encoding="utf-8")

    if args.format == "markdown":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
