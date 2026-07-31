#!/usr/bin/env python3
"""Generate the next manual-dispatch execution plan.

This script turns the latest system audit, readiness preflight, and next-step
queue into a user-facing execution plan for the next manual dispatch. It is
read-only: it does not fetch market data, mutate ledgers, place orders, move
cash, or authorize trades.
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
SAFE_PAPER_VALIDATION_PULSE_COMMAND = (
    "python3 manual-investment-strategy-operator/scripts/due_learning_review_pulse.py "
    "--timeout-seconds 180 --format json"
)
SHANGHAI = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def suggested_live_run_id(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    local = current.astimezone(SHANGHAI)
    return local.strftime("%Y%m%d-live-manual-dispatch-%H%M")


def default_manual_dispatch_command(now: dt.datetime | None = None) -> str:
    return (
        "python3 manual-investment-strategy-operator/scripts/manual_dispatch_run.py "
        f"--run-id {suggested_live_run_id(now)} --monthly-dca 1000"
    )


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


def display_time(value: Any) -> str:
    parsed = parse_time(value)
    if not parsed:
        return str(value or "")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    local = parsed.astimezone(SHANGHAI)
    return local.replace(microsecond=0).isoformat()


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


def load_optional(path: str | Path | None, label: str) -> tuple[dict[str, Any], str | None]:
    if not path:
        return {}, f"{label}:missing_path"
    candidate = Path(path)
    if not candidate.exists():
        return {}, f"{label}:not_found:{candidate}"
    try:
        payload = load_json(candidate)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{label}:unreadable:{exc}"
    if not isinstance(payload, dict):
        return {}, f"{label}:not_object"
    return payload, None


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def p0_tasks(queue: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in queue.get("tasks") or []
        if isinstance(item, dict) and item.get("priority") == "P0"
    ]


def blocking_tasks(queue: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in queue.get("tasks") or []
        if isinstance(item, dict) and item.get("blocks_execute_now") is True
    ]


def first_command_with(queue: dict[str, Any], needle: str, fallback: str) -> str:
    for item in queue.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        for command in item.get("commands") or []:
            if needle in str(command):
                return str(command)
    return fallback


def load_context_from_completion_audit(completion: dict[str, Any]) -> dict[str, Any]:
    inputs = completion.get("inputs") or {}
    context_path = inputs.get("context_json")
    if not context_path:
        return {}
    try:
        payload = load_json(Path(str(context_path)))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_current_tactical_symbol(completion: dict[str, Any], readiness: dict[str, Any]) -> str | None:
    summary = readiness.get("summary") or {}
    symbol = str(summary.get("current_tactical_symbol") or "").strip()
    if symbol and symbol.lower() != "none":
        return symbol

    context = load_context_from_completion_audit(completion)
    tactical_panel = context.get("us_tactical_performance_panel") or {}
    tactical_summary = tactical_panel.get("summary") or tactical_panel
    for component in tactical_summary.get("current_components") or []:
        if not isinstance(component, dict):
            continue
        symbol = str(component.get("symbol") or "").strip()
        if (
            symbol
            and component.get("inclusion_reason") == "current_tactical_position"
            and not symbol.startswith("USD")
        ):
            return symbol
    return None


def crypto_dca_timing_rows(completion: dict[str, Any]) -> list[list[Any]]:
    context = load_context_from_completion_audit(completion)
    panel = context.get("asset_goal_contribution_panel") or {}
    summary = panel.get("summary") or panel
    guidance = summary.get("dca_guidance") or {}
    rows: list[list[Any]] = []
    strategy_fallback = {
        "accelerated_dca": "front_load_plus_near_price_first_tranche",
        "near_price_entry": "near_price_first_tranche_then_better_pullback_second_tranche",
        "limit_order_wait": "limit_order_wait_with_explicit_deadline_and_recheck",
        "hold_stablecoin_until_trigger": "hold_stablecoin_until_data_or_price_trigger",
    }
    for item in guidance.get("long_horizon_timing_decisions") or []:
        if not isinstance(item, dict):
            continue
        decision = item.get("decision")
        rows.append(
            [
                item.get("symbol"),
                decision,
                item.get("dynamic_buy_strategy") or strategy_fallback.get(str(decision), "next_report_must_generate"),
                item.get("long_term_low_value_zone_status") or item.get("long_term_value_zone_status"),
                item.get("front_load_amount_usd"),
                item.get("near_term_total_budget_usd"),
                item.get("required_pullback_to_wait_pct"),
                item.get("cash_idle_drag_comment"),
                item.get("reason"),
            ]
        )
    if rows:
        return rows
    return [
        [
            "本次正式调度生成",
            "accelerated_dca / near_price_entry / limit_order_wait / hold_stablecoin_until_trigger",
            "近价第一档 / 回调限价 / 前置 DCA 动态选择",
            "长期低位、thesis、质押、流动性和数据质量共同决定",
            "按报告输出",
            "按报告输出",
            "必须大于等待成本",
            "现金不是长期默认仓位",
            "下一次正式报告必须比较早买入质押和等待回调的取舍。",
        ]
    ]


def with_tactical_symbol(command: str, symbol: str | None) -> str:
    if "--current-tactical-symbol" in command:
        if "<当前动态战术仓>" in command and symbol:
            return command.replace("<当前动态战术仓>", symbol)
        return command
    if symbol:
        return f"{command} --current-tactical-symbol {symbol}"
    return command


def build_plan(
    completion: dict[str, Any],
    readiness: dict[str, Any],
    queue: dict[str, Any],
    evidence_blockers: dict[str, Any],
    paths: dict[str, str],
) -> dict[str, Any]:
    completion_summary = completion.get("summary") or {}
    readiness_summary = readiness.get("summary") or {}
    queue_summary = queue.get("summary") or {}
    evidence_summary = evidence_blockers.get("summary") or {}
    p0 = p0_tasks(queue)
    blockers = blocking_tasks(queue)
    paper_command = first_command_with(queue, "due_learning_review_pulse.py", SAFE_PAPER_VALIDATION_PULSE_COMMAND)
    current_tactical_symbol = resolve_current_tactical_symbol(completion, readiness)
    dispatch_command = with_tactical_symbol(
        first_command_with(queue, "manual_dispatch_run.py", default_manual_dispatch_command()),
        current_tactical_symbol,
    )
    evidence_stop_condition = None
    if evidence_summary.get("should_stop_open_loop_research") is True:
        evidence_stop_condition = (
            "证据阻断分类显示，大多数剩余缺口需要 API/账户证据或等待样本到期；"
            "不要继续开放式公开搜索长循环。"
        )
    return {
        "generated_at": utc_now(),
        "status": "ok",
        "plan_version": "next-manual-dispatch-execution-plan-v1",
        "read_only": True,
        "live_orders_enabled": False,
        "mutates_ledgers": False,
        "does_not_authorize_trades": True,
        "paths": paths,
        "summary": {
            "goal_mechanism_ready": bool(completion_summary.get("goal_mechanism_ready")),
            "active_goal_execution_matrix_ready": bool(completion_summary.get("active_goal_execution_matrix_ready")),
            "goal_complete": bool(completion_summary.get("goal_complete")),
            "ready_for_next_manual_dispatch": bool(readiness_summary.get("ready_for_next_manual_dispatch")),
            "ready_for_tactical_execute_now": bool(readiness_summary.get("ready_for_tactical_execute_now")),
            "max_allowed_current_action": readiness_summary.get("max_allowed_current_action")
            or completion_summary.get("max_allowed_current_action"),
            "current_tactical_symbol": current_tactical_symbol,
            "recommended_current_turn_action": readiness_summary.get("recommended_current_turn_action")
            or queue_summary.get("recommended_current_turn_action"),
            "should_continue_current_turn": bool(readiness_summary.get("should_continue_current_turn")),
            "current_turn_closeout_reason": readiness_summary.get("current_turn_closeout_reason")
            or queue_summary.get("current_turn_closeout_reason"),
            "next_paper_review_at_utc": readiness_summary.get("next_paper_review_at")
            or queue_summary.get("next_paper_review_at"),
            "next_paper_review_at_shanghai": display_time(
                readiness_summary.get("next_paper_review_at") or queue_summary.get("next_paper_review_at")
            ),
            "next_recommendation_review_at_utc": readiness_summary.get("next_recommendation_review_at")
            or queue_summary.get("next_recommendation_review_at"),
            "next_recommendation_review_at_shanghai": display_time(
                readiness_summary.get("next_recommendation_review_at")
                or queue_summary.get("next_recommendation_review_at")
            ),
            "p0_open_count": len(p0),
            "execute_now_blocking_task_count": len(blockers),
            "evidence_blocker_status": evidence_blockers.get("status"),
            "evidence_blocker_next_mode": evidence_summary.get("recommended_next_mode"),
            "evidence_should_stop_open_loop_research": evidence_summary.get("should_stop_open_loop_research"),
            "evidence_retryable_public_count": evidence_summary.get("retryable_public_count"),
            "evidence_paid_or_api_needed_count": evidence_summary.get("paid_or_entitled_api_needed_count"),
            "evidence_account_or_broker_needed_count": evidence_summary.get(
                "user_account_or_broker_evidence_needed_count"
            ),
            "evidence_time_gated_needed_count": evidence_summary.get("time_gated_learning_outcome_needed_count"),
        },
        "commands": {
            "formal_manual_dispatch": dispatch_command,
            "paper_review_pulse": paper_command,
        },
        "dispatch_loop": [
            {
                "stage": "持仓状态",
                "must_do": "读取 crypto、美股、两路现金、质押、成本和历史建议。",
                "output": "先输出组合表格，再进入 DCA 或候选推荐。",
            },
            {
                "stage": "市场数据",
                "must_do": "获取最新价格、成交量、宏观、情绪、链上/DeFi、美股候选和新闻。",
                "output": "每个关键结论必须带 verified / degraded / disputed / stale / missing 数据状态。",
            },
            {
                "stage": "目标映射",
                "must_do": "把结论映射到 crypto 5-10 年 10x 和美股战术收益目标。",
                "output": "说明更接近目标、暂不改善目标，还是风险太高。",
            },
            {
                "stage": "候选深挖",
                "must_do": "候选必须给出入场、出场、价格、时间、概率、执行度和失效条件。",
                "output": "行动清单最多两档入场、两档出场。",
            },
            {
                "stage": "学习记录",
                "must_do": "写入 recommendation history，并更新 paper/recommendation 复盘日历。",
                "output": "没有记录就不能宣称系统在学习。",
            },
            {
                "stage": "收口判断",
                "must_do": "运行 readiness / queue / status。",
                "output": "没有到期复盘或新问题时停止并汇报。",
            },
        ],
        "crypto_dca_framework": [
            ["SOLUSDT", "主增长候选；数据、链上生态、流动性和宏观节奏都过关时可作为主 DCA。", "conditional_action"],
            ["ADAUSDT", "折价质押卫星；深度折价、质押可持续、生态风险未恶化时作为第二 DCA。", "conditional_action"],
            ["NIGHTUSDT", "5-10 年高凸性尾仓；只在流动性、解锁压力、DUST 需求和项目进展改善时小额进入。", "watch / small_dca_review"],
            ["ETH/lcETH", "已有仓位较大；先补成本、赎回条款和质押流动性证据。", "hold"],
            ["BTC", "不是默认新增主线；只有极端恐慌、明显低估或需要流动性锚时恢复配置。", "watch"],
        ],
        "crypto_dca_timing_gate": crypto_dca_timing_rows(completion),
        "us_tactical_framework": [
            ["当前战术仓仍是最强选择", "继续持有或等待回踩再进，不强行换仓。"],
            ["新候选显著更强", "输出卖出当前战术仓、释放现金、等待候选触发的接力计划。"],
            ["没有候选过双80", "输出 watch / no_action，不硬凑标的。"],
            ["已经释放现金", "现金等待优先当天完成，最多 1-2 个交易日，超过后重评。"],
        ],
        "p0_blockers": [
            {
                "task_id": item.get("task_id"),
                "area": item.get("area"),
                "action": item.get("action"),
                "max_allowed_until_done": item.get("max_allowed_until_done"),
                "due_at": item.get("due_at") or item.get("earliest_at"),
            }
            for item in p0[:8]
        ],
        "execute_now_blockers": [
            {
                "task_id": item.get("task_id"),
                "area": item.get("area"),
                "why_it_matters": item.get("why_it_matters"),
                "max_allowed_until_done": item.get("max_allowed_until_done"),
            }
            for item in blockers[:8]
        ],
        "evidence_blocker_classification": {
            "available": bool(evidence_blockers),
            "summary": evidence_summary,
        },
        "acceptable_progress_next_dispatch": [
            ["Fresh data", "本轮重新获取市场、情绪和持仓信息，不沿用旧结论。"],
            ["Target mapping", "每条建议说明如何影响长期 10x 或美股战术收益目标。"],
            ["Recommendation written", "每条建议写入 recommendation history。"],
            ["Review calendar updated", "明确下一次复盘哪条 paper 或 recommendation。"],
            ["Error attribution", "预测错误时记录原因，不直接自动改规则。"],
            ["Human confirmation", "参数变化只进入 proposed_changes，人工确认后才生效。"],
        ],
        "stop_conditions": [
            "当前没有到期 paper/recommendation 需要复盘。",
            "继续运行只能等待市场时间。",
            "继续运行只是在做开放式深研，而不是回答用户本轮问题。",
            "readiness 输出 recommended_current_turn_action=stop_and_report_status。",
        ] + ([evidence_stop_condition] if evidence_stop_condition else []),
        "terms_note": {
            "goal_mechanism_ready": "机制可用，不代表收益目标已经实现。",
            "active_goal_execution_matrix_ready": "目标执行矩阵已生效，说明原始目标已被写入本地检查清单。",
            "goal_complete": "真实财务结果已经达标；当前仍为 false。",
            "paper_only": "只做模拟或观察，不动真实资金。",
            "execute_now": "可人工确认后立即执行；当前仍未开放。",
            "双80": "目标时间内到目标价的真实预测概率至少 80%，且执行准备度至少 80 分。",
            "cash rail": "资金通道；crypto 和美股现金在不同软件里，不能默认互通。",
            "时间成本风险": "为了等更低价格而少拿质押收益、错过长期上涨暴露或拖慢复利时间的风险。",
            "front_load": "前置 DCA；在长期低位且证据支持时，把未来一小部分定投资金提前使用。",
            "evidence_blocker": "证据阻断分类，说明缺口能否靠继续公开搜索解决；不能解决时应该等样本、API 或账户证据。",
        },
    }


def render_markdown(plan: dict[str, Any]) -> str:
    summary = plan.get("summary") or {}
    commands = plan.get("commands") or {}
    evidence = plan.get("evidence_blocker_classification") or {}
    evidence_summary = evidence.get("summary") or {}
    dispatch_rows = [
        [item.get("stage"), item.get("must_do"), item.get("output")]
        for item in plan.get("dispatch_loop") or []
    ]
    p0_rows = [
        [
            item.get("task_id"),
            item.get("area"),
            item.get("action"),
            item.get("due_at"),
            item.get("max_allowed_until_done"),
        ]
        for item in plan.get("p0_blockers") or []
    ]
    blocker_rows = [
        [
            item.get("task_id"),
            item.get("area"),
            item.get("why_it_matters"),
            item.get("max_allowed_until_done"),
        ]
        for item in plan.get("execute_now_blockers") or []
    ]
    return "\n\n".join([
        f"# Next Manual Dispatch Execution Plan | {plan.get('generated_at')}",
        "这是一份只读计划，不抓新行情、不下单、不转账、不修改持仓账本。它告诉下一次手动调度应该怎样跑，以及什么时候该停止。",
        "## 1. 当前判断",
        markdown_table(
            ["项目", "值", "直白含义"],
            [
                ["手动调度机制", summary.get("goal_mechanism_ready"), "取数、分析、记录、复盘、归因、迭代机制是否可用"],
                ["目标执行矩阵", summary.get("active_goal_execution_matrix_ready"), "原始目标是否已绑定到本地执行矩阵和审计检查"],
                ["财务目标完成", summary.get("goal_complete"), "5年/10年 10x 是否已被真实结果证明"],
                ["下一次正式报告", summary.get("ready_for_next_manual_dispatch"), "用户再次要求时是否可跑正式报告"],
                ["真实立即执行", summary.get("ready_for_tactical_execute_now"), "是否可输出 execute_now"],
                ["最大当前动作", summary.get("max_allowed_current_action"), "当前允许的最高动作等级"],
                ["本轮建议动作", summary.get("recommended_current_turn_action"), "当前应该继续跑还是收口汇报"],
                ["本轮是否继续", summary.get("should_continue_current_turn"), "false 表示本轮应停止并汇报"],
                ["收口原因", summary.get("current_turn_closeout_reason"), "为什么不继续长循环"],
                ["下一次 paper 复盘", summary.get("next_paper_review_at_shanghai"), "Asia/Shanghai 时间"],
                ["下一次 recommendation 复盘", summary.get("next_recommendation_review_at_shanghai"), "Asia/Shanghai 时间"],
                ["证据阻断下一步", summary.get("evidence_blocker_next_mode"), "是否该继续公开研究、等样本、还是补 API/账户证据"],
                ["停止开放式研究", summary.get("evidence_should_stop_open_loop_research"), "true 表示继续公开搜索边际价值很低"],
            ],
        ),
        "## 2. 推荐命令",
        "正式调度命令：\n\n```bash\n" + str(commands.get("formal_manual_dispatch") or "") + "\n```\n\n"
        "最近 paper 样本复盘命令：\n\n```bash\n" + str(commands.get("paper_review_pulse") or "") + "\n```",
        "## 3. 下一次调度闭环",
        markdown_table(["阶段", "必须完成", "输出要求"], dispatch_rows),
        "## 4. Crypto DCA 框架",
        markdown_table(["标的", "规则", "动作上限"], plan.get("crypto_dca_framework") or []),
        "DCA 行动清单最多两档：近价小仓 + 更优回调主仓；不输出三档以上复杂限价表。",
        "## 4A. DCA 时间成本 / 质押机会成本",
        "下一次 DCA 不能默认等回调；必须判断早买入开始质押和获得长期暴露，是否比等待更划算。",
        markdown_table(
            ["资产", "时间决策", "动态买入策略", "长期低位状态", "可前置金额", "近期待投入上限", "等待所需折扣", "现金闲置影响", "原因"],
            plan.get("crypto_dca_timing_gate") or [],
        ),
        "## 5. 美股战术框架",
        markdown_table(["判断", "动作"], plan.get("us_tactical_framework") or []),
        "美股战术仓必须动态识别，不能 hardcode SOXL。CRCL 这类长期保护仓不进入常规短线轮动。",
        "## 6. 当前 P0 阻断",
        markdown_table(["任务", "领域", "动作", "时间", "完成前最大动作"], p0_rows) if p0_rows else "- 暂无 P0 阻断。",
        "## 7. Execute Now 阻断",
        markdown_table(["任务", "领域", "为什么重要", "完成前最大动作"], blocker_rows) if blocker_rows else "- 暂无 execute_now 阻断。",
        "## 7A. 证据阻断分类",
        markdown_table(
            ["类型", "数量", "调度含义"],
            [
                ["可限时重试公开数据", evidence_summary.get("retryable_public_count"), "下次正式调度内重试一次，失败就降级"],
                ["需要 API/数据权限", evidence_summary.get("paid_or_entitled_api_needed_count"), "没有 key 或权限时不继续空跑"],
                ["需要账户/券商/钱包证据", evidence_summary.get("user_account_or_broker_evidence_needed_count"), "需要用户提供截图或导出"],
                ["需要等待样本到期", evidence_summary.get("time_gated_learning_outcome_needed_count"), "到点跑短复盘，不提前长跑"],
                ["建议停止开放式研究", evidence_summary.get("should_stop_open_loop_research"), "true 时本轮应收口"],
            ],
        ) if evidence.get("available") else "- 暂无证据阻断分类。",
        "## 8. 下一次可接受的进步标准",
        markdown_table(["标准", "什么算完成"], plan.get("acceptable_progress_next_dispatch") or []),
        "## 9. 本轮停止条件",
        "\n".join(f"- {item}" for item in plan.get("stop_conditions") or []),
        "## 术语小注",
        "\n".join(f"- `{key}`：{value}" for key, value in (plan.get("terms_note") or {}).items()),
    ]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a read-only next manual dispatch execution plan")
    parser.add_argument("--completion-audit-json")
    parser.add_argument("--readiness-json")
    parser.add_argument("--queue-json")
    parser.add_argument("--evidence-blocker-json")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    completion_path = Path(args.completion_audit_json) if args.completion_audit_json else newest("goal_system_completion_audit_*.json")
    readiness_path = Path(args.readiness_json) if args.readiness_json else newest("next_dispatch_readiness_*.json")
    queue_path = Path(args.queue_json) if args.queue_json else newest("next_goal_execution_queue_*.json")
    evidence_path = Path(args.evidence_blocker_json) if args.evidence_blocker_json else newest("evidence_blocker_classification_*.json")

    errors: list[str] = []
    completion, error = load_optional(completion_path, "completion")
    if error:
        errors.append(error)
    readiness, error = load_optional(readiness_path, "readiness")
    if error:
        errors.append(error)
    queue, error = load_optional(queue_path, "queue")
    if error:
        errors.append(error)
    evidence_blockers, evidence_error = load_optional(evidence_path, "evidence_blocker")

    if errors:
        payload = {
            "generated_at": utc_now(),
            "status": "failed",
            "read_only": True,
            "live_orders_enabled": False,
            "mutates_ledgers": False,
            "errors": errors,
        }
    else:
        payload = build_plan(
            completion,
            readiness,
            queue,
            evidence_blockers,
            {
                "completion_audit_json": str(completion_path),
                "readiness_json": str(readiness_path),
                "queue_json": str(queue_path),
                "evidence_blocker_json": str(evidence_path),
                "evidence_blocker_error": evidence_error or "",
            },
        )

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
