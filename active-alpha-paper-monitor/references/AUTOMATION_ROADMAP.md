# Automation Roadmap

当前版本：`paper_only`。

## 阶段

| 阶段 | 能力 | 是否真实交易 |
|---|---|---|
| Phase 1 | Binance 数据、动态扫描、paper 信号、模拟开平仓、报告闭环 | 否 |
| Phase 2 | 30-50 笔 closed paper、55%-60% 胜率、正净收益、回撤/样本质量证明 | 否 |
| Phase 3 | `$500` 战术 paper 月度翻倍压力测试、激进但有止损/仓位/回撤约束 | 否 |
| Phase 4 | Binance Spot testnet、极小额真实 spot 候选、kill switch、审批、日志和回滚 | 另行审批 |

## Phase 4 前置条件

- 至少3个月 paper/outcome 记录。
- 胜率、EV、最大回撤、Brier score 和错误归因稳定。
- kill switch、balance limit、日志、回滚和审批机制完成。
- API 权限最小化，默认不能提现。
- manual skill 明确批准策略进入 `api_ready_candidate`。

## V2.136 Paper/Testnet Risk-Control Contract

Before any future testnet or tiny-live review, the paper chain must prove the
same portfolio-level controls through `paper_testnet_risk_control_auditor.py`:

- block new entries after 5% realized loss in one Asia/Shanghai day;
- block new entries at 15% portfolio drawdown;
- halve new paper size after two recent consecutive losses and pause after three;
- enforce open-position, total-exposure, single-symbol, and duplicate-ID limits;
- preserve a versioned paper strategy snapshot, stable change IDs, append-only
  decision history, forward-degradation rollback checks, and human approval
  before any live candidate.

These controls are immediately enforceable only in paper/testnet. They do not
make Phase 2 or Phase 3 proven and do not authorize live trading.

## 当前禁止

- live order
- withdrawal
- margin
- futures/perpetual trading
- options/0DTE
- short selling
- 自动跨通道转账

## 推荐自动化调度

- Daily Alpha Scan: 每天1次。
- US Open Dynamic Scan: 工作日 22:00-24:00 Asia/Shanghai，可在电脑开屏并允许 Codex 运行时执行。
- Event Scan: 市场异常时触发。
- Paper Review: 每天1次。
- Sunday Crypto Realistic Paper Loop: 周日10:00-18:00每小时1次，只做真实感paper。
- Monthly Strategy Review: 每月1次。

自动化任务只应输出报告和 handoff，不应修改真实持仓。

## 周日高频 paper 的晋级边界

- 允许 paper 内自动策略迭代。
- 允许记录可实现模拟 PnL、滑点、费用和深度约束。
- 不允许因单个周日表现优秀直接进入真钱交易。
- 若未来升级 24/7 paper，需要新增全天候调度、并发锁、策略回滚、异常恢复和更完整风控面板。
