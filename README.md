# Investing monorepo

这是活跃投资系统的统一源码与合同仓库。它保留 `mobile-investment-console` 的 Git 历史，并统一版本化当前活跃的投资 Skill、配置、脚本、测试和维护文档。

不进入 Git 的内容包括：运行数据库、市场缓存、账户与策略账本、报告、paper 状态、自动化记忆、临时产物和私密数据。这些内容继续留在原目录，由独立备份和哈希清单管理。

所有实质变更先读取 `AGENTS.md` 与 `.codex/governance/`，冻结目标并取得单写入锁。仓库级策略检查运行：

```text
python3 scripts/verify-monorepo-policy.py
```

各子项目仍保留自己的领域测试与业务验收；仓库策略通过不等于投资业务可用。
