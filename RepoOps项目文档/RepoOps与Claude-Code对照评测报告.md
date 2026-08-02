# RepoOps Agent 与 Claude Code 对照评测

评测日期：2026-08-01。

## 一句话结论

在同一 `deepseek-v4-pro` 主模型、同一批 15 条逐任务 pre-fix 跨语言 Issue 上，
RepoOps v4 达到 15/15 结构化成功、100.0% 分类和 93.1% File Recall@5；Claude Code
为 13/15、86.7% 和 77.1%。RepoOps 使用 137 次调查调用，Claude Code 使用 220 次。

这是一轮有审计轨迹的系统对照，说明 RepoOps 的领域检索、状态与输出约束在这批任务
上有效；它不是对所有仓库或所有代码 Agent 的普适排名。

后续原生消融补充了重要边界：RepoOps 前 nanobot 已达到 15/15、100.0% 和 90.4%，
而 RepoOps 同日重复 run 只有 10/15。所以下文的 Claude 对照数字仍是真实锁定结果，
但不能单独代表 RepoOps 相对底座的增量或重复运行稳定性；见
[原生 nanobot 与 GitHub MCP 对照报告](原生Nanobot与GitHub-MCP对照评测报告.md)。

## 锁定结果：跨语言 pre-fix 集

| 指标 | RepoOps v4 | Claude Code v1 | 差值 |
|---|---:|---:|---:|
| Structured Success | **15/15** | 13/15 | +13.3 pp |
| Classification Accuracy | **100.0%** | 86.7% | +13.3 pp |
| File Recall@5 | **93.1%** | 77.1% | +16.0 pp |
| 调查工具调用 | **137** | 220 | -37.7% |
| Invalid Call Rate | **0.73%** | 5.45% | -4.72 pp |
| Evidence Completeness | 98.0% | **100.0%** | -2.0 pp |
| Hallucinated Citation Rate | **0%** | 4.46% | -4.46 pp |
| Runner-reported tokens | **1,881,777** | 2,424,549 | -22.4% |
| 累计逐题耗时 | **1,886.8 秒** | 2,066.4 秒 | -8.7% |

RepoOps 不是每个子项都更优：Go 和 Rust 上 Claude 的 token 更少，Rust 的平均耗时也
更低；Claude 的 Evidence Completeness 为 100%，RepoOps 为 98.0%。总体差距来自
RepoOps 在 Go 上多完成两条、在 TypeScript 上取得更高文件召回，并减少无效及冗余
调查。

原始结果：

- [RepoOps v4 汇总](../eval/runs/deepseek-v4-pro-cross-language-repoops-v4/run_summary.json)
- [Claude Code v1 汇总](../eval/runs/deepseek-v4-pro-cross-language-claude-code-v1/run_summary.json)
- [机器可读对照](../eval/runs/deepseek-v4-pro-cross-language-comparison-v2.json)
- [完整跨语言报告](跨语言多仓库对照评测报告.md)

## 历史 Python 单仓结果

20 条 `HKUDS/nanobot` 统一快照任务用于早期基线和失败分析。正式、无 state 污染的
RepoOps v3 与 Claude Code 对照为：

| 指标 | RepoOps v3 | Claude Code |
|---|---:|---:|
| Structured Success | **18/20** | 15/20 |
| Classification Accuracy（全量） | **85.0%** | 70.0% |
| File Recall@5（全量） | **77.0%** | 65.0% |
| 调查工具调用 | **197** | 340 |
| Runner-reported tokens | **3,014,807** | 4,091,916 |

只看成功输出，两边分类为 94.4% 与 93.3%，File Recall 为 85.6% 与 86.7%。因此这组
旧结果的主要差距是结构化收尾可靠性和工具效率，而不是成功样本“更聪明”。它使用
统一 post-fix 快照，部分人工文件标签存在路径漂移，所以跨语言逐任务 pre-fix v4
是当前更适合简历的主结论。

Python v5 验证了紧凑收尾可将结构化成功提高到 20/20，但同时暴露出网络阻断时 prompt
遗漏 Issue 标题的问题，导致分类降到 70.0%、File Recall 降到 73.7%。修复后对 6 条
分类错误做目标回归为 6/6 正确；由于这不是完整重跑，不把它包装成新的全量准确率。

## Claude Code 是怎么测试的

测试不是让 Claude Code “评价 RepoOps”，而是让它作为独立 Agent 解同一批题：

1. 每条 Issue 创建与 RepoOps SHA 相同、物理隔离的 detached worktree；
2. 每题启动一个本机 Claude Code 2.1.220 无交互进程；
3. 使用 `--bare --no-session-persistence`，关闭项目记忆、插件同步和 MCP；
4. 主模型指定为 `deepseek-v4-pro`，仅开放 Bash/Read，只允许只读搜索；
5. 用相同 BenchmarkAnswer JSON 字段收集 category、files、facts、hypotheses 和
   citations；
6. 解析 `stream-json`，保存可观察工具参数/结果、错误、耗时和 usage；
7. 排除只用于提交 schema 的 `StructuredOutput`，只统计调查调用；
8. 用 RepoOps 相同的确定性评分器评分，失败不补答案、不重抽。

核心入口是 `nanobot/repoops/claude_benchmark.py`，多仓库编排是
`nanobot/repoops/cross_language_benchmark.py`。完整命令和工具归一化方法见
[Claude Code 对照测试方法](Claude-Code对照测试方法.md)。

## 公平性边界

- 对照的是两个 Agent 系统，不是 DeepSeek 与 Claude 两个模型；两边主模型都指定为
  `deepseek-v4-pro`。
- 两边任务、SHA、超时、最终字段和评分器相同，但系统提示、工具协议和结构化输出
  实现不同；这正是要比较的工程差异。
- Claude Code 使用通用 Bash/Read，RepoOps 使用 15 个领域工具。Tool
  Precision/Recall 因工具词表不同，不作为跨系统 headline。
- Claude init/result 记录主模型为 `deepseek-v4-pro`；独立诊断 smoke 曾在
  `modelUsage` 观察到 `deepseek-v4-flash` 辅助用量，因此不声称其内部绝对只发生
  单一模型调用。
- 当前共覆盖 Python、Go、TypeScript、Rust 四个仓库；跨语言集每仓只有 5 条，只做
  一次采样，不能外推为生产准确率。
- GitHub Issue 当前公开评论可能包含事后信息；两边代码固定在 pre-fix commit，但
  Issue 评论没有历史时间切片。

## 无效结果审计

以下 run 不进入正式指标：

- Python v2：20/20，但读取了旧 baseline 的 task state；
- 跨语言 unpinned run：远程读取可能看到 post-fix `HEAD`；
- 跨语言 pinned-state run：钉住 SHA，但复用旧 task state；
- Python v6：未完成的单次紧凑收尾压力实验；
- Python v7：按用户要求中止，仅 18/20 shard 完成，不能形成全量指标。

失败或污染结果均保留在 `eval/runs/`，目录名或本文明确标注 invalid/incomplete，避免
把后验挑选的最好数字当成可复现结论。

跨语言 RepoOps v5 是完整重复 run，不属于 invalid/incomplete：10/15 结构化成功。
它作为重复性反证保留，不能因结果较差而从面试口径中省略。

## 简历和面试口径

简历建议写：

> 构建 Python / Go / TypeScript / Rust 四仓库真实历史 Issue 评测集；用 DeepSeek
> V4 Pro 在 15 条逐任务 pre-fix 跨语言任务上取得 15/15 结构化成功、100.0% 分类和
> 93.1% File Recall@5，同主模型 Claude Code 为 13/15、86.7% 和 77.1%，调查调用
> 减少 37.7%；另以 RepoOps 前原生 nanobot 和官方 GitHub MCP 做消融，并完整保留
> 失败、污染、重复和中止 run 审计。

面试时不要说“全面击败 Claude Code”。更准确的回答是：

> 我控制了主模型、任务、commit 和评分器，比较的是领域 Agent 与通用代码 Agent 的
> 系统差异。RepoOps 在这 15 条任务上成功率、文件召回和调用效率都更好，但样本量小、
> 每个语言只有一个仓库；原生 nanobot 已有 90.4% File Recall，RepoOps 重复 run 也
> 暴露了收尾失败，所以我把它当工程证据，不外推成通用排行榜或稳定 SLA。
