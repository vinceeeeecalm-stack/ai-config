# Derivatives Shadow Evidence Policy

## Purpose

阶段一影子链把公开衍生品证据前移到 discovery 全候选，但不改变生产排序或用户动作。它回答的是“这些因子是否能更早提供方向信息”，不是“是否应该下单”。

## Inputs and binding

`impulse_capture_scanner.py --discovery-only --derivatives-shadow-handoff` 为同轮最多二十个候选输出只读现货因子。`derivatives_shadow_collector.py` 补充：

- OI 1h、4h、24h 变化；
- 当前资金费率；
- mark/index 永续基差；
- 合约主动买入比例；
- 合约 24h 成交量；
- 每项来源、错误和采集时间。

同一轮所有记录共享 `snapshot_id / strategy_version / config_digest / source_digest`。缺少历史 OI 点必须写 `null`，不得写成零变化。

## Directional states

- `CONFIRMED_LONG`：OI 燃料、现货主动买盘、价格结构、合约主动买盘、不过热 funding 和流动性同时通过。
- `CROWDED_CONFLICT`：OI 上升但 funding 过热。
- `OI_ONLY_CONFLICT`：OI 上升，但现货、结构或合约主动成交没有确认。
- `NO_FUEL`：有 OI 数据但没有新增仓位燃料。
- `DATA_INSUFFICIENT`：无法形成方向判断。

这些都是内部标签，始终 `formal_action_eligible=false`。不得映射为 `ENTER_NOW / WAIT_FOR_ENTRY`，不得进入 Paper 或真钱 ROI。

## Failure and latency

单个 symbol 或单个来源失败只影响该候选。每来源超时最多 12 秒，整轮最多 120 秒；未完成候选必须留下明确 `source_errors`。全部公共衍生品来源失败时结果为 `DATA_DEGRADED`，不是“市场没有机会”。

## Promotion gate

阶段一生产晋升必须使用新的 superseding GoalContract，且不得早于配置中的十四天规则窗口。晋升前至少比较：发现提前量、黄金机会识别率、假阳性、资金费率拥挤误判、OI-only 冲突率、来源覆盖和延迟。影子结果不允许回填或改写既有生产建议。
