# Research Committee Policy

本文件定义 `manual-investment-strategy-operator` 的强制多研究员流程。目标是避免沿用旧结论、减少单一数据源偏差，并把长期目标、数据质量和执行可行性放在行动建议之前。

## 强制流程

每次生成美股或 crypto 报告前，必须执行：

1. `Prior Thesis Challenge Panel`: 先质疑上一轮结论，列出支持证据、反证、失效条件和是否允许沿用旧 thesis。
2. `Research Committee Gate`: 并行调用多个 subagent 独立取数、独立判断、独立列缺口。
3. `Arbiter Decision`: 主 agent 只做融合仲裁，不能跳过分歧和缺失数据直接给行动。

如果 subagent 工具不可用，或少于 6 个相关研究角色成功返回，报告必须标记 `research_committee_degraded`，且不得输出新的 `execute_now`。

普通手动调度必须按墙钟时间收口：默认 20 分钟内输出可读报告，30 分钟强制停止等待并汇报状态。Research Committee 是质量门，不是无限运行许可。

当当前运行环境提供 `multi_agent_v1` 或等价 subagent 工具时，主 agent 必须优先使用真实 subagent，而不是只运行本地 fallback：

- 对可并行的研究角色使用 subagent 并行研究；如果当前已存在可复用 subagent 线程，可以先 `send_input` 复用，避免重复创建。
- 每个 subagent 必须返回 `MULTI_AGENT_RESEARCH_SCHEMA.md` 定义的 JSON；不能只返回自然语言结论。
- 每个 subagent 必须同时返回 `source_refs`、`evidence_items` 和 `plain_language_notes`。`source_refs` 是可追溯来源编号，`evidence_items` 是机器可检查的数据字段，`plain_language_notes` 是给最终中文报告使用的术语解释。只在 prompt 里写“请解释”不算完成。
- 主 agent 必须把 subagent JSON 保存或汇总为 `research_panel` 输入，再由 `scripts/research_panel_runner.py --external-agent-outputs-json` 校验。
- 收集 subagent 输出时必须使用 `scripts/research_subagent_output_collector.py`；完整研究任务包必须启用 `--fail-on-missing`。若缺少 expected role、输出 `execute_now`、或超过任务包允许的 `max_allowed_action_for_subagent`，collector/quality auditor 必须标记 contract error 或降级，不得把它当成可执行证据。普通手动调度的限时收口可以不阻塞可读报告，但必须把缺席角色标为 missing/degraded，并把最大动作限制在 `watch/paper_only/conditional_action`。
- 若 subagent 槽位已满、部分角色超时、或返回不符合 schema，缺失角色必须进入 `missing_data_summary`，并按降级规则限制最大动作。
- `social_news_agent` 超时或缺席时，必须阻断 `execute_now`，但不能阻断普通可读报告。报告应明确社交情报缺口，并把它放入下一轮 evidence backlog。
- 主 agent 不得用自己补写的观点假装是 subagent 输出；本地 runner 只能作为 degraded fallback。

本地自动化可使用 `scripts/research_panel_runner.py` 将已有持仓快照、目标路径、active handoff、crypto 多源快照和推荐历史转换成统一 `research_panel`。该本地 runner 不是 subagent 的替代品：默认全覆盖模式要求 8 个外部角色都返回（不相关角色可返回 `asset_scope=["not_applicable"]`），至少 6 个角色成功、至少 6 个角色 evidence-verified、至少 6 个角色有 ok source，且不能全部 degraded；否则必须设置 `research_committee_degraded=true`，并把最大动作限制在 `conditional_action/watch/paper_only/hold`。

质量审计失败后，必须用 `scripts/research_evidence_backlog_builder.py` 把 `repair_queue` 转换为下一轮研究证据补采队列。该 backlog 只定义应补哪些数据和来源，不会改变策略权重，也不能解除 action readiness 阻断。

## 研究角色

| agent_id | 职责 |
|---|---|
| `prior_thesis_challenge_agent` | 质疑旧结论，判断是否有反证、失效条件或旧 thesis 不能沿用。 |
| `portfolio_state_agent` | 核对持仓、现金通道、质押/锁仓、目标差距、超配/低配。 |
| `macro_regime_agent` | 检查利率、DXY、VIX、美债、CPI/PCE、风险偏好和资金流。 |
| `crypto_market_agent` | 检查 crypto 价格、1D/7D/30D、成交量、盘口、流动性、funding/OI。 |
| `onchain_defi_agent` | 检查 TVL、费用/收入、稳定币、链上活跃、供应/解锁和数据缺口。 |
| `social_news_agent` | 检查官方公告、关键人物、新闻和社交热度；社交信号不能单独触发实盘。 |
| `us_equity_alpha_agent` | 检查美股开盘走势、sector rotation、财报/新闻和候选相对当前战术仓优势。 |
| `backtest_validation_agent` | 检查历史样本、walk-forward、base rate 和过拟合风险。 |

## 仲裁规则

- 长期目标优先于短期技术面。
- 数据质量拥有否决权：`disputed/stale/missing` 的关键数据必须降级。
- 非降级 research panel 必须满足 `research_method=external_subagent_outputs`、`external_agent_validation.valid=true`、`unique_known_role_count>=6`、`successful_known_role_count>=6`、`evidence_verified_known_role_count>=6`、`roles_with_ok_source_count>=6`、`committee_quality_gate_passed=true`，且不能全部 missing/stale/disputed。
- Evidence quality 与 action readiness 分开判断：研究员可以用可靠证据得出“不能执行”的结论。成本 lot、cash rail、paper 样本、推荐校准、双 80 或人工确认不足应进入 `action_blocker_queue`，不应为了拿到 evidence credit 而隐藏。
- 旧结论只有在 prior thesis challenge 未发现反证时才可沿用。
- 任一关键角色输出 `block/no_deploy/risk_alert`，对应资产不得新增。
- 社交/新闻不能单独触发 `execute_now`。
- 美股/crypto 战术 `execute_now` 仍必须满足真实目标达成概率 `>=80%` 且执行准备度 `>=80`。
- `execute_now` 不是永久硬编码关闭；它只能由 `report_readiness.execute_now_gate_version=positive-path-v1` 的最终门槛打开。该门槛必须同时看到：非降级外部 Research Committee、策略晋级证据通过、无阻断型缺失数据、至少一个候选通过双 80、live order 仍关闭并等待人工确认。
- 最终行动清单仍遵守 Two-Step Action Ladder。

## 报告要求

报告必须包含：

- 旧结论挑战结果。
- subagent 覆盖角色和缺失角色。
- bull/base/bear case。
- 反证和缺失数据。
- arbiter 决策和最大允许动作。
- 每个关键行动的“什么会改变判断”。
