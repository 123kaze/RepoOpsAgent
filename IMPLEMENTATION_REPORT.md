# RepoOps 实现记录

## 目标与边界

本次工作把 nanobot 特化为 RepoOps，同时遵守“核心保持小、能力从边缘扩展”的
约束。`agent/loop.py`、`agent/runner.py`、消息总线、Provider、会话、Cron、
Gateway 和 WebUI 数据流均未加入 RepoOps 分支。

## 已完成

### 1. GitHub 与代码工具

在 `nanobot/agent/tools/repoops.py` 增加 15 个工具：

1. `repoops_list_issues`
2. `repoops_get_issue`
3. `repoops_search_issues`
4. `repoops_search_code`
5. `repoops_read_file`
6. `repoops_get_pull_request`
7. `repoops_get_pull_request_diff`
8. `repoops_get_ci_status`
9. `repoops_get_ci_failure_logs`
10. `repoops_search_workspace`
11. `repoops_get_task_state`
12. `repoops_update_task_state`
13. `repoops_daily_digest`
14. `repoops_create_draft`
15. `repoops_execute_draft`

工具通过 nanobot 的自动发现和 `ToolRegistry` 注册，无需修改 Runner。

### 2. GitHub API 与安全

- 固定相对 API 路径并限制在配置的 HTTPS origin。
- 每个请求经过 nanobot 共享 SSRF guard。
- Actions 日志 302 只允许 GitHub 签名下载域名，并逐跳重验。
- 下载压缩包与展开内容都有大小上限。
- GitHub 输出统一标记为不可信外部内容。
- 仓库 allowlist 默认拒绝全部。

GitHub REST 契约按 2026-03-10 版本头调用；Issue、PR 与 Actions 端点按 GitHub
官方文档核对。

### 3. 结构化任务状态

新增 `RepoTaskState`、`Evidence`、`Hypothesis` 和 `ToolRecord`。每个任务独立保存：

- 已确认事实
- 缺失信息
- 相关文件与 Issue
- 带置信度和证伪方式的假设
- claim-to-source 证据
- 工具轨迹与下一步
- 是否需要人工批准

状态存储在活动工作区 `.repoops/` 内，使用临时文件、`fsync` 和原子替换。

### 4. 人工审批

写操作分成草稿和执行两个工具。执行条件同时包括：

- 草稿仍为 pending；
- 仓库仍在 allowlist；
- 同一个 session；
- 不同 turn；
- 原始用户文本中存在独立一行的精确批准口令；
- 已配置 GitHub token。

执行前原子抢占为 `executing`，成功后标记 `executed`。遇到不确定的网络失败时
不自动退回 pending，避免重复 GitHub 写入。

### 5. RAG / 检索

- Python 顶层函数与类的 AST 分块
- 覆盖模块级代码的重叠行窗口
- BM25 关键词检索
- 本地 trigram 相似度
- 精确符号/路径 boost
- 依赖、构建产物、`.git`、`.repoops`、`.nanobot` 和 `sessions` 排除

该实现可离线复现，不依赖外部 embedding。它是“符号优先混合检索”MVP，不冒充
已经完成外部 dense embedding 或 cross-encoder。

### 6. Skill 与自动化

- always-on `repoops` 总体策略
- `repoops-issue-analysis`
- `repoops-pr-review`
- `repoops-ci-diagnosis`
- 可由 nanobot Cron 调用的只读日报工具

### 7. 评测

原来的 20 条自造离线任务已全部替换为 `HKUDS/nanobot` 的真实历史 Issue。每条
任务包含公开 Issue URL、合并 PR、固定 commit SHA 与人工筛选的核心生产文件。

新增：

- `nanobot.repoops.benchmark`：通过 `Nanobot.from_config().run_streamed()` 启动
  RepoOps Agent，记录工具名、脱敏参数、状态、完整有界结果、hash、耗时、最终
  JSON 和 usage，不保存隐藏 reasoning；
- `nanobot.repoops.benchmark_merge`：合并并行 shards 并重新计算确定性指标；
- `eval/demo_cases.json`：3 Issue、3 PR、3 个真实失败 Actions run；
- `eval/demo_ground_truth.json`：与 Agent prompt 分离的人工标准答案；
- `eval/deepseek_config.example.json`：不含明文 key 的 DeepSeek V4 Pro 配置；
- invocation 级 session namespace 与 state directory 隔离，避免旧答案污染；
- 对常见模型 JSON 小错误做可重复 repair，再用 Pydantic schema 校验。
- 参数按敏感键脱敏；非结构化工具输出、错误与最终答案额外过滤常见 API key、
  GitHub token 和 Bearer credential，并对脱敏后的结果重新计算 hash。

10 项指标覆盖分类、File Recall@5、工具准确性、无效/重复调用、证据完整率、引用
可追溯失败率、审批命中率和平均步骤数。citation 只有在工具结果中能找到 source
且 excerpt 达到保守 token overlap 时才算受支持。
无效工具调用只统计真实 tool error/参数错误，不把“最终答案不可解析”混进
Invalid Call Rate；正式基线纠正后为 21/222（9.5%）。

首次真实 smoke 揭示了 25 步过度检索问题；加入 8 次工具调用/5 批次的 prompt
预算并隔离会话后，同题降为 8 步。优化前后轨迹均保留在 `eval/runs/`。

正式 DeepSeek V4 Pro 基线完成 20/20 条执行，17 条得到可解析 JSON；分类准确率
80.0%、File Recall@5 75.3%、平均 11.1 步。3 条达到迭代上限后未完成结构化收尾，
均作为失败进入正式指标。

面试演示集完成 3 Issue、3 PR、3 个指定 CI job，9/9 得到可解析报告，共 78 次
RepoOps 工具调用。CI 摘要器改为优先 timeout、pytest、Ruff rule 等强因果信号，
避免 setup 日志中的 `ErrorActionPreference` 抢占输出预算。

## 文档与清理

删除了 nanobot 上游的多渠道使用教程、Provider 大全、发布历史、营销封面和截图、
远程安装脚本及旧协作说明，共约 1.4 万行。保留：

- MIT `LICENSE`
- `THIRD_PARTY_NOTICES.md`
- `.agent` 设计/安全约束
- nanobot 运行时源码、测试与 WebUI
- 必要的构建和频道依赖安装脚本

新增/重写 README、架构、配置、安全、贡献、源码学习、Bad Cases、实现和评测文档。

## 验证记录

- RepoOps 专项测试：28 passed
- Python 全仓库测试：5991 passed、11 skipped、0 failed
- Ruff（`nanobot/`、`tests/`、`scripts/`、`conftest.py`）：通过
- BasedPyright 全仓库严格检查：0 errors、0 warnings、0 notes
- WebUI 测试：896 passed、0 failed（Node 26 使用 10 秒 test timeout；默认 5 秒
  首轮有 1 个 flaky timeout，单测重跑通过）
- WebUI TypeScript 生产构建与 ESLint：通过
- Python sdist/wheel：构建成功，产物包含 RepoOps、Skill 和 WebUI
- 20 条真实历史 Issue：加载成功且 task ID 唯一
- `repoops` CLI entry point：`🛠️ RepoOps v0.3.0`

WebUI 所在机器使用 Node 26；完整测试通过时禁用了 Node 的实验性 WebStorage，
避免它与 happy-dom 的 `localStorage` 冲突。
完整命令和残余风险见 [评测报告](EVALUATION_REPORT.md)。
