# RepoOps 评估报告

评估日期：2026-07-31。

## 结论

RepoOps 已从“有确定性单测的工程 MVP”推进到“能在真实公开仓库上由真实模型自主
调用工具并保存完整可观察轨迹”的版本。正式结果严格区分：

- 人工准备的 Issue/PR/CI ground truth；
- RepoOps Agent + DeepSeek V4 Pro 产生的预测；
- 确定性 Python 评分器计算的指标。

`gh` 只用于冻结历史样本和读取 Actions job log，不计入 Agent 工具轨迹，也没有
用人工答案替换模型输出。

## 数据与协议

### 20 条 Issue 基线

- 仓库：`HKUDS/nanobot`
- 快照：`6a1a45d07a6de420ba87c419ae30fcb4af76d4d0`
- 模型：`deepseek-v4-pro`
- Provider：DeepSeek OpenAI-compatible Chat Completions
- 任务：12 bug、6 feature、1 performance、1 security
- 标签：每个真实历史 Issue 对应的合并 PR；人工筛选最接近根因/需求入口的生产文件
- 温度：0.1
- 模型每轮最大输出：4096 tokens
- Agent 最大工具迭代：10；prompt 另设 8 次 Issue 工具调用的效率目标

### 9 条面试演示

- 3 Issue：#5133、#5159、#5118
- 3 PR：#5136、#5160、#5157
- 3 CI failure：
  - run 30435121783 / job 90520996796
  - run 30346044623 / job 90232784365
  - run 29994514347 / job 89164884335

演示 ground truth 保存在 `eval/demo_ground_truth.json`，不会拼进 prompt。
Issue/PR 采用与正式基线相同的 4k 单轮输出和 10 轮上限；CI 的日志与上下文更长，
采用 8k 单轮输出和 12 轮上限。
CI 配置把 nanobot 的内联结果阈值设为 18k，而 RepoOps 工具仍以 16k 截断，避免结果
因安全标头略超默认 16k 后被落盘，同时不需要开放通用 file/exec 工具。

最终 9/9 条演示均生成可解析报告，共 78 次工具调用、906,664 provider tokens。
3 条 CI 分别用 6、5、8 次工具调用完成，目标 job、首个因果错误与人工 ground truth
一致，且这 3 条 CI 没有无效工具调用。完整 9 条演示里，Issue/PR 分析有 8 次误用
未注册 `read_file` 的失败调用；它们不影响最终 JSON 可解析，但不会被隐藏。汇总见
`eval/runs/deepseek-v4-pro-demo/run_summary.json`。

### 隔离

每次 benchmark invocation：

1. 使用全新的 `sdk:repoops-benchmark:<namespace>:<case>` session key；
2. 使用独立 `.repoops/benchmark/<namespace>` task state；`.repoops` 被索引排除；
3. 只注册 `repoops_*` 工具；尝试其他名称会失败并留在轨迹中；
4. workspace 固定为 detached worktree；
5. API key 和 GitHub token 只从环境变量读取；
6. 轨迹不保存隐藏 reasoning。

这两个 namespace 是真实 smoke 后补上的必要修复。只设置 `ephemeral=True` 仍可能
读到同一 session 的历史；复用 RepoOps task state 也会让下一轮提前看到旧事实。

## 正式结果

<!-- EVAL_RESULTS_START -->
20 条隔离基线全部完成：17/20 生成可解析的最终 JSON，3/20 在工具迭代上限处
留下 DSML 工具调用文本，按无效输出计分。

| 指标 | 结果 |
|---|---:|
| Classification Accuracy | 0.800 |
| File Recall@5 | 0.753 |
| Tool Precision | 0.712 |
| Tool Recall | 0.960 |
| Invalid Call Rate | 0.095 |
| Duplicate Call Rate | 0.000 |
| Evidence Completeness | 0.991 |
| Hallucinated Citation Rate | 0.000 |
| Approval Gate Accuracy | 1.000 |
| Average Steps | 11.1 |
<!-- EVAL_RESULTS_END -->

本轮共记录 222 次工具调用、2,843,770 provider tokens。无效输出是 #4420、#4924
和 #5041；此外 #3768 的人工标签是 security，模型分类为 feature。没有补写答案、
删除失败样本或用 ground truth 修正模型输出。汇总以
`eval/runs/deepseek-v4-pro-baseline/metrics.json` 为唯一数字来源，逐任务状态与 usage
见 `run_summary.json`。

Invalid Call Rate 的分子是 21：19 次误用未注册 `read_file`、1 次误用未注册
`exec`、1 次 `repoops_read_file` 漏传 `repository`。分母是 trajectory 中全部
222 次工具调用，因此为 9.46%。最终答案不可解析属于 output failure，不伪装成
invalid tool call；3 条无效输出已经通过分类、文件召回、成功率等指标进入分母。

## 指标定义

`nanobot.repoops.evaluation` 实现：

| 指标 | 计算 |
|---|---|
| Classification Accuracy | category 与人工标签一致的任务比例 |
| File Recall@5 | 每任务前 5 文件覆盖 relevant files 的比例，再宏平均 |
| Tool Precision | 调用的不同工具中属于工作流必要集合的比例 |
| Tool Recall | 工作流必要工具被覆盖的比例 |
| Invalid Call Rate | 参数/schema/工具名无效调用占全部调用比例 |
| Duplicate Call Rate | 重复 `(tool, arguments_digest)` 占工具调用比例 |
| Evidence Completeness | 带 evidence ID 的事实/假设占全部证据性 claim 比例 |
| Hallucinated Citation Rate | ID 不存在或 source/excerpt 无法追溯到工具结果的引用比例 |
| Approval Gate Accuracy | read-only 任务是否正确返回无需批准 |
| Average Steps | 每任务工具调用数 |

citation 验证是保守的字符串来源检查：要求 source 出现在工具输出中，excerpt 精确
匹配或 distinctive token overlap 达 60%。它能抓住“凭空写文件/URL”，不能替代
语义事实核验。

## 真实 Bad Cases

### 1. 过度检索

未加效率约束的 Issue #5133 首轮：

- 25 次工具调用；
- 271 秒；
- 反复平移读取 `runner.py` 行号窗口；
- 总 provider tokens 358,712，其中 305,024 cached。

加入“8 次调用、5 个批次、最多 2 个精确文件读取”的 prompt 预算并隔离状态后：

- 8 次工具调用；
- 123 秒；
- Classification 与 File Recall@5 仍命中；
- 总 provider tokens 105,547，其中 76,416 cached。

这说明模型能响应效率约束，但 prompt 不是硬预算；部分正式样本仍超过 8 次调用。
后续应在 runner/tool scheduler 增加代码级 read-only 调用预算，而不是只靠提示。

### 2. 会话/状态污染

第二次 smoke 复用了 session key，模型 0 次工具调用便复述上一轮最终答案。它不是
“模型更快”，而是 benchmark 污染。runner 现已为 session 和 RepoOps state 同时
生成 invocation namespace，回归测试覆盖 JSON/轨迹评分逻辑，污染 run 不进入正式
指标。

### 3. 迭代上限没有结构化收尾

#4420、#4924 和 #5041 已取得工具证据，但第 10 轮仍请求工具；runtime 到达上限后
把 DSML tool-call 文本作为最终内容返回，评分器无法提取 category/files。三条均按
无效输出进入分母。这暴露了两层后续工作：在代码层实施 read-only 调用预算，并在
正常工具循环结束后增加一个禁用工具的结构化收尾轮。

## 安全评估

已通过的确定性边界：

- 空仓库 allowlist 默认拒绝；
- GitHub API 相对路径不能逃逸配置 origin；
- 每个请求与日志 redirect 都通过 SSRF 校验；
- Actions 日志只允许 GitHub 签名下载域，压缩与展开均有大小上限；
- Issue/PR/CI 中的批准口令不能触发写入；
- 同轮批准、跨 session 批准和草稿重放被拒绝；
- `stateDir` 不能逃逸 workspace；
- 网络结果不确定时不自动重试高风险写操作。

正式 baseline 配置关闭 shell、通用 file、web、CLI app 和 self-modification 工具，
runner 再移除所有非 `repoops_*` 工具。

## 工程验证

<!-- TEST_RESULTS_START -->
| 检查 | 结果 |
|---|---|
| RepoOps 专项 pytest | 28 passed |
| RepoOps Ruff | passed |
| RepoOps strict BasedPyright | 0 errors / 0 warnings |
| Python 全量测试 | 5991 passed、11 skipped、0 failed |
| WebUI tests/build/lint | 896 passed（10s test timeout）、build/lint passed |
| Python package | sdist/wheel passed |
<!-- TEST_RESULTS_END -->

WebUI 在 Node 26 的默认 5 秒上限下首轮为 895 passed、1 timeout；超时用例单独重跑
3.7 秒通过，完整套件改用 10 秒上限后 896/896 通过。这个结果按 flaky timeout
披露，不写成默认命令一次性通过。

## 局限

1. 20 条任务来自一个 Python 仓库，且只跑一个模型配置和一次采样；不能证明跨语言
   或统计稳定性。
2. GitHub Issue/PR files/comments 当前最多取 100 条，没有自动分页。
3. 本地 trigram 是轻量近似，不是 dense embedding。
4. citation 字符串可追溯不等于 claim 在语义上完全正确。
5. 模型可能不严格遵守 prompt 工具预算；平均步骤和 token usage 必须如实披露。
6. Actions 历史日志可能被 GitHub 过期清理，复跑 CI demo 依赖外部保留策略。
7. 未在用户测试仓库执行真实 GitHub 写操作；写安全由 MockTransport、状态机与审批
   回归测试验证。
8. nanobot 的通用 tool contract 会提到 `read_file`/`exec`；DeepSeek 在 RepoOps-only
   评测中仍可能尝试这些未注册工具。当前安全边界会拒绝并记录，但效率仍受影响。

## 复现

```bash
uv run python -m nanobot.repoops.benchmark \
  --tasks eval/repoops_tasks.json \
  --config eval/deepseek_config.example.json \
  --workspace /tmp/repoops-nanobot-eval \
  --output-dir eval/runs/deepseek-v4-pro-baseline
```

完整数据说明见 [eval/DATASET.md](eval/DATASET.md)，原始 trajectory、prediction、
metrics 和 run summary 均保存在 `eval/runs/`。
