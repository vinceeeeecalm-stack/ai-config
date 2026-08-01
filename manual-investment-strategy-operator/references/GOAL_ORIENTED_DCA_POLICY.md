# Goal Oriented DCA Policy

## 目标函数

本 policy 用于长期新增资金和 DCA 决策。每轮必须明确选择一个目标：

- `capital_preservation`：优先生存、回撤、流动性和估值安全边际；
- `compound_15_25`：以长期 15%–25% 复合回报路径为目标；
- `aggressive_10x`：以高风险的 5–10 年 10x 路径为目标。

目标不同必须改变权重和结果，不能先指定资产再倒推理由。`aggressive_10x`
沿用以下路径测算：

- 5年组合达到 10x。
- 10年组合达到 10x 以上。
- 用少数高质量高凸性资产提高上行空间，但不让未验证资产拖垮长期核心仓。

5年10x 约等于年化 58%。10年10x 约等于年化 26%。因此，新增资金必须优先考虑资本效率、长期增长弹性、质押复利、流动性、供应压力和数据质量。报告必须先量化当前组合距离所选目标的差距，再推荐资产。

## Independent Next-Dollar Ranking

长期排名必须独立于短线扫描和既有角色模板，先运行
`scripts/longterm_next_dollar_ranker.py`。候选池同时包含当前持仓、美股公司、
普通 ETF、BTC/ETH/SOL 等流动性 crypto、其他新合格资产以及现金。每轮比较：

- 公司基本面或网络采用；
- 护城河与价值捕获；
- 资产负债表或代币供应/稀释；
- 当前估值与 5 年/10 年情景；
- 生存率、预期回撤和流动性；
- 与现有组合的相关性；
- 新增一美元的边际目标贡献。

每轮只输出一个 next-dollar 胜者和最多两个落选原因。BTC、ETH、SOL、ADA
等只能作为基线候选，不能携带固定胜负结论。风险关闭时现金可以成为研究
胜者。现金为零仍要输出研究胜者和计划，只把 `executable_amount` 设为 0。

## Goal Gap Panel

每次长期或 DCA 报告必须输出：

- 当前总资产和默认/月度 DCA 假设。
- 5年/10年 10x 所需年化收益。
- 当前组合是否过度集中、过度防御或过度投机。
- 每个主要持仓对目标的贡献标签：`accelerator / neutral / drag / tail_convexity`。
- 对 ETH/lcETH、ADA、SOL、NIGHT 等主要资产说明其是否提高目标达成概率。

目标标签定义：

| 标签 | 说明 |
|---|---|
| accelerator | 增长、估值修复或质押复利能明显提高 10x 路径概率 |
| neutral | 有生存、流动性或风险平衡价值，但资本效率不足 |
| drag | 仓位过大、预期弹性不足或新增资金效率低 |
| tail_convexity | 上行空间大但失败率、流动性或信息风险也高 |

## Long-Term Goal DCA Engine

默认新增 DCA 资金为 `$1,000/月`，除非用户明确修改。报告可以根据市场情绪、机会质量、现金通道和数据质量建议本月少买、正常买或加速买，但必须说明原因。

### Risk-Adjusted Path Timing Overlay

Sharpe、Sortino、相对 BTC 的 information ratio 和最大回撤只调整已经通过
长期 thesis 的投入节奏，不参与网络采用、价值捕获、供应稀释或长期价格
情景评分。短期路径过热时缩小或延后当前批次；路径改善时可恢复正常批次；
负 Sharpe 本身不得解释为“越跌越买”。详细口径见
`RISK_ADJUSTED_PATH_QUALITY_POLICY.md`。

### Concentrated Long-Term DCA Gate

长期 DCA 必须先解决“新增资金投向是否过度分散”，再讨论候选数量。研究过一个资产不等于应该持有它，候选名单也不能直接变成买入名单。

- 每一期最多只有 `2` 个实际 DCA 目的地：一个主目的地和一个次目的地。其余资产只能是 `hold / watch / replacement_candidate`。
- 组合中有实质权重的 crypto 资产默认最多 `4` 个。`实质权重` 指组合权重达到或计划达到 `5%` 以上；零碎余额、退出中的机会仓和纯观察标的不计入。
- 新资产只有在长期 thesis 总分、净收益质量、供应风险、采用数据和目标贡献明显优于“继续买现有低配资产”时才能加入。否则不得为了叙事覆盖或表面分散而新增。
- 每次推荐必须比较全部实质持仓与跨资产候选，再决定是否需要新资产。新增资产默认采用“替代一个较弱目的地”，而不是在原有资产之外继续叠加。
- 同一生态或同一风险因子高度重合的资产要合并看待。例如 ETH 与以太坊 DeFi 协议代币并不等于完全分散；报告必须说明相关性和共同风险。
- 当前没有 crypto rail 现金时，只能输出下一期 DCA 路线和观察触发，不得把计划写成当前可执行买单。

集中度判断必须输出：

| 字段 | 含义 |
|---|---|
| `material_crypto_asset_count` | 当前权重至少 5% 的资产数量 |
| `max_material_crypto_assets` | 默认 4 |
| `active_dca_destinations_this_cycle` | 本期真正准备投入的资产，最多 2 个 |
| `existing_holding_benchmark` | 新候选需要击败的现有低配持仓 |
| `replacement_not_addition` | 新资产是否替代较弱持仓/目的地，而非简单追加 |
| `correlation_overlap` | 与现有核心仓是否共享同一生态、流动性或监管风险 |

术语备注：`集中` 不是把全部资金压在一个币上，而是只保留少量 thesis 清楚、可持续验证、彼此角色不同的资产；`过度分散` 是持有很多小仓，但没有一个仓位足以对长期目标产生实质贡献。

### Durable Yield Quality Gate

报告不得把质押 APY 写成固定收益。ETH、SOL、ADA 的奖励率都会随网络参数、总质押量、验证人表现、通胀、手续费、服务商抽成和协议风险变化。

长期比较必须使用“净收益质量”，而不是只比较名义 APY：

- `net_reward_rate`：扣除验证人/服务商费用后的预计收益率。
- `reward_source_quality`：奖励来自真实手续费、协议发行，还是额外代币补贴；发行奖励必须同时扣看通胀稀释。
- `liquidity_cost`：解押等待、兑换折价、无法即时卖出的机会成本。
- `operational_risk`：验证人失误、slash、智能合约、托管或流动性质押凭证风险。
- `compounding_reliability`：奖励能否自动复投、到账是否稳定、历史波动是否可接受。
- `net_real_yield_comment`：名义 APY 扣除代币供给稀释后，是否仍能改善持有人价值。

质押复利只能降低达到 10x 所需的价格涨幅，不能替代网络采用和价格增长。若一个资产链上采用长期弱于另一资产，较高 APY 不能自动使其成为主 DCA 方向。

### Long-Horizon Low-Value Acceleration Gate

DCA 决策必须把时间周期拉长到 5-10 年看，而不是只看未来几天是否还能回调。如果决策相关资产同时满足以下条件，报告可以建议“适当加大当期投入”或“前置一部分未来 DCA”：

- 价格处在长期价值低位：例如相对历史高点、200 日均线、90/180 日区间、市值/TVL 或长期估值情景处在有吸引力区域。
- 长期 thesis 没有被破坏：链上、生态、流动性、供应、监管或安全事件没有出现结构性恶化。
- 可质押资产的 APY、锁定、provider 风险和流动性经过校验，且质押收益能真实进入 5年/10年复利测算。
- 当前持仓低配或目标贡献为 `accelerator / satellite / tail_convexity`，新增资金能提高 10x 路径概率。
- 数据质量至少不是 `disputed/stale`；若数据只是轻度 `degraded`，只能 `smaller size / conditional_action`。

这条 gate 的核心判断是：等待更低价格的潜在收益，是否大于“早买入获得长期暴露 + 质押收益 + 降低等待错过成本”的综合价值。报告必须明确写出取舍，而不是默认保留现金等回调。

当市场处在长期价值低位时，DCA 的默认思路要从“尽量等最低点”改成“尽量让资本更早进入长期复利路径，同时保留少量机会仓”。也就是说，近价买入、限价等待、前置未来 DCA 三者要动态选择：如果长期 thesis 完好、质押收益可验证且当前仓位低配，系统应提高近价第一档或前置 DCA 的权重；只有当未来几天到几周的回调概率、幅度和触发条件足够清晰，才把主要资金继续留给限价单。

当资产处在 5-10 年视角的长期价值低位时，报告应主动考虑提高当期投入强度。这里的“低位”不能只看今天涨跌，而要结合历史回撤、长期均线、估值情景、市值/TVL、资金流、链上活跃和长期 thesis 是否完好。若这些条件支持，且资产可质押、当前仓位低配，系统应倾向于 `near_price_entry` 或 `accelerated_dca`，因为越早进入越早获得质押复利和长期上涨暴露。

时间成本风险必须显式展示：如果为了等一个小幅回调而错过更大的长期修复行情，或者少拿数周/月的质押收益，这本身就是成本。只有当预期回调折扣明显大于质押收益、错过上涨缓冲和执行不确定性时，才建议继续等限价单。

质押启动延迟也必须进入判断。ADA 这类资产即使买入后需要几周才开始显示实际质押收益，等待现金闲置仍可能更差，因为“等待买入时间 + 质押启动等待时间”会叠加。报告必须比较两个时间点：现在买入后的预计开始计息日，和等待回调买入后的预计开始计息日；若第二种方案需要明显更低价格才补偿延迟，就应优先近价第一档或小幅前置。

等待不是默认动作，必须被数据证明更优。若一个资产同时处在长期低位、长期 thesis 完好、可质押收益已校验、当前组合低配，报告默认至少考虑近价第一档；只有当 1-2 周内的概率加权回调收益明显超过 `required_pullback_to_wait_pct`，并且有明确限价、等待期限和失效条件时，才允许把主仓完全留给限价单。

前置未来 DCA 的判断必须保守但不能机械：

| 条件 | 允许前置 |
|---|---:|
| 长期低位 verified、质押 verified、当前明显低配、宏观未显著风险关闭 | `0.5-1.0` 个月 |
| 长期低位较强但存在轻度数据降级，或短线波动偏高 | `0.25-0.5` 个月 |
| 数据 disputed/stale、解锁/流动性阻断、thesis 受损 | `0` 个月 |

术语备注：`前置 DCA` 是把未来一小部分定投资金提前使用，不是加杠杆；它只适合长期低位且证据通过时使用，且必须保留基本稳定币机会仓。

### Staking Opportunity Cost Model

对 SOL、ADA、ETH/lcETH 等可质押资产，报告必须估算等待的机会成本：

- `staking_wait_cost = planned_wait_days / 365 * APY * planned_amount`
- `staking_activation_delay_cost = provider_activation_delay_days / 365 * APY * planned_amount`
- `required_pullback_to_wait = staking_wait_cost + missed_upside_buffer + execution_uncertainty_buffer`
- 若预期回调空间小于等待成本和错过风险，优先 `near_price_small_entry` 或 `accelerated_dca`。
- 若 1-2 周内高概率出现更大回调，且不破坏长期 thesis，可使用 `better_pullback_main_entry` 或限价单。

术语备注：`机会成本` 是“因为等待而错过的收益或上涨暴露”；`staking_activation_delay_cost` 是“买入后还要等多久才开始真正获得质押收益的成本”；`missed_upside_buffer` 是“等回调时价格直接上涨的风险缓冲”。

### Long-Term Low-Value Entry Mode

每次 DCA 报告必须单独输出“长期低位入场判断”，不能只把结论藏在分批买入表里。该面板必须回答三个问题：

1. 现在是否处在 5-10 年视角的长期价值低位。
2. 现在买入并开始质押/持有，能否比继续等回调更接近 10x 目标。
3. 如果选择等待，价格至少需要给出多少额外折扣，才足以补偿少拿的质押收益、错过上涨的风险和执行不确定性。

允许的结论只有四类：

| 结论 | 说明 |
|---|---|
| `accelerated_dca` | 长期低位、thesis 完好、质押/持有收益明确、当前低配，适合加大本期投入或前置一部分未来 DCA |
| `near_price_entry` | 当前价格已经足够合理，先买第一档，剩余等更优价格 |
| `limit_order_wait` | 等待回调更划算，但必须有明确限价、等待期限和失效条件 |
| `hold_stablecoin_until_trigger` | 数据冲突、thesis 风险或流动性/解锁风险未清楚，暂不新增 |

若资产处在长期低位且适合质押，现金闲置必须被视为潜在拖累，而不是默认安全选择。报告必须说明“为什么现在不买”，并给出等待触发条件；否则应倾向于 `near_price_entry` 或 `accelerated_dca`。这不等于追高，而是承认长期复利和时间在场本身有价值。

DCA 不使用固定默认比例。每次先评估当前持仓、目标差距、市场体制和长期 thesis，再按角色给出动态目标。以下是组合角色区间，不是必须同时持有的资产清单：

| 角色桶 | 动态区间 | 默认含义 |
|---|---:|---|
| quality core | 45%-65% | 生存能力、流动性和长期采用最强的核心仓；超配时可持有但暂停新增 |
| primary growth engine | 20%-35% | 本期最能提高 5年/10年目标概率的增长主线，通常也是第一 DCA 目的地 |
| staking/value satellite | 5%-15% | 有质押或价值修复逻辑，但采用和资本效率弱于主线 |
| validated new satellite | 0%-12% | 最多一个新增卫星；必须明显优于继续买现有低配资产 |
| stablecoin opportunity bucket | 0%-10% | 只在有明确回调触发或交易摩擦需要时保留，不长期闲置 |

角色区间允许合计不等于 100%，因为报告只为当前组合选择真正需要的角色，不要求为了填满模板而新增资产。现有仓位超出区间时，默认先通过后续 DCA 稀释，除非 thesis 破坏或流动性风险要求主动减仓。

单次月度权重调整默认不超过 10 个百分点；若出现重大 thesis 变化、严重数据冲突、流动性恶化或安全/监管事件，可以提出更大调整，但必须进入 `proposed_changes` 并要求人工确认。

若 Long-Horizon Low-Value Acceleration Gate 通过，当月投入可以高于默认 `$1,000`，或把下一期 DCA 的一部分前置，但必须满足：

- 不动用美股现金通道，除非用户确认跨通道资金已到账。
- 不牺牲必要的交易摩擦/机会仓；稳定币不应长期闲置，但也不能把所有资金一次性打到单一高风险资产。
- 报告必须明确写出 `front_load_extra_months`、`front_load_amount_usd` 和 `near_term_total_budget_usd`：也就是本次是否把未来 0.25/0.5/1.0 个月的 DCA 提前用掉，以及提前后近期待投入上限是多少。
- 最终行动仍遵守两档上限：`near_price_small_entry` 和 `better_pullback_main_entry`，不能输出复杂多档表。
- 加速投入属于 `conditional_action` 或人工确认草案，不是自动下单。
- 若本期没有足够稳定币但用户明确新增现金即将进入 crypto rail，报告可以输出“到账后执行”的前置计划；未到账前不得把未来现金算成可交易余额。

## 每次推荐前的持仓检查

DCA 分配不得脱离当前持仓。每次推荐 DCA 交易对前，必须先检查：

- 当前组合距离 5年/10年 10x 目标的差距。
- 每个主要持仓是否是 `accelerator / neutral / drag / tail_convexity`。
- 可质押资产含 5年/10年复利后，价格仍需上涨多少倍才能帮助实现 10x。
- 等待回调的机会成本是否高于早买入并开始质押的价值。
- 每个现有持仓是否超过由本轮目标与风险预算推导的动态区间。
- 每个低配候选的采用、现金流或链上活跃、供应/稀释、流动性和价格位置是否支持新增。
- 高凸性候选是否有长期 thesis、可退出流动性和可验证的供应/融资路径。
- 可质押资产是否仅因 APY 被高估，而忽略通胀、价值捕获与机会成本。
- 现金或高流动性锚是否在当前风险体制下具有最高边际贡献。
- crypto rail 是否有稳定币或已确认入金；未到账资金不得用于交易建议。
- 美股 rail 现金不得被直接用于 crypto DCA，除非用户确认跨通道转账到账。

## Long-Term Thesis Score

DCA 和长期候选必须生成长期 thesis 分数。短期 `paper_only` 或 `no_deploy` 只限制短线交易，不自动否决长期小额 DCA。

评分维度：

| 维度 | 说明 |
|---|---|
| network_adoption | 用户、开发者、应用、TVL、交易/活跃地址或生态增长 |
| token_utility | 代币是否有真实需求、治理、费用、资源或经济捕获 |
| supply_unlock | 总供应、通胀、解锁、空投/赎回或卖压 |
| liquidity | 交易所深度、成交额、价差和退出可行性 |
| staking_or_yield | 质押 APY、奖励稳定性、锁定、slash、费用和复投可行性 |
| price_location | 相对 7/30/90/200 日趋势和历史回撤位置 |
| narrative_quality | 官方路线图、行业趋势、监管/合规适配和长期叙事 |
| data_quality | 多源数据是否 verified，是否存在 disputed/stale/missing |
| goal_gap_contribution | 对 5年/10年 10x 路径是 accelerator、neutral、drag 还是 tail_convexity |
| macro_sensitivity | 当前宏观环境对 DCA 节奏的影响 |
| compounding_required_price_multiple | 含质押复利后仍需的价格上涨倍数 |

长期建议动作：

- `long_term_dca`: 长期评分高、数据 verified、仓位未过度集中。
- `long_term_watch_or_small_dca`: 长期 thesis 强但数据、流动性或供应压力仍需验证。
- `hold_only`: 存量可持有，但新增资金效率不足。
- `reduce_or_no_new_dca`: thesis 恶化、供应/流动性风险上升或仓位过高。

## DCA 交易对输出

每次报告必须显式输出本期 DCA 交易对，但只比较本次真正影响推荐的标的，不硬性输出固定代币长表。

## DCA Investment Intensity Panel

每次 crypto DCA 报告开头必须先输出“投入强度结论”，让用户可以直接看懂本期是否应该加大投资力度。该面板必须在资产深度分析之前出现，不能把结论藏在后文。

允许的投入强度只有五类：

| 强度 | 含义 |
|---|---|
| `no_extra_deploy` | 不追加，最多保留现有挂单或等待触发 |
| `normal_dca` | 只使用本月既定 DCA 额度 |
| `accelerated_dca_light` | 使用本月额度，并前置未来 0.25 个月 DCA |
| `accelerated_dca_medium` | 使用本月额度，并前置未来 0.5 个月 DCA |
| `full_near_term_deploy` | 当前现金可基本全部进入市场，但仍必须保留最低稳定币机会仓 |

该面板必须明确回答：

- 本次可动用 crypto rail 现金是多少，哪些是已确认现金，哪些只是用户口述或待截图确认。
- 建议本轮实际投入多少美元、保留多少 USDT、是否前置未来 DCA。
- 本轮投入强度为什么是这个档位：长期低位、恐慌情绪、质押复利、当前低配、数据质量、链上/项目风险、流动性和短线趋势分别如何影响结论。
- 若建议等待，必须给出等待需要补偿的最低额外折扣；若不给出明确折扣，不能默认让现金闲置。
- 每个动作必须落到两个以内的买入档：`near_price_entry` 和 `better_pullback_entry`。

投入强度判断规则：

- 当市场情绪处于 Extreme Fear、资产处在长期低位、长期 thesis 未破坏、可质押资产低配且数据质量未 disputed/stale，可至少输出 `normal_dca` 或 `accelerated_dca_light`。
- 若当前组合已经高度集中在某一核心仓，新增资金应优先补低配增长仓或高凸性尾仓，而不是继续加仓超配资产。
- 若链上、解锁、项目进展或流动性数据缺失，但价格和盘口可验证，最多输出 `accelerated_dca_light`；不得输出 `full_near_term_deploy`。
- 若出现单资产项目风险、流动性骤降、供应解锁压力或价格数据冲突，则对应资产只能 `small_dca_only`、`watch` 或 `hold_stablecoin_until_trigger`。
- `full_near_term_deploy` 必须同时满足：数据质量 verified、主资产处于长期低位、短线未出现失控下跌、组合稳定币机会仓仍保留、且本轮候选能明显提高 5年/10年 10x 目标达成概率。

术语备注：`投入强度` 是“本轮该用多少现金进市场”的结论；`前置 DCA` 是把未来一小部分定投资金提前使用，不是加杠杆；`稳定币机会仓` 是为了后续更好价格或突发机会保留的现金。

必须说明：

- `primary_pair`：本期第一推荐交易对。
- `secondary_pair`：第二推荐或小额候选。
- `satellite_pair`：长期高凸性或机会候选，如适用。
- `avoid_pairs`：本期不推荐或暂停交易对。
- 本月建议金额、权重、分批计划、立即执行金额、触发价、失效条件。
- 推荐变化是否提高 5年/10年 10x 目标达成概率。
- 长期价格情景：5年/10年 survival/base/bull 区间、供应口径、质押调整后的收益含义、`10x` 目标适配标签和 DCA 含义。
- 宏观面板给出的 DCA 节奏：`accelerate / normal / split_more / wait_for_pullback`。
- 长期低位与质押机会成本结论：`accelerated_dca / near_price_entry / limit_order_wait / hold_stablecoin_until_trigger`。
- 前置投入结论：若长期低位、质押收益和数据质量同时支持，输出最多 0.25/0.5/1.0 个月的 `front_load` 建议；若不支持，明确写 0，而不是默认保留现金。
- 微观评分和质押复利如何改变本期权重。

若市场数据不足或冲突，`primary_pair` 可以变成 `hold_USDT_until_verified`。

## NIGHT 规则

NIGHT 是 5-10 年高凸性尾仓候选，不是稳定复利仓，也不是默认短线交易仓。

必须跟踪：

- DUST 真实需求和 NIGHT/DUST 经济模型是否产生使用需求。
- 官方路线图、主网/生态应用、开发者增长和合作进展。
- 交易所流动性、成交额、价差、盘口深度和可退出性。
- 空投、赎回、thawing schedule、解锁或其他供应压力。
- 价格是否重新站上长期趋势，而不是只有短线反弹。

若长期评分提高，NIGHT 可以进入小额长期 DCA 或保留较高尾仓；若供应压力、生态停滞、流动性下降或数据冲突，降低权重。不得因为短期评分低而永久写死 `no_deploy`，也不得因为单日上涨而强行加仓。

## Staking Compounding Model

每个可质押且与本期决策相关的资产必须输出：

- 使用的 APY 和来源/provider。
- 奖励是否延迟、锁定、可流动、需要手动领取或自动复投。
- 5年复利倍数：`(1 + APY)^5`。
- 10年复利倍数：`(1 + APY)^10`。
- 含复利后达到 10x 仍需的价格倍数：`10 / 复利倍数`。
- 质押收益是否足以补偿较低增长、锁定、slash、费用、流动性或 provider 风险。

如果 APY、解锁、费用或可流动性缺失，相关资产不得成为强主推荐，只能 `conditional_action`、`smaller_size` 或 `hold_only`。

## Long-Term Price Scenario Panel

每次长期或 DCA 报告必须对本次真正影响推荐的资产输出长期价格情景。该面板不要求固定列出所有代币，而是围绕“本次为什么买、为什么不买、为什么持有、为什么等待”来展示。

必填字段：

- 当前价格、市值、FDV 和供应口径。
- 5年 survival/base/bull 价格区间。
- 10年 base/bull 价格区间。
- 含质押复利后的收益解释：质押能降低多少价格上涨压力，是否足以补偿流动性、provider、slash 或生态风险。
- 10x 目标适配标签：`strong_engine / satellite / tail_convexity / quality_hold / drag_for_new_dca`。
- DCA 含义：`increase / maintain / small_dca_only / hold_only / no_new_dca / trim_review`。
- 置信区间和降级原因。

现有核心仓的通用规则：当任一高质量资产已明显超配时，报告必须同时说明
“长期可持有”和“新增一美元效率偏低”。是否恢复新增由本轮跨资产排名决定，
不能由资产名称或历史角色决定。

## 动态资产规则

任何股票、ETF 或 crypto 都必须用同一轮新鲜证据重新评分。可质押资产评估
净奖励、通胀、解锁和流动性；公司评估收入质量、现金流、资产负债表、稀释
和估值；ETF 评估底层资产、费用、集中度和跟踪结构。某资产过去是核心、
增长主线或卫星，不构成本轮继续新增的理由。SOL、ADA、BTC、ETH/lcETH、
NIGHT、NEAR、SUI、LINK、TRX 等都可以随证据成为胜者、落选者或不合格
候选，不得按名字自动推荐或排除。

## 5年和10年测算要求

当用户要求长期计划时，报告必须至少输出三种情景：

- Survival：增长仓表现不稳定，高凸性资产失败或长期低迷。
- Base：增长主线和少数卫星获得中高增速，质押收益复投。
- Bull / Moonshot：高 beta 增长仓和高凸性尾仓共同贡献。

报告必须说明测算是路径模拟，不是收益承诺。测算不要求硬性展示固定代币对比表，只需解释本次推荐中真正影响长期路径的标的。

## Missing Data / Downgrade

长期 DCA 报告必须列出缺失或降级数据，并说明处理方式：

| 影响 | 处理 |
|---|---|
| no impact | 数据缺口不影响本次推荐 |
| smaller size | 允许建议，但必须降低金额 |
| conditional only | 只允许条件触发，等待下一次数据确认 |
| block | 阻断该资产的新增建议 |

宏观、微观、质押、解锁、链上或流动性数据缺失时，不能隐藏在泛化的 `degraded` 描述里。
