# Research Committee Policy

本文件定义 `active-alpha-paper-monitor` 输出 handoff 前必须执行的多研究员复核。monitor 只发现候选和风险，不授权真实交易。

## 强制流程

委员会在发现阶段之后运行，不能阻塞 Top3 首次输出。候选进入相应动作层级前：

1. 对候选做旧 thesis challenge，避免沿用上一轮结论。
2. 并行执行 Research Committee Gate。
3. 写入 `research_panel`；若不可用，写入 `research_panel_missing_reason` 并设置 `research_committee_degraded=true`。
4. 降级 handoff：缺少研究面板时，最高只能 `watch/paper_only/risk_alert/no_deploy`。

分层要求：

- discovery：不调用委员会；
- `intraday_scalp` 小仓：微观结构与组合风险两个角色；
- 1–7 日完整仓位：四个与候选直接相关的角色；
- 二元事件、新高风险资产或重大长期配置：六个以上角色。

委员会缺失只降低动作资格，不能删除已经由市场数据生成的 Top3。

当当前运行环境提供 `multi_agent_v1` 或等价 subagent 工具时，monitor 必须优先使用真实 subagent 输出，不得用本地脚本生成的观点伪装成 subagent：

- 对可并行的研究角色使用 subagent 独立取数、独立判断、独立列缺口。
- 每个 subagent 必须返回 `MULTI_AGENT_RESEARCH_SCHEMA.md` 中的统一 JSON。
- 真实 6+ 成功返回角色 JSON 可作为 `research_panel` 嵌入 handoff，但还必须通过质量门槛：`evidence_verified_known_role_count>=6`、`roles_with_ok_source_count>=6`、`committee_quality_gate_passed=true`，且不能全部 degraded。若少于 6 个成功角色、校验失败、质量门槛失败或所有角色均为 degraded，必须写 `research_panel_missing=true`、`research_panel_missing_reason` 和 `research_committee_degraded=true`。
- 若质量门槛失败，下一轮 monitor 应参考 `RESEARCH_EVIDENCE_BACKLOG_POLICY.md` 生成或消费 evidence backlog，优先修复 role-native evidence gaps，而不是重复输出同类 degraded handoff。
- 本地脚本只能作为 degraded fallback 或候选发现器，不能授权真实交易，也不能输出 `small_probe_review` 以上的候选等级。
- 社交、funding、trending 或单源新闻只能影响 watch / paper priority，不能替代 Research Committee Gate。

## 研究角色

| agent_id | 职责 |
|---|---|
| `prior_thesis_challenge_agent` | 质疑旧结论，列出反证、失效条件和旧 thesis 是否可沿用。 |
| `portfolio_state_agent` | 检查持仓、现金通道、质押/锁仓、目标差距和候选是否适合当前组合。 |
| `macro_regime_agent` | 检查利率、DXY、VIX、美债、CPI/PCE、风险偏好和资金流。 |
| `crypto_market_agent` | 检查 crypto 量价、盘口、流动性、funding/OI。 |
| `onchain_defi_agent` | 检查 TVL、费用/收入、稳定币、链上活跃和供应/解锁缺口。 |
| `social_news_agent` | 检查官方公告、关键人物、新闻和社交热度；不能单独触发真实交易。 |
| `us_equity_alpha_agent` | 检查美股开盘动态、板块轮动、新闻和候选相对当前战术仓的优势。 |
| `backtest_validation_agent` | 检查历史样本、walk-forward、base rate 和过拟合风险。 |

## Monitor 限制

- monitor 的 `max_allowed_action` 不是交易许可。
- 社交强信号但价格/成交量/流动性未确认时，最多 `watch`。
- 历史验证不足时，最多 `paper_only`。
- 任一关键数据冲突或过期，必须写入 `missing_data_summary` 并降级。
- manual skill 必须重新执行组合、现金、长期目标、风险和人工确认 gate。
