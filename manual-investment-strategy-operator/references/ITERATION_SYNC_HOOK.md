# Iteration Sync Hook

这个文件定义 `manual-investment-strategy-operator` 的迭代同步钩子。

## 目的

本仓库里有两份会影响运行结果的副本：

| 副本 | 路径 | 作用 |
|---|---|---|
| 工作区副本 | `/Users/vincentpan/Documents/investing/manual-investment-strategy-operator` | 日常编辑、迭代、测试 |
| 安装副本 | `/Users/vincentpan/.codex/skills/manual-investment-strategy-operator` | Codex 直接调用 skill 时读取 |

如果只更新工作区副本，不同步安装副本，直接调用 skill 时会读到旧规则、旧脚本或旧持仓，因此报告可能和对话中的最新判断不一致。

## 必跑命令

每次修改以下内容后，都必须运行：

```bash
python3 /Users/vincentpan/Documents/investing/manual-investment-strategy-operator/scripts/sync_installed_skill.py --with-smoke
```

需要同步的内容包括：

- `SKILL.md`
- `references/`
- `scripts/`
- `config/`
- `examples/`
- `evidence_templates/`
- `import_templates/`
- `recommendations/`
- `performance/`
- `unified-longterm-alpha-investor` 中 schema gate 需要的基线文件

## Smoke 的含义

`--with-smoke` 会跑一个轻量安装版测试：

1. 生成当前持仓快照。
2. 生成 5年/10年目标测算。
3. 运行安装版 `manual_dispatch_run.py --smoke`。
4. 验证 schema baseline、持仓覆盖、目标测算和报告入口是否能走通。

Smoke 不是正式投资报告，不允许真实下单结论。它只证明“安装版 skill 可以读取最新逻辑和最新持仓”。

## 失败处理

如果同步脚本输出：

- `missing`：安装版缺文件，先重新运行同步脚本。
- `empty`：目标文件可能是云端占位或复制中断，先执行 `brctl download` 后重跑。
- `invalid_json`：对应配置文件 JSON 格式损坏，必须先修复，不能继续生成报告。
- `diff`：工作区和安装版关键文件内容不同，必须重新同步。

## 白话备注

- `schema baseline`：报告赖以理解持仓、成本、现金、质押和推荐记录的账本规格。
- `installed skill`：Codex 真正调用 skill 时读取的那份本地安装副本。
- `smoke test`：轻量冒烟测试，只看系统能不能跑通，不代表投资建议有效。
