#!/usr/bin/env python3
"""Plain-language explanations for audit warnings.

This module is intentionally read-only and side-effect free. It does not fetch
market data, mutate ledgers, write recommendation history, place orders, or
change the max allowed action. It only translates audit findings into user
readable Chinese.
"""

from __future__ import annotations

from typing import Any


WARNING_EXPLANATIONS: dict[str, dict[str, str]] = {
    "cost_basis_coverage": {
        "area": "持仓成本",
        "plain_meaning": "系统知道你持有什么，但 lcETH 和部分 ADA/SOL 的历史成本还不完整，所以不能严肃计算完整收益率。",
        "why_it_matters": "成本不完整时，换仓收益、真实回撤和长期复利测算都会偏粗。",
        "who_can_fix": "用户提供 Coinbase/Ledger/交易所成交导出或截图后，系统做只读校验。",
        "action_limit": "可以给 DCA/持有建议；不能把收益率和换仓收益写成完全成本可信。",
    },
    "staking_and_cash_rails": {
        "area": "现金通道",
        "plain_meaning": "crypto 和美股现金通道是分开的，美股券商的 settled cash / buying power 还没被证据确认。",
        "why_it_matters": "现金通道不清楚时，系统可能高估某一路能马上投入的资金。",
        "who_can_fix": "用户提供券商现金、购买力、未结算资金和挂单截图或导出。",
        "action_limit": "可以分析机会；真实美股仓位大小必须降级。",
    },
    "goal_outcome_not_yet_verified": {
        "area": "目标结果",
        "plain_meaning": "系统机制已经搭好，但真实 5年/10年 10x 收益还没有发生，不能把机制可用当成目标完成。",
        "why_it_matters": "机制通过只证明以后能持续学习和复盘，不证明账户已经达到目标。",
        "who_can_fix": "只能靠未来真实净值表现证明。",
        "action_limit": "目标继续保持 active；不能标记 complete。",
    },
    "us_tactical_50pct_goal": {
        "area": "美股战术仓",
        "plain_meaning": "美股短线资金池能被跟踪，但现金数字部分来自口述/截图，未被券商导出复核。",
        "why_it_matters": "战术仓的仓位大小和风险预算依赖真实可用现金。",
        "who_can_fix": "用户补券商现金和持仓导出。",
        "action_limit": "可以输出 watch/conditional；仓位 sizing 不能升级到高确定性。",
    },
    "research_committee_gate": {
        "area": "多研究员质量",
        "plain_meaning": "研究委员会流程存在，但本次没有足够多的外部 subagent 带来源证据返回，所以不能算深度研究完全通过。",
        "why_it_matters": "缺少独立研究证据时，系统容易沿用旧结论或单一数据源。",
        "who_can_fix": "下一次正式调度时让更多 subagent 或外部研究输出带 source_refs/evidence_items。",
        "action_limit": "不得输出新的 execute_now，只能 watch / paper_only / conditional_action。",
    },
    "research_subagent_output_collection": {
        "area": "subagent 输出",
        "plain_meaning": "系统有收集 subagent 输出的接口，但当前没有收集到完整角色文件。",
        "why_it_matters": "缺角色时，研究委员会只能保守降级。",
        "who_can_fix": "下一轮 subagent 写入 per-role JSON 后再收集。",
        "action_limit": "研究委员会保持 degraded。",
    },
    "paper_validation_gate": {
        "area": "模拟验证",
        "plain_meaning": "短线策略的模拟样本还不够，不能证明它已经有稳定胜率。",
        "why_it_matters": "月度高收益目标需要靠样本复盘证明，而不是靠单次判断。",
        "who_can_fix": "继续跑 paper 复盘，等更多模拟仓位关闭后统计。",
        "action_limit": "短线真实 execute_now 继续阻断。",
    },
    "recommendation_learning_loop": {
        "area": "建议复盘",
        "plain_meaning": "系统已经记录很多建议，但 hit/failed/not_triggered 等真实复盘结果还太少，无法校准 80% 概率。",
        "why_it_matters": "没有结果复盘，就无法知道某类建议到底是否可靠。",
        "who_can_fix": "到期后逐条复盘建议结果，至少先解决 10 条。",
        "action_limit": "概率只能作为学习样本，不能宣称 80% 真实把握。",
    },
    "latest_report_audits": {
        "area": "报告审计",
        "plain_meaning": "最近报告结构可用，但仍有轻微 warning，通常意味着部分数据降级或证据不够完整。",
        "why_it_matters": "报告能读不等于所有结论都能升级成强动作。",
        "who_can_fix": "下一次 fresh 调度和证据补齐后复查。",
        "action_limit": "不阻断可读报告，但会阻断高等级执行。",
    },
    "fresh_market_intelligence_panel": {
        "area": "市场取数",
        "plain_meaning": "报告里市场数据刷新面板不完整或有降级。",
        "why_it_matters": "没有新行情和情绪数据时，不能用旧结论指导短线动作。",
        "who_can_fix": "下一次正式调度时重新抓取行情、新闻、链上和宏观数据。",
        "action_limit": "不得升级为 execute_now。",
    },
    "research_committee_panel": {
        "area": "研究委员会",
        "plain_meaning": "研究委员会面板存在缺口，通常是角色不足、证据不足或来源不清。",
        "why_it_matters": "深度研究不完整时，候选只能作为观察或条件行动。",
        "who_can_fix": "补齐 subagent 输出、来源编号和证据项。",
        "action_limit": "只能 watch / paper_only / conditional_action。",
    },
    "recommendation_history": {
        "area": "建议记录",
        "plain_meaning": "建议历史记录不完整或写入状态不理想。",
        "why_it_matters": "没有结构化记录，后续就无法复盘命中率和错误原因。",
        "who_can_fix": "下一次报告确保 recommendation history 写入成功。",
        "action_limit": "不得宣称学习闭环完整。",
    },
}


GENERIC_TERM_NOTES: dict[str, str] = {
    "warning": "有缺口但系统还能运行；这不是失败，只是对应结论要降级。",
    "blocked_by_evidence": "缺少能证明结论的证据，所以不能升级动作。",
    "partially_proven": "部分证明成立，但还不够完整。",
    "degraded": "数据或流程降级，结论只能保守使用。",
    "paper_only": "只做模拟或观察，不动真实资金。",
    "execute_now": "可人工确认后立即执行；当前仍未开放。",
}


def check_key(check: dict[str, Any]) -> str:
    return str(
        check.get("requirement_id")
        or check.get("name")
        or check.get("check")
        or check.get("id")
        or "unknown_check"
    )


def check_is_warning(check: dict[str, Any]) -> bool:
    if check.get("severity") != "warning":
        return False
    if "passed" in check:
        return not bool(check.get("passed"))
    return True


def explain_audit_check(check: dict[str, Any]) -> dict[str, Any]:
    key = check_key(check)
    template = WARNING_EXPLANATIONS.get(key, {})
    next_action = check.get("next_action") or check.get("remediation") or "下次报告继续复查。"
    return {
        "source_check_id": key,
        "requirement_id": key,
        "status": check.get("status"),
        "passed": check.get("passed"),
        "severity": check.get("severity"),
        "area": template.get("area") or "未分类",
        "plain_meaning": template.get("plain_meaning") or "这个检查还没有被完全证明，因此相关结论需要保守处理。",
        "why_it_matters": template.get("why_it_matters") or "证据不完整时，系统不能把结论升级为高确定性操作。",
        "who_can_fix": template.get("who_can_fix") or "下一次调度或人工证据补齐后复查。",
        "next_step": str(next_action),
        "action_limit": template.get("action_limit") or "不得升级为高确定性执行。",
    }


def build_warning_explanations(audit_or_checks: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    checks = audit_or_checks.get("checks") if isinstance(audit_or_checks, dict) else audit_or_checks
    if not isinstance(checks, list):
        return []
    return [
        explain_audit_check(item)
        for item in checks
        if isinstance(item, dict) and check_is_warning(item)
    ]
