# 统一投资系统仓库协作规则

## Closed-loop delivery governance

- 项目唯一根目录：`/Users/vincentpan/Documents/investing`。这是独立 Git 仓库，不得再使用 `/Users/vincentpan` 的父级仓库。
- 投资 Skill、自动化、配置、数据合同、脚本、测试、接口与持久化的实质变更必须使用全局 `closed-loop-delivery-governor` Skill。
- 同一工作树只有一个写入任务；自动化要改 Skill、配置或脚本时必须先取得治理锁，锁被占用时只能生成修改提案。
- Git 只跟踪源码、Skill、配置、Schema、确定性夹具、测试和维护文档。市场缓存、运行数据库、账本、报告、paper 状态、临时产物和私密数据必须留在 Git 外并由独立清单管理。
- 数据权威以 `.codex/governance/data-authority.json` 为准；估算、历史兼容和 Demo 数据不能冒充当前账户或正式决策输入。
- 仓库策略测试入口：`python3 scripts/verify-monorepo-policy.py`。各子项目仍需运行自己的领域测试与验收入口。
- 只有受影响子项目测试、运行证据、无 P0/P1、版本追溯和高风险独立复验都通过后，相关变更才能达到 `BUSINESS_READY`。
- 用户反馈先追加偏差和回归场景；不得覆盖冻结目标、偏差历史或已发布证据。
