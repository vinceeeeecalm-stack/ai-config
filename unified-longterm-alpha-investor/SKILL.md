---
name: unified-longterm-alpha-investor
description: 投资系统的 legacy schema 与历史账本兼容层。仅在 Manual/Active V3 需要读取旧持仓 lot、成本、质押或 API 估值字段时使用；不再作为当前推荐、行情扫描、执行仲裁或缺失脚本的运行入口。
---

# Unified Long-Term Alpha Investor Compatibility Layer

本 skill 是历史兼容层，不是当前投资决策引擎。

## 当前权威关系

- `manual-investment-strategy-operator` 负责当前持仓、长期 DCA、战术/事件交易、风险门和最终人工确认草案。
- `active-alpha-paper-monitor` 负责主动扫描、paper 验证和统一 handoff。
- 本 skill 只提供 legacy ledger、成本 lot、质押和 API 估值字段。

发生冲突时，Manual V3 的当前覆盖和 `PortfolioStateV2` 优先。本 skill 的旧数量、现金、价格和动作不得覆盖更新证据。

## 按需读取

- 旧账本字段：`references/PORTFOLIO_LEDGER_SCHEMA.md`
- API 估值口径：`references/API_PRICED_LEDGER_POLICY.md`
- 质押兼容字段：`references/STAKING_VALUATION_AND_DYNAMIC_BTC.md`
- Binance 历史数据兼容说明：`references/BINANCE_MARKET_DATA_POLICY.md`

不要读取不存在的旧 references、`references/originals/` 或旧 scripts。不存在或为空的历史路径不属于 V3 运行依赖。

## 数据使用规则

- `config/portfolio_ledger.json` 只补充成本、lot、账户、质押和历史状态。
- 当前数量、现金和持仓状态必须由 Manual V3 的权威解析器决定。
- 旧截图价格只能用于历史对账；当前估值必须引用本轮 EvidenceSnapshotV2。
- lcETH 的产品身份、兑换率、赎回、费用和 slashing 未验证时必须保持 evidence gap。
- 不从旧 USDT、SOXL、NIGHT 或 ENA 行恢复已被更新覆盖为零的仓位。

## 安全边界

- 不自动下单、转账、提现、质押、换仓或修改真实账户。
- 不提供当前 execute-now 建议。
- 不把 legacy 回测、paper 或目标比例解释为收益保证。
- 不再被 Active 的多源 wrapper 当作脚本依赖；需要的当前脚本必须归属 Manual 或 Active 自身。

## 兼容输出

读取 legacy 数据时必须附带：

- `legacy_source_path`
- `legacy_as_of`
- `fields_used`
- `fields_rejected_as_stale`
- `superseded_by_evidence_id`

如果旧账本损坏，只阻断依赖旧成本/lot 的计算，不得覆盖或删除当前持仓真相。
