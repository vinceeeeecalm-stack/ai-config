# Active Alpha Signal Policy

## 信号来源

| 来源 | 用途 | 限制 |
|---|---|---|
| Binance spot ticker/klines/order book | 价格、量能、流动性 | 单独不足以实盘 |
| Impulse capture engine | 分钟级异动、放量突破、盘口承接和假突破过滤 | 只能输出 watch/paper/conditional，不能自动买入 |
| Binance funding/open interest | 拥挤度、squeeze 风险 | 只做风险/候选输入 |
| CoinGecko trending | 叙事发现 | 小币需要额外验证 |
| DeFiLlama | TVL、链上生态 | TVL不等于价格会涨 |
| Yahoo/public US equity screeners | 美股 market movers、most active、开盘量价 | 单独不足以实盘 |
| Public company/news sources | 财报、订单、监管、政策、并购、行业催化 | 未确认新闻最高 watch |
| House/Senate official schedules, Clerk votes and committee pages | 美国 crypto 法案院别、阶段、正式投票/markup 日程 | 二级“本周/周一投票”不得覆盖官方议程 |
| Reddit/X/LunarCrush/Santiment | 社交热度和叙事 | 不能单独交易 |
| Crypto key person / official social intel | 关键人物、官方账号、交易所、稳定币、监管和资金流事件 | 只能输出 watch/paper/conditional/risk_alert |
| QQQ/SOXX/VIX/DXY/BTC dominance | 市场体制 | 降级/升级候选 |

## 信号等级

| 等级 | 条件 | 动作 |
|---|---|---|
| weak | 单一来源或噪声 | research_watch |
| moderate | 价格+量能或叙事+流动性 | paper_only |
| strong | 多源一致且 walk-forward 不失败 | paper_forward_candidate |
| validated | paper 连续命中且风险可控 | small_probe_review |

## 不能做的事

- 不能用社交媒体言论直接买入。
- 不能用关键人物单条发言直接买入；未验证账号、截图二传、搬运和传闻最高只能 `watch`。
- 不能用 funding 异常直接反向交易。
- 不能用 trending 小币直接进入 `$500` 或 `$3000`。
- 不能因为用户要求收益最大化而降低门槛。
- 不能自动真实下单。

## 分数解释

`multi_source_alpha_score_points` 是候选排序分数，不是预测概率。

`impulse_score_points` 是短线异动强度分数，也不是预测概率。它只说明“现在是否出现了值得盯盘或 paper 验证的资金行为”，不能直接写成 80% 目标达成概率。

`risk_adjusted_path.quality_score` 是闭合K线收益路径质量分数，也不是预测
概率或公允价值。它只允许按
`RISK_ADJUSTED_PATH_QUALITY_POLICY.md` 对 research/paper 排名做有限调整。

真实概率必须另行写成：

- `forecast_event`
- `forecast_window`
- `forecast_probability_pct`
- `forecast_base_rate`
- `forecast_invalid_if`

如果缺少历史基础概率和 paper 记录，不得输出 80%+ 预测概率。

## 美股开盘扫描附加规则

- 候选必须和当前战术仓比较，而不是固定和 SOXL 比较。
- `why_better_than_current_tactical_position` 必填。
- 输出最多 Top 1-3，且只能作为 handoff 给 manual skill。
- 不能输出真实下单、期权、0DTE、卖空或保证金建议。

## Crypto 关键人物社交情报附加规则

- 使用 `config/crypto_key_person_registry.json` 作为账号和身份来源。
- 输出字段必须符合 `CRYPTO_KEY_PERSON_INTELLIGENCE_POLICY.md`。
- `social_intel_score_points` 只是信息面强度，不是预测概率。
- 官方安全、tokenomics、上/下架、监管风险可以输出 `risk_alert`，用于暂停新增风险。
- 正向路线图、生态合作或资金流消息必须有官方或跨源确认，才可进入 `conditional_action`。

## Candidate Coverage Relay Gate

动态扫描一旦把资产列为正式候选，候选符号集必须成为下游角色的共同输入，不能继续沿用各角色在扫描前写死的资产列表。每个候选都要生成 `candidate_coverage_matrix`，至少覆盖：

- `market`：价格、成交、盘口、funding/OI 或美股量价；
- `official_event`：项目/公司官方公告、监管/安全/交易所状态，事件驱动下跌还要查处置、冻结、黑名单、修复或恢复进度；
- `social_news`：候选自身的官方/可信社交与新闻交叉验证；
- `asset_specific_risk`：crypto 的合约/桥/解锁/链上风险，或美股的 SEC/融资/稀释/债务风险。

每格必须含 `status / checked_at / sources / symbols`，且 `symbols` 必须明确包含当前候选；不能用一个泛市场报告冒充候选已路由。缺少候选自身的官方事件检查时必须标记 `event_intelligence_degraded`，不得写“没有新催化/没有进展”，真实执行最高 `no_deploy`，但仍可保留独立的 `watch / paper_only / risk_alert`。官方处置进展可以降低新增尾部风险，但不能单独证明事件完全结束或授权追涨。

## US Crypto Legislative Relay Gate

市场结构、稳定币、DeFi 或 token classification 法案必须先经过
`US_CRYPTO_LEGISLATIVE_EVENT_RADAR_POLICY.md`。候选 handoff 必须写清：

- 当前院别与程序阶段；
- House/Senate/committee 哪个事件已经发生；
- 正式 floor schedule 是否列出该法案；
- 二级来源说的是确定日程还是期望窗口；
- 影响资产和传导逻辑；
- 当前量价是否独立确认。

未取得官方 floor schedule 时最高为 `watch`，不得写“确定投票”。
