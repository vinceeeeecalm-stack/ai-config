# 美股盘前＋盘中完整机会引擎

## 权限边界

用户仍只调用 `$manual-investment-strategy-operator`。内部按以下顺序工作：

1. `Public Equity Investing` 插件通过 Alpaca 只读连接器获取 market clock、资产身份、snapshot、quote、trade、1m/5m bars。
2. `active-alpha-paper-monitor` 生成动态候选、板块、历史回放和阶段零影子证据。
3. Manual 对唯一 Top1 做估值、官方催化、资本风险和最终人工仲裁。
4. 任何生产赚钱规则变更都必须交给 `closed-loop-delivery-governor`，每十四天最多晋升一个规则。

不调用账户、订单或写权限；不保存插件凭证、连接元数据、完整原始响应或账户字段。阶段零永远
`formal_action_eligible=false`、`production_rule_changed=false`。

## 数据等级

- `CONSOLIDATED_REALTIME`：保留给后续受治理且可验证的 SIP/双 consolidated 接入；当前阶段零本地适配器禁用该等级。
- `IEX_CROSS_VERIFIED`：Alpaca IEX 加第二个不超过 60 秒、差异不超过 1% 的公共实时价格。可形成影子 `ENTER_NOW`，风险层只能为 `SMALL`。
- `IEX_ONLY`：可形成完整研究 Top1；盘前最高影子 `WAIT_FOR_ENTRY`，盘中也不能绕过双源实时门。
- `STALE_OR_CONFLICTED`：过期、冲突或双源差异超过 1%，只能 `NO_TRADE`。

SIP 订阅失败必须记录为权限边界，不能把 IEX 标成 consolidated。当前阶段零即使输入自称 SIP 成功也会拒绝；以后开通 SIP 必须另建治理目标和连接器权威证明，不自动提高风险。普通 `second_source` 字典也不能升级 IEX，只有允许来源的只读 public-price connector envelope 才能形成 `IEX_CROSS_VERIFIED`。

## 完整漏斗

盘前美东 `04:00–09:29` 与盘中 `09:30–15:55` 都执行同一完整漏斗，不把盘前降为候选发现：

```text
动态全市场候选
→ 交易所、身份、$5、OTC、停牌和流动性资格
→ 动态板块热度与指数相对强度
→ 盘前/盘中成交额、相对成交量、trade count、spread
→ 闭合 1m/5m、VWAP、开盘区间、突破或首次有效回踩
→ 官方催化、估值、融资稀释、债务和二元风险
→ 费用后 EV、目标先于止损、盈亏比和历史样本
→ Top20、Top3、唯一 research_top1
→ ENTER_NOW / WAIT_FOR_ENTRY / NO_TRADE
→ 生命周期、持续候选、提醒去重和漏检复盘
```

价格低于 5 美元、OTC、停牌、退市风险、身份不明或 20 日中位成交额不足的标的在排名前排除。局部数据源失败只淘汰受影响候选。
每轮还必须生成 `USEquityDynamicUniverseAuditV1`，证明 Nasdaq、NYSE、NYSE American 的活跃资产池、动态 unusual volume、涨幅、板块轮动与新闻催化发现均已完成；缺少该审计时只能 `NO_TRADE`，不得把固定名单或少量传入候选称为全市场扫描。

## 推荐卡

推荐卡必须绑定同一：

```text
snapshot_id / strategy_version / config_digest / source_digest /
market_session / price_as_of / decision_valid_until
```

只允许三种用户动作。`WAIT_FOR_ENTRY` 必须包含价格区间、触发、五分钟有效期、目标、止损和最迟退出；`NO_TRADE` 的 `decision_card` 必须为空。内部 `small_entry_now` 只能映射为 `current_action=ENTER_NOW, risk_tier=SMALL`，不能直接展示。

10% 是优先目标而非硬门。先比较费用后保守净利润、单位时间与单位风险净利润、目标先于止损概率、催化与结构一致性、价值和资本风险、流动性与退出能力。当前真实建议风险上限仍为 0.5%；1% 和 2% 只显示压力测试。现金为零不改变市场判断，但 `account_execution=NO_DEPLOY_CASH`、金额为 0。

## 阶段零运行

插件响应必须先由 `AlpacaMarketSnapshotV1` 白名单转换，再传给：

```text
python3 ../active-alpha-paper-monitor/scripts/us_equity_intraday_shadow.py \
  --input <secret-safe-evidence.json> \
  --output <runtime-shadow-result.json> \
  --as-of <UTC timestamp>
```

六场景前向影子验收：

```text
python3 ../active-alpha-paper-monitor/scripts/us_equity_intraday_shadow.py --forward-suite
```

阶段零影子 `ENTER_NOW` 不是正式投资建议，不进入 Paper/真钱 ROI，也不改变现有生产动作。只有后续新 GoalContract 完成阶段一规则晋升后，Manual 才能把已验证的实时门接到正式仲裁。

## 长期提醒隔离

CRCL 等长期价值观察只输出 `long_term_value_alert / current_valuation_state / thesis_status / add_or_wait_zone / next_review_at`。它不参与盘内 Top1，不是战术资金来源，短期亏损也不能转入长期底账。
