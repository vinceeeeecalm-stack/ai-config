# SQL / Schema Baseline Execution Policy

## Purpose

用户可能把“SQL文档”“schema文档”“账本文档”混用。当前工作区未发现 `.sql` 文件时，系统必须把已有 schema、账本和推荐历史作为执行基础，而不是假装存在 SQL 数据库。

本策略用于每次手动调度前确认：

- 是否真的存在 `.sql` 文件。
- 哪些 schema / ledger / history 文件是当前权威基础。
- 这些文件是否可读、可解析、字段是否足够支持 5年/10年 10x、月度 `$1,000` DCA、美股战术收益和学习闭环。
- 如果基础数据缺失，报告应该降级，而不是沿用旧结论。

## Authoritative Baseline Files

| 类别 | 默认文件 | 用途 |
|---|---|---|
| 持仓 schema | `unified-longterm-alpha-investor/references/PORTFOLIO_LEDGER_SCHEMA.md` | 定义持仓、现金、质押、成本、推荐记录字段 |
| API估值规则 | `unified-longterm-alpha-investor/references/API_PRICED_LEDGER_POLICY.md` | 明确截图只确认数量/成本，估值必须用 API 多源价格 |
| 当前持仓账本 | `unified-longterm-alpha-investor/config/portfolio_ledger.json` | 当前资产、现金通道、质押、成本覆盖、目标策略版本 |
| 手动建议历史 | `manual-investment-strategy-operator/recommendations/recommendation_history.json` | 建议、结果复盘、概率校准和 proposed changes |
| 模拟交易账本 | `active-alpha-paper-monitor/paper_trades/paper_portfolio_ledger.json` | paper trading 样本、胜率、回撤和晋级证据 |
| 手动配置 | `manual-investment-strategy-operator/config/manual_strategy_config.json` | 目标函数、DCA、风控、双80、学习门槛 |
| 主动监控配置 | `active-alpha-paper-monitor/config/active_alpha_monitor_config.json` | paper/watch 验证、模拟资金和主动扫描边界 |

## Required Gate

每次正式手动报告必须执行 `Schema Baseline Gate`：

```bash
python3 manual-investment-strategy-operator/scripts/schema_baseline_auditor.py \
  --output manual-investment-strategy-operator/experiments/schema_baseline_audit_YYYYMMDD.json \
  --markdown-output manual-investment-strategy-operator/reports/schema_baseline_audit_YYYYMMDD.md
```

该脚本只读，不修改账本，不生成交易指令。

## Pass / Degrade Rules

| 状态 | 含义 | 最大动作 |
|---|---|---|
| `verified_schema_baseline` | 必要 schema 与账本可读、JSON 可解析、关键字段齐全 | 仍需通过后续数据、研究、双80和人工确认 |
| `schema_baseline_degraded` | 没有 `.sql` 但 schema/ledger 基础可用；或部分非阻断项缺失 | `conditional_action / watch` |
| `schema_baseline_blocked` | 持仓账本、推荐历史或 paper ledger 无法读取/解析 | 不得输出新的交易建议，只能输出修复提示 |

`.sql` 文件缺失本身不是阻断项；只要 schema 文档和 JSON 账本齐全，就应解释为“当前系统使用 JSON/schema 账本作为 SQL 等价基础”。

## Report Requirements

报告中新增 `Schema Baseline Panel`：

- 是否发现 `.sql` 文件。
- 本次采用的权威数据基础。
- 每个基础文件的状态：`ok / missing / invalid / degraded`。
- 哪些目标依赖这些文件：长期 10x、月度 DCA、美股战术、学习闭环。
- 如果某个基础缺失，它如何影响动作等级。

## Plain-Language Note

术语小注：

- `schema`：数据结构说明，告诉系统账本里应该有哪些字段。
- `ledger`：账本，记录持仓、现金、成本、质押和交易/建议历史。
- `SQL`：数据库查询语言；当前没有 `.sql` 文件时，不代表系统不能执行，只代表当前权威基础是 schema 文档和 JSON 账本。
