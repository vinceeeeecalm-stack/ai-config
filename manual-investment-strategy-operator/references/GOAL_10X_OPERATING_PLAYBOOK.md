# Goal 10x Operating Playbook

本 playbook 是日常使用入口。它把现有 skill 文档、审计脚本和学习闭环收敛成一套可执行流程：每次由用户手动触发，系统主动取数、分析、输出建议、记录建议、安排复盘，但不自动下单、不自动转账。

## 1. What This System Is

| 项目 | 定义 |
|---|---|
| 系统角色 | 被动手动调度的投资策略分析程序 |
| 触发方式 | 用户主动要求报告、DCA 判断、美股机会扫描或组合复盘 |
| 自动化边界 | 可以自动取数、审计、写建议记录和生成复盘日历；不能自动真实交易或移动资金 |
| 当前验收目标 | `goal_mechanism_ready=true`，即每次调度都能学习和进步 |
| 尚未达成 | `goal_complete=false`，真实 5年/10年 10x 和美股战术高收益还需要长期结果证明 |

术语备注：`goal_mechanism_ready` 是“系统会按流程学习”；`goal_complete` 才是“真实收益目标已经被结果证明”。

## 2. User Goals

| 目标 | 如何落到流程 |
|---|---|
| 5 年内本金 10x | 激进路径，只能通过增长主线、高凸性尾仓、美股战术 alpha 和严格复盘共同尝试 |
| 10 年内本金 10x | 兜底路径，用长期 DCA、质押复利、资产轮动和风险控制提高概率 |
| 每月 `$1,000` crypto DCA | 每次先看目标差距、持仓比例、宏观、链上/流动性、质押，再动态分配，不固定买 BTC |
| 战术资金月度 ROI `100%` 进攻目标 | 优先服务盘中、日度、周度和 1–3 周机会，只约束已确认的战术资金与同通道闲置资金，不包含 CRCL 等长期保护仓，也不是收益承诺 |
| 每次调度主动取数 | 抓市场、情绪、宏观、链上、新闻、社交、候选和现金通道，再输出行动 |

这套系统不保证每次都赚钱。正确目标是每次调度都留下可复盘证据，让 60%-79% 的判断逐步校准到 80%+。

## 3. Standard Manual Dispatch

日常正式报告优先使用一键入口：

```bash
python3 manual-investment-strategy-operator/scripts/manual_dispatch_run.py \
  --run-id YYYYMMDD-manual-goal10x \
  --monthly-dca 1000 \
  --current-tactical-symbol <当前动态战术仓>
```

普通调度不应无限等待。默认每个核心步骤有 timeout；如果外部研究、API 或数据源失败，本轮应降级收口，而不是跑到十几个小时。

Paper 复盘的普通调度命令必须先使用到期守门。未到期时它只输出状态并停止；只有样本已经 `due_now` 时，才会在内部调用一次短脉冲模拟复盘：

```bash
python3 manual-investment-strategy-operator/scripts/due_learning_review_pulse.py \
  --timeout-seconds 180 \
  --format json
```

`sunday_crypto_realistic_paper_loop.py` 属于较长的主动模拟/研究循环，只适合单独打开 active monitor 窗口时运行，不作为普通手动调度的默认复盘命令。

如果只是想先确认“下一次该不该正式跑、先复盘什么、为什么还不能 execute_now”，先运行只读 readiness 检查：

```bash
python3 manual-investment-strategy-operator/scripts/next_dispatch_readiness.py \
  --format markdown
```

该检查只读取最新完成度审计、学习复盘日历和证据缺口包，不抓新行情、不改账本、不授权交易。

| 步骤 | 输出 | 失败时 |
|---|---|---|
| Schema Baseline | 确认本地账本和 schema 可读 | 账本不可读则停止新建议 |
| Fresh Market Intelligence | 当前市场、宏观、情绪、链上、候选数据 | 降级为 `watch/conditional` |
| Manual Report | 可读 Markdown 报告和行动清单 | 不作为可用报告 |
| Paper Validation Pulse | 检查已有模拟仓是否到期/触发退出 | 记录缺口，不授权交易 |
| Learning Review Calendar | 下一次 paper/recommendation 复盘时间 | 不能升级强执行 |
| Report Audits | 完整性、目标覆盖和系统完成度 | 顶层状态变成 failed/degraded |
| Progressive Learning Audit | 样本、命中率、证据角色、下一步 | 只用于学习，不证明收益 |

## 4. Daily Decision Reading Order

每次报告生成后，先看这 6 个位置：

0. **Investment Decision Validity**：先确认 `formal_investment_report_valid` 是否为 `true`。
1. **One Page Conclusion**：今天最大动作等级是什么。
2. **Portfolio / Goal Gap**：当前组合离 5年/10年 10x 还差多少年化。
3. **Crypto DCA Direction**：本月 DCA 买什么、买多少、等什么价。
4. **Tactical Ranked Choices**：当前战术仓是否继续持有、卖出、等待回补或接力；同时显示一个主推荐与最多两个合格备选。
5. **Risk / Downgrade**：哪些数据缺失导致不能强执行。
6. **Learning Review Calendar**：下一次什么时候能复盘，样本还差多少。

如果报告没有明确“现在做什么 / 等什么 / 不做什么 / 何时复盘”，本轮报告不能作为有效调度。

如果调度摘要显示 `formal_investment_report_valid=false`，本轮只能用于结构测试、降级阅读或排查，不能作为正式投资判断入口。`execute_now_valid=true` 比它更严格，即使出现也仍需要人工确认。

## 5. Crypto DCA Rule

每月 DCA 默认 `$1,000`，但必须动态分配：

| 资产角色 | 当前默认处理 |
|---|---|
| SOL | 主增长候选；低配、回调、流动性和链上数据支持时优先 |
| ADA | 深度折价 + 质押复利卫星；生态弱时只小额 |
| NIGHT | 高凸性尾仓；只能在流动性、解锁、项目进展和长期 thesis 更清晰时小额 |
| ETH/lcETH | 已有核心且可能超配；一般不新增，只监控质押/解锁/费用 |
| BTC | 机会型流动性锚，不是默认 DCA 主线 |
| USDT | 机会仓，等待更优回调或数据确认；不长期闲置 |

DCA 行动最多两档：

| 档位 | 含义 |
|---|---|
| 近价小仓 | 数据支持但价格不是极优时，用较小金额进入 |
| 更优回调主仓 | 价格到更好区间、数据仍未恶化时，用主金额进入 |

## 6. US Equity Tactical Rule

美股分成长期保护仓和战术仓：

| 类型 | 操作 |
|---|---|
| 长期保护仓 | 例如 CRCL，默认不为短线卖出 |
| 条件流动性仓 | 例如 COIN，只有机会显著更好才参与轮动 |
| 动态战术仓 | 由当前持仓自动识别，不 hardcode SOXL |
| 杠杆 ETF | 只做短期战术，必须有最晚退出日 |

股票、ETF、杠杆 ETF 与 crypto 短线主建议必须满足：

- 先判断 2-4 周主趋势，再给 1-5 个交易日入场/出场。
- 每个标的最多 2 个入场区间和 2 个出场区间。
- 未通过真实目标达成概率 `>=80%` 和执行准备度 `>=80` 时，只能 `watch / paper_only / conditional_action`。

术语备注：`执行准备度` 是数据质量、流动性、价差、止损、资金来源和人工确认是否齐全的评分，不是收益概率。

## 7. Action Level Ladder

| 动作等级 | 可以做什么 | 不能做什么 |
|---|---|---|
| `no_action` | 明确不动，等待新信号 | 不给入场 |
| `watch` | 观察价位、新闻、数据变化 | 不部署真实资金 |
| `paper_only` | 进入模拟验证 | 不真实买入 |
| `conditional_action` | 给条件计划，用户确认且触发后再考虑 | 不默认立即执行 |
| `execute_now_candidate` | 可进入人工确认草案 | 仍不是自动下单 |
| `execute_now` | 只有双80、数据、风控、现金通道、人工确认全通过 | 不能由社交热度或单源新闻触发 |

当前系统大多数时候应停在 `paper_only / watch / conditional_action`，直到样本复盘和证据足够。

## 8. Learning Loop

每条建议都必须变成可复盘记录：

| 字段 | 为什么需要 |
|---|---|
| recommendation_id | 后续能找到这条建议 |
| entry_zone | 判断入场是否触发 |
| target / review window | 判断命中、失败、未触发或失效 |
| probability_pct | 后续校准预测是否过高或过低 |
| readiness_score | 判断是否因为执行条件不足而失败 |
| invalidation | 明确什么情况说明原判断错了 |

样本门槛：

| 阶段 | 样本数 | 用途 |
|---|---:|---|
| 早期校准 | 10 | 看系统是否开始有方向感 |
| 可用校准 | 20 | 可以提出小幅参数草案 |
| 强验证 | 50+ | 才能更严肃讨论稳定 80%+ |

## 9. Timebox Rule

一次普通手动调度应该有清晰收口：

| 时间点 | 默认动作 |
|---|---|
| 10 分钟 | 给用户短状态：已完成哪些取数/研究，是否出现降级 |
| 20 分钟 | 目标产出一版可读报告或状态报告 |
| 30 分钟 | 强制收口；关闭或降级未返回 subagent；生成剩余证据队列 |

| 场景 | 处理 |
|---|---|
| API 或网页慢 | 使用已有缓存或降级数据生成报告 |
| subagent 不齐 | 标记 `research_committee_degraded`，本轮不 `execute_now` |
| `social_news_agent` 不返回 | 不阻塞持仓/DCA 可读报告；只阻断 `execute_now` |
| paper 样本未到期 | 显示下一次复盘时间，不继续空等 |
| recommendation 未到期 | 进入 learning calendar，不强行复盘 |
| 用户只要即时建议 | 生成降级可读报告，明确缺口和最大动作 |

如果本轮需要超过普通调度时间，应拆成两个任务：先给降级结论，再另开深度研究。

术语备注：`timebox` 是“限时收口”，不是减少分析深度；它的作用是先让用户拿到可读结论，再把未完成证据变成下一轮补采任务。

## 9A. Current Turn Closeout Rule

普通手动调度必须能判断“现在该停下汇报，还是继续跑”。这条规则专门防止一次会话因为长期证据缺口、未到期样本或完整研究委员会补证据而跑到十几个小时。

| 判断结果 | 何时出现 | 当前轮应该做什么 |
|---|---|---|
| `stop_and_report_status` | 机制已可用、没有到期 paper/recommendation 复盘、真实目标尚未完成 | 停止继续跑，汇报已完成内容、剩余缺口和下一次触发条件 |
| `run_short_paper_validation_pulse` | paper 样本已经到期 | 只运行短脉冲复盘，不跑长循环 |
| `stop_and_report_status` | paper 样本 24 小时内到期但尚未到期 | 汇报下一次复盘时间，不为未成熟样本反复空跑 |
| `run_recommendation_review_draft` | 历史 recommendation 已到期 | 生成复盘草案，等待人工确认 |
| `repair_before_next_report` | 手动报告机制或关键账本不可用 | 先修复阻断项，不生成新交易建议 |

术语备注：`current turn` 是“当前这一次对话执行”；`closeout` 是“收口汇报”。它不是停止系统后续学习，只是避免把未来几天或几周才会有结果的事情压在当前这一轮里。

只读检查命令：

```bash
python3 manual-investment-strategy-operator/scripts/next_dispatch_readiness.py \
  --format markdown

python3 manual-investment-strategy-operator/scripts/next_goal_execution_queue.py \
  --format markdown
```

## 10. Next Evidence Priorities

当前系统下一步最有价值的证据不是等待未来样本成熟，而是优先回调 point-in-time 历史数据完成样本外、事件、摩擦和稳定性回归；未来结果并行用于校准和失效检测：

| 优先级 | 证据 | 影响 |
|---|---|---|
| P0 | 历史 point-in-time 回调与 walk-forward | 不等待未来数周即可验证短期策略的样本外胜率、EV、回撤和稳定性 |
| P0 | 主推荐与最多两个备选的同口径回归 | 让用户看到最高优先级判断及其他高质量选择为何排名靠后 |
| P1 | 到期 paper 与 recommendation resolved 复盘 | 并行校准历史模型并检测策略失效，不阻塞首次排名 |
| P1 | 美股 settled cash / buying power 截图或导出 | 决定真实 sizing |
| P1 | lcETH 成本、赎回、费用、解锁条款 | 决定 ETH/lcETH 是否拖慢目标 |
| P1 | ADA/SOL 完整 lot 与质押奖励 | 决定真实收益和 DCA 权重 |
| P1 | 外部研究角色证据补齐到 6+ | 决定 Research Committee 是否解除降级 |

## 11. Plain Command Modes

| 模式 | 命令特点 | 使用场景 |
|---|---|---|
| 正式调度 | 不加 `--smoke`，不跳过审计 | 需要今日报告和可复盘建议 |
| 降级可读报告 | 加 `--allow-degraded-market-intelligence` | 网络/API 不完整，但仍需要方向 |
| 快速结构测试 | 加 `--smoke --use-existing-inputs` | 只检查脚本和格式 |
| 不写推荐历史 | `--skip-recommendation-history-write` | 仅测试，不作为正式建议 |

正式投资判断不要用 smoke 结果。

每次调度摘要必须显示：

| 字段 | 含义 |
|---|---|
| `formal_investment_report_valid` | 是否能作为正式人工投资判断入口 |
| `execute_now_valid` | 是否同时满足正式报告与立即执行动作门槛 |
| `invalid_reasons` | 为什么本轮不能作为正式投资判断 |
