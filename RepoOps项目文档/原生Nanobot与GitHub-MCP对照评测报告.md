# 原生 nanobot 与 GitHub MCP 对照评测报告

评测日期：2026-08-01。

## 一句话结论

在同一 15 条跨语言历史 Issue 上，RepoOps v4 的最佳完整 run 达到 15/15 结构化成功、
100.0% 分类和 93.1% File Recall@5；RepoOps 出现前的原生 nanobot 与“原生 nanobot +
官方 GitHub MCP Server”都达到 15/15、100.0% 和 90.4%。因此 RepoOps 相对原生
nanobot 的可观测文件定位增益只有 **2.7 个百分点**，并且全部来自一条需要追踪 5 个
实现阶段的 TypeScript feature；不能把对 Claude Code 的大幅优势直接解释为相对
nanobot runtime 的同等增益。

同日按当前代码重复运行的 RepoOps v5 只有 10/15 生成结构化答案，5 条在
`tool_budget_exhausted` 后没有合法 JSON。这说明 v4 展示了当前实现的质量高点，
但单次 run 的结构化收尾稳定性仍不足；面试时应同时披露 v4 与重复 run，而不是只报
最好数字。

## 总表

所有全量指标都以 15 条为分母；失败输出按错误计 0，不做人工补写。

| 系统 / run | 结构化成功 | 分类准确率 | File Recall@5 | 调用 | Invalid | Tokens | 平均耗时 |
|---|---:|---:|---:|---:|---:|---:|---:|
| RepoOps v4（此前锁定完整 run） | **15/15** | **100.0%** | **93.1%** | 137 | 0.73% | 1,881,777 | 125.8s |
| 原生 nanobot（RepoOps 前） | **15/15** | **100.0%** | 90.4% | **136** | 2.94% | **1,354,894** | 86.9s |
| 原生 nanobot + GitHub MCP v1.0.5 | **15/15** | **100.0%** | 90.4% | 155 | 1.29% | 1,497,269 | **69.0s** |
| Claude Code v1（既有对照） | 13/15 | 86.7% | 77.1% | 220 | 5.45% | 2,424,549 | 137.8s |
| RepoOps v5（同日重复稳定性 run） | 10/15 | 66.7% | 59.6% | 162 | **0.62%** | 1,893,061 | 350.1s |

耗时不是严格 SLA 对照：前三个新 run 使用 3 个并发 case，既有 v4/Claude 使用 2 个，
且模型服务状态不同；每题 `duration_ms` 虽单独计时，仍可能受 Provider 排队影响。
分类、文件、结构化成功、调用和 token 是本报告更重要的指标。

## RepoOps 相对原生 nanobot 到底增加了什么

以成功完整的 RepoOps v4 与原生基线比较：

- 分类和结构化成功没有提升，都是 100.0% 与 15/15；
- File Recall@5 从 90.4% 到 93.1%，提升 2.7 个百分点；
- 136 与 137 次调用基本相同；
- Invalid Call Rate 从 2.94% 降到 0.73%；
- RepoOps 使用 38.9% 更多 runner-reported tokens；
- RepoOps 的工具角色 precision / recall 为 80.9% / 85.3%，原生工具归一化后为
  66.7% / 40.0%，但两套工具词表不同，这两个数字只能说明工作流覆盖，不能当作端到端
  准确率。

2.7 个百分点的文件召回差异全部来自 `vitest-issue-7352`：人工标签有 5 个生产文件，
RepoOps 命中 4 个，两个原生基线都命中 2 个。其余 14 条任务的宏平均文件召回相同。
这与 RepoOps 的设计目标一致：它在“CLI 声明 → 序列化 → 类型 → runtime 消费”的
多阶段 feature 链上更有帮助；对多数单文件 bug，原生 nanobot + 良好任务模板已经很强。

因此更准确的项目价值不是“把 nanobot 从 0 提升到 93.1%”，而是：

1. 增加受控 GitHub、固定 SHA 检索、任务状态、证据与审批等工程能力；
2. 在这组任务上改善复杂多文件 feature 的召回与无效调用率；
3. 同时付出更高上下文/token 成本，并仍有结构化收尾不稳定问题。

## GitHub MCP 的结果应该怎样理解

[GitHub MCP Server](https://github.com/github/github-mcp-server) 是官方工具服务器，不是
独立 Agent。这里测试的是“RepoOps 前的 nanobot runtime + GitHub MCP”，不能写成
“RepoOps 击败/不敌 GitHub MCP Agent”。

本轮锁定：

- release：[`v1.0.5`](https://github.com/github/github-mcp-server/releases/tag/v1.0.5)；
- commit：`c471ae94bb04059dc26e12c305e219c8fd4299e4`；
- 模式：`stdio --read-only --tools=issue_read,search_issues`；
- 归档 SHA256：`b5b939180a29414c834b038174e213c29873c678a1716068168882d7102532f1`；
- 本地代码仍使用原生 nanobot 的 `grep/find_files/list_dir/read_file`。

只开放 Issue 工具而不开放 MCP 的远程代码读取，是因为任务要求查看每题不同的 pre-fix
commit；直接读取 GitHub 当前 `HEAD` 可能看到修复后代码，造成答案泄漏。官方的
read-only 和 tool allowlist 能力见其
[配置文档](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md)。

MCP 与 `gh issue view` 原生基线的分类和 File Recall 完全相同。MCP 无效调用率更低
（1.29% vs 2.94%），但调用更多（155 vs 136）、tokens 更多（1.50M vs 1.35M）。
其 5.33% citation verification failure 集中在 `bat-issue-3002`：通用文件工具结果
没有回显完整路径，导致评分器无法把 final answer 的 source 与原始输出字符串直接关联。
这反映的是 provenance 可验证性缺口，不等同于 5.33% 的事实都语义造假。

## “RepoOps 出现前”的基线怎样保证

RepoOps 首次实现提交是 `89ba6ae1`，其父提交为：

```text
6a1a45d07a6de420ba87c419ae30fcb4af76d4d0
```

评测为这个提交建立独立 detached worktree。每条任务由外层当前评分 harness 启动一个
独立进程，并把该 worktree 放在 `PYTHONPATH` 首位；worker 只导入旧提交的
`Nanobot`、Agent loop、provider 和通用工具，不导入 `nanobot.repoops`。外层只负责
构造任务、保存标准轨迹、解析 JSON 和评分。

原生基线配置：

- 模型同为 `deepseek-v4-pro`、temperature 0.1、4096 output tokens、10 轮上限；
- Issue 通过 allowlist 限制的只读 `gh issue view` 获取；
- 代码只允许 `grep`、`find_files`、`list_dir`、`read_file`；
- 禁止通用 web、自修改、写文件和非 allowlist shell；
- 使用与 RepoOps 相同的任务、人工标签、pre-fix SHA、JSON 字段和评分器。

这不是“未经提示的 nanobot 默认聊天成绩”：为了可评分，两组基线都有任务说明、效率
约束与 JSON contract。它回答的是“同模型、同任务协议下，RepoOps 专用工具相对旧
nanobot 通用工具增加多少”，而不是“完全零配置时谁更好”。

仍有一个无法事后抹平的提示版本差异：RepoOps v4 生成时的 prompt 还没有直接携带
任务集标题和新版分类边界；两组新基线与 RepoOps v5 使用当前模板。Issue 获取工具正常
时，各系统仍会读到标题和正文，但这不是严格的单变量实验。正因如此，报告同时给出 v4
高点与 v5 当前模板重复 run，不声称 2.7pp 是具有统计显著性的纯工具因果效应。

另外，GitHub Issue/评论读取的是评测当天可见状态，不是 closing PR 合并时的历史 API
快照；模型采样不可固定 seed。后续严谨实验应冻结 Issue payload，并让各配置至少重复
3 次。

## RepoOps v5 重复 run 的失败

v5 不是挑选出来的困难子集，而是同一 15 条任务的一次完整重复运行。失败任务为：

- `cobra-issue-1933`
- `cobra-issue-2240`
- `cobra-issue-2017`
- `vitest-issue-10520`
- `bat-issue-3002`

五条都有 9–12 次工具轨迹，`run_error` 为空，`stop_reason` 为
`tool_budget_exhausted`，最终内容无法解析为 JSON。只看 v5 的 10 条成功输出，分类为
100.0%、File Recall@5 为 89.3%；全量计分则是 66.7% 和 59.6%。因此问题主要是
收尾可靠性，而不是成功答案全部失去分类能力。

报告不把 v5 与 v4 平均，也不把它静默排除。当前合理表述是：v4 是一次完整锁定结果，
v5 是重复性反证；要得到稳定结论，后续应在相同 Provider 时段做多 seed / 多次重复，
并用代码级强制 JSON finalizer 替代仅靠 prompt 的收尾。

## 可审计产物

- [原生 nanobot 汇总](../eval/runs/deepseek-v4-pro-cross-language-vanilla-nanobot-v1/run_summary.json)
- [原生 + GitHub MCP 汇总](../eval/runs/deepseek-v4-pro-cross-language-github-mcp-v1/run_summary.json)
- [RepoOps v5 重复 run 汇总](../eval/runs/deepseek-v4-pro-cross-language-repoops-v5/run_summary.json)
- [v4 高点四方机器对照](../eval/runs/deepseek-v4-pro-cross-language-comparison-v3.json)
- [v5 重复 run 四方机器对照](../eval/runs/deepseek-v4-pro-cross-language-comparison-v4.json)
- [GitHub MCP 版本锁](../eval/github_mcp_server.lock.json)
- [原生 nanobot 配置](../eval/deepseek_vanilla_nanobot_config.example.json)
- [GitHub MCP 配置](../eval/deepseek_github_mcp_config.example.json)

三个新 run 的 `trajectories/` 都保存脱敏工具参数、结果、最终输出、耗时和 usage；密钥
只通过环境变量注入。评测结束后检查了 45 个 pre-fix worktree，tracked 文件变更为 0。

## 复现命令

先准备 RepoOps 前的 runtime 与凭据；GitHub MCP binary 应下载 `v1.0.5` 对应平台资产
并按版本锁校验 SHA256：

```bash
git worktree add --detach /tmp/nanobot-pre-repoops \
  6a1a45d07a6de420ba87c419ae30fcb4af76d4d0

export REPOOPS_DEEPSEEK_AUTH_TOKEN='...'
export GITHUB_MCP_TOKEN='...'
export GITHUB_MCP_SERVER='/absolute/path/to/github-mcp-server'
```

原生 nanobot：

```bash
uv run python -m nanobot.repoops.cross_language_benchmark \
  --tasks eval/cross_language_tasks.json \
  --agent vanilla-nanobot \
  --config eval/deepseek_vanilla_nanobot_config.example.json \
  --runtime-root /tmp/nanobot-pre-repoops \
  --repository-cache /tmp/repoops-crosslang/sources-vanilla \
  --worktree-root /tmp/repoops-crosslang/worktrees-vanilla \
  --output-dir eval/runs/deepseek-v4-pro-cross-language-vanilla-nanobot-v1 \
  --jobs 3
```

官方 GitHub MCP：

```bash
uv run python -m nanobot.repoops.cross_language_benchmark \
  --tasks eval/cross_language_tasks.json \
  --agent github-mcp \
  --config eval/deepseek_github_mcp_config.example.json \
  --runtime-root /tmp/nanobot-pre-repoops \
  --repository-cache /tmp/repoops-crosslang/sources-mcp \
  --worktree-root /tmp/repoops-crosslang/worktrees-mcp \
  --output-dir eval/runs/deepseek-v4-pro-cross-language-github-mcp-v1 \
  --jobs 3
```

`cross_language_report` 的 `--vanilla-run-dir` 与 `--github-mcp-run-dir` 会把两组加入
统一机器报告。不要把真实 token 写进示例配置或 trajectory。

## 面试建议

可以说：

> 我用 RepoOps 引入前的真实 nanobot 父提交和官方 GitHub MCP 做了消融。原生
> nanobot 在 15 条上已经有 90.4% File Recall@5，RepoOps 最佳完整 run 是 93.1%，
> 增益集中在多阶段配置链；RepoOps 的核心价值还包括固定 SHA、证据状态和写操作审批，
> 不是只靠 accuracy。重复 run 又暴露出 5 条结构化收尾失败，所以我没有把一次 100%
> 包装成稳定生产准确率。

不要说：

- “RepoOps 比原生 nanobot 提升了 16 个百分点”——16.0pp 是相对 Claude Code；
- “GitHub MCP 是一个被 RepoOps 击败的 Agent”——它只是工具服务器；
- “生产准确率 100%”——样本小、只覆盖 3 个仓库，且 v5 已证明重复性不足。
