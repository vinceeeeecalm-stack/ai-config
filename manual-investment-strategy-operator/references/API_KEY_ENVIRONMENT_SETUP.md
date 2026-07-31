# API Key Environment Setup

## Purpose

本文件说明如何让手动报告使用免费/只读市场数据 API，同时避免把密钥写入 skill、报告、日志或 git。

## Safe Storage

推荐做法：

1. 复制模板：

```bash
cp manual-investment-strategy-operator/config/api_env_template.txt .env.local
```

2. 在 `.env.local` 中填入你自己的 key。
3. 运行报告时添加：

```bash
python3 manual-investment-strategy-operator/scripts/generate_manual_report.py --env-file .env.local
```

`.env.local` 已被 `.gitignore` 忽略。脚本只会显示变量是否存在，不会输出变量值。

## Supported Variables

| 变量 | 用途 | 当前权限要求 |
|---|---|---|
| `COINMARKETCAP_API_KEY` | Crypto 价格交叉验证、市值和供应量辅助 | market data only |
| `TWELVEDATA_API_KEY` | 美股/ETF quote fallback | market data only |
| `ALPHA_VANTAGE_API_KEY` | 美股 quote 与宏观 fallback | market data only |
| `FINNHUB_API_KEY` | 美股 quote/news fallback | market data only |
| `BINANCE_API_KEY` | Binance public/market-data 权限增强；不用于下单 | market data only |
| `X_BEARER_TOKEN` | Crypto 关键人物社交情报，可选 | read/search only |
| `NEYNAR_API_KEY` | Farcaster/Neynar 社交情报，可选 | read/search only |

## Safety Rules

- 不要把真实 key 放进 `config/api_env_template.txt`。
- 不要把真实 key 贴进报告、recommendation history、experiment 或 git commit。
- 当前 skill 不调用下单、提现、保证金、合约或账户写入 API。
- Binance key 即使存在，也只用于 market data；如果未来要读取账户或下单，必须另开设计和审批。

## Degradation Meaning

- `missing_key`：可选 key 没有配置，不一定阻断报告，但会降低多源校验能力。
- `failed`：源不可达、DNS 失败、限流或响应错误。
- `degraded`：脚本跑完但关键数据缺失，不能支持强动作。
- `verified`：当前核心数据域至少有一个可用源。
