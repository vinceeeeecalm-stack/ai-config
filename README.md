# Investing monorepo

这是活跃投资系统的统一源码与合同仓库。GitHub `vinceeeeecalm-stack/ai-config` 的 `main` 是唯一权威源码；本地工作树用于受治理的修改，全局 `~/.codex/skills` 仅是从权威 `main` 同步的安装镜像。本仓库保留 `mobile-investment-console` 的 Git 历史，并统一版本化当前活跃的投资 Skill、配置、脚本、测试和维护文档。

不进入 Git 的内容包括：运行数据库、市场缓存、账户与策略账本、报告、paper 状态、自动化记忆、临时产物和私密数据。这些内容继续留在原目录，由独立备份和哈希清单管理。

所有实质变更先读取 `AGENTS.md` 与 `.codex/governance/`，冻结目标并取得单写入锁。仓库级策略检查运行：

```text
python3 scripts/verify-monorepo-policy.py
```

各子项目仍保留自己的领域测试与业务验收；仓库策略通过不等于投资业务可用。

## 赚钱闭环唯一入口

主 Skill 安装位置：`/Users/vincentpan/.codex/skills/manual-investment-strategy-operator/SKILL.md`

推荐一句话：

```text
$manual-investment-strategy-operator 按赚钱导向双区闭环运行一次：短期 Crypto 1–7 日输出唯一 Top1 和明确入场/等待计划；长期按我确认的持仓输出最值得新增一美元的方向与目标路径。
```

快捷指令：

```text
$manual-investment-strategy-operator 运行短期赚钱区。读取本月最小成交回执，扫描 Crypto 1–7 日机会，输出唯一 Top1、明确入场判断和本月距离翻倍目标的差距。

$manual-investment-strategy-operator 运行长期复利区。读取我确认的长期持仓和现金，按5年10倍主路径、10年10倍兜底，输出本期最值得新增一美元的方向、买入区间和等待条件。

$manual-investment-strategy-operator 复盘本月赚钱目标。读取所有最小成交回执，计算资金加权净ROI，归因选标、入场、退出和风险错误，只提出下一轮一个最值得修改的规则。

$manual-investment-strategy-operator 完整扫描当前美股盘前或盘中机会：通过 Public Equity Investing/Alpaca 读取实时分钟线、报价和成交，输出唯一 research_top1、明确动作、入场价格、目标、止损和最迟退出；若证据不足则明确 WAIT_FOR_ENTRY 或 NO_TRADE。
```

美股新链路当前是阶段 0 影子验证：盘前与盘中都会完成动态候选、板块、量价、催化、估值、资本风险、Top20、Top3 和唯一 Top1，但不会覆盖生产动作，也不会计入 Paper 或真钱 ROI。当前 Alpaca 权限为 IEX；IEX 不会被伪装为 SIP consolidated quote。

冻结目标：短期战术区为 Crypto 1–7 日月度资金加权净 ROI `+100%` 的进攻目标，并设战术池 `-15%` 紧急暂停线；长期为 5 年 10 倍主路径、10 年 10 倍兜底。以上是可审计方向，不是收益保证，不授权自动交易。

当前数据准备：新的 `$500` `tactical_1_7d` Paper 基线已隔离建立，旧期限未知 Paper 不进入新分母；真钱利润仍为 `UNMEASURED/NOT_STARTED`，长期旧估算为 `DATA_DEGRADED`。系统、账户、真钱利润与长期路径分开报告，不再整体显示笼统 `BLOCKED`。

正式用户动作只允许 `ENTER_NOW / WAIT_FOR_ENTRY / NO_TRADE`。Backtest、walk-forward 与 Paper 只属于内部 `RegressionEvidenceV1`，不能成为真钱动作或收益。权威确定性入口：

```text
python3 manual-investment-strategy-operator/scripts/universal_investment_core.py --self-test
```

恢复时先核验仓库外备份目录中的 Git bundle、工作区快照与 SHA-256 清单，再从 GitHub `main` 恢复源码；运行账本、账户、报告、截图、缓存、密钥和私密配置不得进入公开仓库。
