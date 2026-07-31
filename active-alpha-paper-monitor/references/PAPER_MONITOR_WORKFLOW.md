# Paper Monitor Workflow

## 运行周期

| 周期 | 目的 | 输出 |
|---|---|---|
| Daily Alpha Scan | 抓取多源信号，更新候选 | alpha monitor report |
| Daily Crypto Paper Auto Trader | 每日/高频复盘 crypto paper 仓位，扫描新候选并模拟真实 API 下单/出单 | daily crypto paper report/handoff/experiment |
| Fast Crypto Paper Auto Trader | 每小时轻量扫 `15m/1h`，及时发现新入场机会并模拟 API 买入 | fast crypto paper report/handoff/experiment |
| Validation Progress Runner | 串联 pre-audit、退出监控、fast paper、可选 daily 深扫和 post-audit，显示证据缺口变化 | validation progress report/experiment |
| Sunday Crypto Realistic Paper Loop | 周日10:00-18:00每小时运行真实感crypto paper链路 | hourly report/handoff/experiment |
| US Open Dynamic Scan | 工作日 22:00-24:00 扫描美股开盘短线候选 | us-open handoff/report |
| Crypto Key Person Intelligence | 扫描 crypto 关键人物/官方账号 | social-key-person handoff/report |
| Event Scan | 监管、安全、slash、暴涨暴跌、funding异常 | event alert |
| Walk-forward Refresh | 更新历史验证 | backtest summary |
| Validation Sample Audit | 聚合 paper、walk-forward、recommendation calibration 样本，明确晋级缺口 | validation sample audit |
| Paper Review | 复盘虚拟交易 | paper trade outcomes + readable portfolio report |
| Monthly Strategy Review | 评估胜率、EV、回撤和错误归因 | proposed changes |

## 每次运行步骤

1. 读取配置和上次 handoff。
2. 抓取公开/授权只读数据源。
3. 计算多源 Alpha 分数。
4. 读取或运行 walk-forward。
5. 检查当前信号。
6. 生成 paper trade plan。
7. 执行 `Research Committee Gate`：默认并行覆盖旧 thesis、组合/现金、宏观、crypto 量价、链上/DeFi、社交/新闻、美股 alpha、历史验证 8 个角色；不相关角色可返回 `asset_scope=["not_applicable"]`，但不能静默缺失，且至少 6 个角色必须成功。
8. 若 subagent 不可用、关键角色缺失或数据源严重失败，写入 `research_panel_missing_reason`，设置 `research_committee_degraded=true`，并把最高动作降级为 `watch/paper_only/risk_alert/no_deploy`。
9. 若 paper 自动执行模式开启，使用 paper portfolio ledger 执行虚拟开仓。
10. 复盘到期 paper 记录。
11. 运行或刷新 `scripts/validation_sample_auditor.py`，把 paper 样本、walk-forward stage、推荐校准和缺口写入证据台账。
12. 若目标是连续推进证据样本，优先运行 `scripts/validation_progress_runner.py`，让它按 pre-audit -> exit monitor -> fast paper -> optional daily -> post-audit 的顺序生成 delta。
13. 输出带 `research_panel` 或明确降级原因的 handoff。

## Research Committee Gate

该 gate 用于降低旧结论锚定。monitor 每次输出 handoff 前都要把候选交给独立研究角色复核：

- `prior_thesis_challenge_agent`: 质疑上轮结论，列出反证和失效条件。
- `portfolio_state_agent`: 检查持仓、现金通道、质押/锁仓、目标差距。
- `macro_regime_agent`: 检查利率、DXY、VIX、美债、CPI/PCE、风险偏好和资金流。
- `crypto_market_agent`: 检查价格、1D/7D/30D、成交量、盘口、流动性、funding/OI。
- `onchain_defi_agent`: 检查 DeFiLlama、TVL、费用/收入、稳定币、链上活跃、供应/解锁缺口。
- `social_news_agent`: 检查官方公告、关键人物、新闻和社交热度；不能单独触发真实交易。
- `us_equity_alpha_agent`: 检查美股开盘走势、sector rotation、财报/新闻和候选相对当前战术仓的优势。
- `backtest_validation_agent`: 检查历史样本、walk-forward、base rate 和过拟合风险。

每个角色必须输出统一 JSON：`agent_id`、`asset_scope`、`sources_used`、`signals`、`confidence_pct`、`data_quality`、`missing_data`、`failed_gates`、`recommended_max_action`、`what_would_change_my_mind`。

当前运行环境提供 subagent 工具时，必须按 `references/SUBAGENT_ORCHESTRATION_RUNBOOK.md` 先并行调用真实 subagent，再把结果通过 `--external-agent-outputs-json` 交给 active 脚本。脚本缺少外部 JSON 时必须保留 `research_panel_missing` 与 `research_committee_degraded=true`，不得把本地扫描结果伪装成完整投委会。

## Crypto Key Person Intelligence

用户触发或自动监控时可运行 crypto 关键人物社交情报。该链路只发现信息面变化，不决定真实交易。

输出文件：

```text
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-social-key-person-intel-handoff.json
active-alpha-paper-monitor/reports/YYYY-MM-DD-social-key-person-intel.md
active-alpha-paper-monitor/experiments/YYYYMMDD-social-key-person-intel.json
```

执行规则：

- 读取 `config/crypto_key_person_registry.json`。
- 抓取官方 RSS/站点、X Recent Search、Bluesky、Farcaster/Neynar 和 Reddit 中可用的数据源。
- 标记身份可信度、事件类型、资产映射、时效性和跨源确认。
- 输出 `social_intel_score_points`、`recommended_max_action` 和风险标签。
- 不输出真实交易指令；manual skill 必须二次审查。

## Sunday Crypto Realistic Paper Loop

周日 10:00-18:00 Asia/Shanghai 可运行每小时 Crypto paper 循环。该循环是未来真实交易链路前的模拟测试，只使用 `$500` paper ledger。

输出文件：

```text
active-alpha-paper-monitor/reports/YYYY-MM-DD-sunday-crypto-hourly-HHMM.md
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-sunday-crypto-hourly-HHMM-handoff.json
active-alpha-paper-monitor/experiments/YYYYMMDD-HHMM-sunday-crypto-realistic-paper.json
active-alpha-paper-monitor/paper_trades/YYYY-MM-DD-sunday-crypto-hourly-HHMM-paper-trades.json
```

执行规则：

- 只做 long spot paper，不做真实下单。
- 入场/出场使用 bid/ask、手续费、滑点、1%深度和成交量约束估算。
- 信息面会纳入 CoinGecko trending、Reddit 24h关键词、funding/OI、24h量价异动，用于发现短期强波动候选。
- 若没有严格候选，强信息面 + 当前信号 + 流动性通过时，允许 `$25` paper 探索仓。
- 每小时允许 paper 内自动策略版本迭代，但所有迭代都必须写入 experiment。
- 无硬性日内回撤停止线，但每次报告必须显示最大回撤和亏损路径风险。
- 数据冲突、盘口缺失或流动性不足时，只输出观察报告，不开新 paper 仓。

## Daily Crypto Paper Auto Trader

该链路把周日真实感 paper loop 泛化为日常调度，用于验证“如果未来接入真实交易 API，系统是否能按相同规则及时模拟买入/卖出”。当前仍只做 paper。

输出文件：

```text
active-alpha-paper-monitor/reports/YYYY-MM-DD-daily-crypto-paper-HHMM.md
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-daily-crypto-paper-HHMM-handoff.json
active-alpha-paper-monitor/experiments/YYYYMMDD-HHMM-daily-crypto-paper-auto-trader.json
active-alpha-paper-monitor/paper_trades/YYYY-MM-DD-daily-crypto-paper-HHMM-paper-trades.json
```

执行规则：

- 使用 `scripts/daily_crypto_paper_auto_trader.py`。
- 每次运行先复盘 open paper 仓，触发止盈/止损/到期时按真实盘口假设模拟卖出。
- 然后抓取 Binance spot 24h、盘口、深度、K 线，CoinGecko 校验，Reddit/trending/funding/OI 信息面。
- 使用 walk-forward 当前信号 + 信息面 + 流动性门决定是否开新的 long spot paper 仓。
- 开仓仍只允许 virtual paper order，必须记录手续费、滑点、bid/ask spread、1% depth、entry/stop/take/expiry。
- 每次 paper 买入/卖出都必须写入 `paper_orders`，模拟真实交易 API 的 market order 生命周期，但 `live_orders_enabled=false`。
- 若运行时提供 `--external-agent-outputs-json`，daily/fast/Sunday loop 必须把合法 6+ subagent research panel 写入 handoff、experiment 和报告摘要；否则必须写 `research_panel_missing`、`research_committee_degraded=true` 和缺失原因。
- open paper 仓位不得只被动等待到期；调度时必须检查动态退出，包括移动止盈保护、信息衰减退出、硬止损、硬止盈和到期。
- `paper_position_exit_monitor` 是独立快速退出链路：只读取 open paper 仓位、刷新盘口和信息面、模拟卖出、写报告；不得执行深度策略扫描或新开仓。
- `paper_position_exit_monitor` 也必须在 report / experiment / handoff 中同步 research 降级字段；它不产生真实交易许可。
- `daily_crypto_paper_auto_trader` 必须用动态 paper 仓位：根据 OOS、信息强度、现金占比、总敞口、单仓上限、流动性和深度把仓位分为 minimum/mid/accelerated/strict，但仍只限 paper。
- 当 paper 现金明显闲置时，`daily_crypto_paper_auto_trader` 可扩大预筛候选池、限制单一 symbol 占满候选表，并在同一次调度内开多个不同 symbol 的 paper 仓。该逻辑只提升模拟测试覆盖率，不代表真实交易授权。
- `15m` 短周期仓位必须使用更紧的移动止盈和更短的信息衰减退出窗口，避免短线信号被动拖成长期仓。
- 目标 `monthly_double` 只作为追踪指标，不得把未验证策略宣传为保证收益。
- 当 open paper 达到上限、数据冲突、流动性不足或策略证据不足时，只输出观察报告。

## Fast Crypto Paper Auto Trader

该链路是 daily 链路的轻量入场层，解决完整 walk-forward 深扫耗时过长导致入场不及时的问题。

输出文件：

```text
active-alpha-paper-monitor/reports/YYYY-MM-DD-fast-crypto-paper-HHMM.md
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-fast-crypto-paper-HHMM-handoff.json
active-alpha-paper-monitor/experiments/YYYYMMDD-HHMM-fast-crypto-paper-auto-trader.json
active-alpha-paper-monitor/paper_trades/YYYY-MM-DD-fast-crypto-paper-HHMM-paper-trades.json
```

执行规则：

- 使用 `scripts/fast_crypto_paper_auto_trader.py`。
- 默认只扫 `15m/1h`，使用较小策略上限和较短 K 线缓存窗口，适合 hourly 自动化。
- 仍先复盘 open paper 仓位，再扫描新入场候选。
- 每次最多开一个新 symbol 的 paper 仓；如果 open 仓已满、候选已持仓、或剩余候选未通过信息/历史/流动性 gate，则不强行开仓。
- 如果 fast 链路反复只发现已持仓 winner，且该仓已有浮盈或移动止盈保护、信息分达标、单 symbol 暴露未超限，则可开一个 `winner_scale_in_probe` paper 仓。该加仓仍使用真实盘口假设、手续费、滑点、深度和跨源校验。高信息分、已有盈利保护的仓位允许用较小样本做 paper 加仓测试，但必须在报告中暴露样本不足风险。
- 为了贴近月度翻倍的测试目标，paper 组合不再用过低的固定仓位槽位压制资金利用率。若现金占比偏高、总敞口仍低于上限、并且候选通过当前信号/流动性/跨源校验，fast 与 daily 链路可扩大候选扫描宽度和 paper 开仓槽位；所有扩容必须记录在 experiment，并继续禁止真实下单。
- `social_key_person_intel` handoff 会进入 fast/daily 的 `info_pressure_score`，但只作为候选排序与 paper sizing 的辅助因子。风险公告、监管、token unlock 等风险类信息只提高监控优先级，不得单独触发 long paper 或真实交易。
- 深扫发现新策略仍由 `daily_crypto_paper_auto_trader.py` 或 Sunday loop 承担；fast 链路负责更及时地执行已可验证的短线机会。
- 输出仍必须显示 `live_orders_enabled=false`，不得调用真实下单或私有 API。

## Validation Progress Runner

该 runner 不是新的交易策略，而是把 paper 证据推进流程变成可重复调度的验证循环。

输出文件：

```text
active-alpha-paper-monitor/reports/YYYY-MM-DD-validation-progress-runner-YYYYMMDD-HHMMSS-ffffff.md
active-alpha-paper-monitor/experiments/YYYYMMDD-HHMMSS-ffffff-validation-progress-runner.json
```

同一分钟内可能连续运行多次 validation runner，因此 artifact 名称必须至少包含秒级时间和唯一后缀；不得覆盖上一轮 paper evidence。

执行规则：

- 使用 `scripts/validation_progress_runner.py`。
- 每轮先运行 `validation_sample_auditor.py` 取得基线，再运行 `paper_position_exit_monitor.py` 复盘 open paper 仓。
- 默认继续运行 `fast_crypto_paper_auto_trader.py`；只有显式传入 `--include-daily` 时才追加较重的 `daily_crypto_paper_auto_trader.py`。
- 若显式传入 `--include-walkforward-refresh`，runner 会把 `weekly_goal_strategy_lab.py` 输出保存为新的 walk-forward evidence，帮助 `target_research_pass` 缺口真实刷新。
- 若显式传入 `--include-current-signal-probe`，runner 会保存当前信号探针结果，供后续 active/manual 报告引用。
- 最后再次运行 `validation_sample_auditor.py`，输出 equity、closed count、open count、paper order count、failed gates 和 sample gaps 的变化。
- 所有 child step 必须输出 JSON；若 child 失败，runner 标记 `degraded_child_failure`，但仍尽量输出 post-audit。
- 任一 child stdout 或其引用 JSON artifact 中出现 `live_orders_enabled/private_api_used/private_api_keys_used/allow_real_orders=true` 时，runner 标记 `blocked_safety_error`。
- runner 只推进 paper/验证证据；`max_allowed_action` 不得超过 `paper_only`，不能绕过 manual skill。

## US Open Dynamic Scan

工作日 22:00-24:00 Asia/Shanghai 可运行美股开盘动态扫描。该扫描只发现候选，不决定真实交易。

输出文件：

```text
active-alpha-paper-monitor/handoffs/YYYY-MM-DD-us-open-handoff.json
active-alpha-paper-monitor/reports/YYYY-MM-DD-us-open-monitor.md
```

输出规则：

- 候选来自动态数据源，不固定股票池。
- 最多输出 Top 1-3。
- 每个候选必须包含入场区间、目标价、目标窗口、止损、最晚退出、真实概率、执行准备度和相对当前战术仓的优势。
- 若候选未验证或不能明显强于当前战术仓，输出 `watch`。
- 不自动真实下单，不修改真实持仓。

## Paper Portfolio Ledger

V2.136 adds a shared portfolio risk gate before every entry-capable paper path.
It evaluates Asia/Shanghai daily realized loss, recent consecutive losses,
portfolio drawdown, exposure, position counts, duplicate trade/order IDs and
paper-only rollback evidence. Exit review remains active while new entries are
reduced or blocked.

默认模拟本金为 `$500`。ledger 文件：

```text
active-alpha-paper-monitor/paper_trades/paper_portfolio_ledger.json
```

执行原则：

- 只做虚拟现货 long，不做真实下单。
- 每次开仓必须记录 `paper_trade_id`、信号来源、入场价、数量、止损价、止盈价、到期时间。
- 默认单笔不把全部 `$500` 打满，按候选质量和风险预算分配。
- 触发止盈/止损时立即虚拟平仓；未触发则到期平仓。
- 到期复盘必须写入 realized PnL、胜负、exit reason，并进入下一轮策略学习。

常用命令：

```bash
python3 active-alpha-paper-monitor/scripts/paper_portfolio_engine.py open \
  --trade-id paper-YYYYMMDD-TRX-001 \
  --symbol TRXUSDT \
  --strategy-family "1d momentum" \
  --notional 150 \
  --stop-pct -8 \
  --take-profit-pct 20 \
  --max-holding 7d

python3 active-alpha-paper-monitor/scripts/paper_portfolio_engine.py review

python3 active-alpha-paper-monitor/scripts/paper_portfolio_engine.py summary
```

`review` 每次运行都必须同步生成一份可读报告：

```text
active-alpha-paper-monitor/reports/YYYY-MM-DD-paper-portfolio-review.md
```

该报告必须包含当前持仓、交易跟踪、复盘动作和仍需监控的 open paper 标的。

## Paper Trade 字段

```json
{
  "paper_trade_id": "paper-YYYYMMDD-symbol-seq",
  "symbol": "SUIUSDT",
  "strategy_family": "pullback",
  "created_at": "ISO-8601",
  "entry_rule": "next closed signal or limit price",
  "entry_price": null,
  "stop_price": null,
  "take_profit_price": null,
  "max_holding_window": "24h-7d",
  "virtual_notional_usd": 150,
  "status": "planned | open | closed | expired",
  "outcome": "pending | hit | failed | not_triggered | invalidated",
  "notes": []
}
```

## 晋级规则

| 阶段 | 条件 | 真实资金 |
|---|---|---|
| `research_watch` | 多源分数或叙事出现，但历史不足 | 不允许 |
| `paper_only` | 历史或多源部分支持，仍未通过严格门槛 | 不允许 |
| `paper_forward_candidate` | 当前信号触发且风险不高 | 不允许 |
| `small_probe_review` | paper 连续命中，walk-forward 不失败 | 交给 manual skill |
| `live_candidate` | manual skill 人工确认 | monitor 仍不下单 |

walk-forward evidence 必须单独保存为 experiment，并由 manual skill 的 `strategy_promotion_evaluator.py` 消费。即使 walk-forward 出现高 OOS 收益，也只能作为研究/纸面交易候选，不能绕过 forward paper ledger、recommendation outcome calibration、Research Committee 和人工确认。

## Validation Sample Audit

`scripts/validation_sample_auditor.py` 是只读审计层，默认输出 JSON/Markdown。它必须：

- 以 `paper_portfolio_ledger.json` 为当前组合状态来源，统计 cash、equity、open、closed、drawdown 和 `live_orders_enabled`。
- 扫描 `paper_trades/` 历史快照时按 `paper_trade_id` 去重；同一笔交易优先保留字段最完整的 full trade，event/review summary 只作为补充证据。
- 分离 open 浮盈、closed realized PnL 和 walk-forward OOS，不得把三者混成真实胜率。
- 输出 `closed_paper_trades_needed`、`calibration_resolved_needed`、`walkforward_target_research_pass_needed` 等缺口。
- 输出 `next_validation_queue`，但该队列只代表 paper/验证优先级，不代表实盘买入。
- 一旦发现 `live_orders_enabled=true` 或 `private_api_used=true`，必须直接 `block`，并交给 manual skill 人工审查。

晋级解释：

- `research_watch`: 历史样本太少或当前信号不足，只能观察。
- `paper_only`: 允许进入 paper 仓或 paper 队列，仍不允许真实资金。
- `target_research_pass`: 只能说明历史研究值得继续 paper；不是实盘许可。
- 若 latest walk-forward 没有任何 `target_research_pass`，manual 报告必须把短线策略保留在 `research_required / paper_only`。

## 失败处理

- 连续2次 paper failed：降级为 `watch`。
- 连续3次 paper failed：暂停该策略本月扫描。
- 数据源冲突：标记 `disputed`，不进入 paper。
- 单一社交热点：最高 `research_watch`。
- funding 过度拥挤：降级或风险警报。
