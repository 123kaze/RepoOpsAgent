# Claude Code 对照测试方法

## 测试目的

这次不是比较 DeepSeek 和 Claude 两个模型，而是让 **RepoOps Agent** 与本机
**Claude Code** 使用同一个 `deepseek-v4-pro` 主模型，完成同一批真实 Issue，观察
领域 Agent 工程对成功率、工具效率和可追溯性的影响。当前 headline 来自 15 条
Go/TypeScript/Rust 逐任务 pre-fix 对照；20 条 Python 统一快照结果作为历史补充。

## 控制变量

两边共同使用：

- 同一批 20 条 `HKUDS/nanobot` 历史 Issue；
- 同一个固定代码快照 `6a1a45d07a6de420ba87c419ae30fcb4af76d4d0`；
- 同一个 DeepSeek Anthropic-compatible endpoint；
- 主模型 `deepseek-v4-pro`；
- 同一套最终答案字段和确定性评分器；
- 每题独立上下文，失败样本不重抽、不人工补答案；
- 每题最长 600 秒。

人工标签只在运行完成后参与评分，不会放进 prompt。API 密钥只通过本机设置和环境
变量注入，轨迹保存前会脱敏。

## Claude Code 运行方式

使用本机 Claude Code 2.1.220 的无交互模式。核心参数为：

```text
claude -p --bare
  --settings ~/.claude/settings.json
  --model deepseek-v4-pro
  --effort max
  --tools Bash,Read
  --permission-mode dontAsk
  --strict-mcp-config
  --mcp-config '{"mcpServers":{}}'
  --disable-slash-commands
  --no-session-persistence
  --output-format stream-json
  --json-schema <BenchmarkAnswer schema>
```

`--bare`、空 MCP 配置和禁用 session persistence 用于减少项目记忆、插件和历史会话
带来的污染。Claude Code 只使用 Bash/Read 调查面；prompt 禁止编辑、删除、Git 写入
和子 Agent，并将调查预算设为最多 8 次工具调用、最多读取 3 个精确文件范围。

Issue 通过只读命令获取：

```bash
gh issue view <number> --repo HKUDS/nanobot \
  --json number,title,body,state,labels,comments,url
```

本地代码只允许使用 Read 和 `rg`/`grep`/`find` 一类搜索。运行前后检查 detached
worktree 的 tracked 状态，正式测试中没有代码写入。

## 测试 Harness 如何工作

实现位于 [`nanobot/repoops/claude_benchmark.py`](../nanobot/repoops/claude_benchmark.py)：

1. 为每个 Issue 单独启动一次 Claude Code 子进程；
2. 通过 `stream-json` 读取 init、assistant、tool result 和 final result 事件；
3. 保存可观察的工具名、参数、结果、错误、耗时、最终答案和 token usage；
4. 排除只负责提交 JSON Schema 的 `StructuredOutput` 调用，不把它算作调查工具；
5. 不保存隐藏 reasoning；
6. 将结果写入 `trajectories/*.json`，再生成 `predictions.json`、`metrics.json` 和
   `run_summary.json`。

为了复用同一个评分器，Claude Code 工具会做最小语义归一化：

| Claude Code 行为 | 统一工具类别 |
|---|---|
| `Read` | `repoops_read_file` |
| `gh issue view` | `repoops_get_issue` |
| `gh issue list` / `gh search issues` | `repoops_search_issues` |
| `rg` / `grep` / `find` / `git grep` | `repoops_search_workspace` |

Tool Precision/Recall 没有作为跨系统主结论，因为 RepoOps 有专用 task-state 工具，
Claude Code 没有等价能力，直接比较会系统性惩罚 Claude Code。

## 实际执行

为缩短墙钟时间，20 条任务被拆成两个 shard 并行执行。每条任务仍是独立进程和独立
上下文，最后由 `nanobot.repoops.benchmark_merge` 合并；合并只汇总已有轨迹并重新
评分，不改变答案。

单次完整复现命令：

```bash
uv run python -m nanobot.repoops.claude_benchmark \
  --tasks eval/repoops_tasks.json \
  --workspace /absolute/path/to/nanobot-snapshot \
  --settings ~/.claude/settings.json \
  --model deepseek-v4-pro \
  --effort max \
  --output-dir eval/runs/deepseek-v4-pro-claude-code-v1
```

Claude Code 的 DeepSeek endpoint 和凭据来自 `~/.claude/settings.json`，报告与命令
不会记录密钥内容。

### 跨语言扩展如何执行

新增的 `nanobot.repoops.cross_language_benchmark` 读取
`eval/cross_language_tasks.json`，按 `repository + snapshot_sha` 为每题创建独立
detached worktree，再启动原有 Claude harness。Cobra、Vitest、bat 各 5 条，15 条
使用 15 个不同的 closing-PR pre-fix commit。

```bash
uv run python -m nanobot.repoops.cross_language_benchmark \
  --tasks eval/cross_language_tasks.json \
  --agent claude-code \
  --settings ~/.claude/settings.json \
  --model deepseek-v4-pro \
  --effort max \
  --repository-cache /tmp/repoops-crosslang/sources-claude \
  --worktree-root /tmp/repoops-crosslang/worktrees-claude \
  --output-dir eval/runs/deepseek-v4-pro-cross-language-claude-code-v1 \
  --jobs 2
```

RepoOps 使用另一套物理 worktree，但每题 commit SHA 与 Claude 完全相同。两边都以
2 个并发 case 运行；并发不共享模型上下文。每个 Claude 进程前后检查 tracked Git
状态，正式测试没有修改被测仓库代码。

## 最终结果

当前正式跨语言结果：

| 指标 | RepoOps v4 | Claude Code |
|---|---:|---:|
| 结构化成功 | **15/15** | 13/15 |
| 全量分类准确率 | **100.0%** | 86.7% |
| 全量 File Recall@5 | **93.1%** | 77.1% |
| 调查工具调用 | **137** | 220 |
| 无效调用率 | **0.73%** | 5.45% |
| Runner-reported tokens | **1,881,777** | 2,424,549 |

相对 Claude，RepoOps 的调查调用少 37.7%、tokens 少 22.4%。Claude 的两条失败均为
`error_max_structured_output_retries`；RepoOps 15 条全部输出可评分 JSON。完整分语言
和失败分析见[跨语言多仓库对照评测报告](跨语言多仓库对照评测报告.md)。

历史 Python 单仓结果：

| 指标 | RepoOps v3 | Claude Code |
|---|---:|---:|
| 结构化成功 | **18/20** | 15/20 |
| 全量分类准确率 | **85.0%** | 70.0% |
| 成功样本分类准确率 | **94.4%** | 93.3% |
| 全量 File Recall@5 | **77.0%** | 65.0% |
| 成功样本 File Recall@5 | 85.6% | **86.7%** |
| 调查工具调用 | **197** | 340 |
| 无效调用率 | 1.52% | **0.59%** |
| Runner-reported tokens | **3,014,807** | 4,091,916 |

Claude Code 的 5 条失败中，1 条连接中断，4 条达到结构化输出重试上限；RepoOps v3
有 1 条 Provider 参数解析失败和 1 条迭代上限收尾失败。只看成功样本，两边分类与
File Recall 接近；RepoOps 的主要优势是全量成功率高 15 个百分点，并减少 42.1%
的调查工具调用。此前 20/20 的 RepoOps v2 因读取旧 state 已撤回。

Python v3 的 20 条结果继续作为统一快照历史基线，不与当前跨语言 v4 混算。后续
Python v5 虽达到 20/20 结构化成功，但因网络阻断时 prompt 遗漏 Issue 标题而出现
分类漂移；修复后的 6/6 目标回归不能替代完整重跑，故不作为 headline。

## 公平性边界

- 这是两个 Agent 系统使用同一主模型的工程对照，不是隔离所有变量后的纯模型实验。
- 两者系统提示、工具协议、结构化输出实现和迭代策略不同。
- Claude Code 使用通用 Bash/Read，RepoOps 使用 15 个领域工具；这正是被评估的架构
  差异。
- Claude Code init/result 记录的主模型是 `deepseek-v4-pro`；单独诊断 smoke 的
  `modelUsage` 曾出现 `deepseek-v4-flash` 辅助用量，因此不声称其内部绝对只有一次
  单模型调用。
- 当前覆盖 4 个仓库和 4 种主要语言，但跨语言集每仓只有 5 条且只有一次采样，不能
  外推成通用代码 Agent 排名。

完整逐题结果和失败样本见
[RepoOps 与 Claude Code 对照评测报告](RepoOps与Claude-Code对照评测报告.md)。
