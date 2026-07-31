# RepoOps 真实历史任务集

本目录把“模型输入”“人工标准答案”“Agent 运行结果”分开保存，避免把人工检索
伪装成 Agent 能力。

## 数据

- `repoops_tasks.json`：20 个来自 `HKUDS/nanobot` 的真实历史 Issue。
- `demo_cases.json`：3 个 Issue、3 个 PR、3 个失败 CI run 的面试演示集。
- `demo_ground_truth.json`：演示集的人工标签，**不会**进入 Agent prompt。
- `deepseek_config.example.json`：只引用环境变量的 DeepSeek V4 Pro 配置。
- `deepseek_demo_config.example.json`：CI 演示使用 8k 单轮输出、12 轮上限，并把
  runtime 内联结果阈值设为 18k；RepoOps 工具自身仍受 16k 输出硬上限约束。
- `runs/`：实跑后生成的轨迹、预测和指标；每次运行使用独立子目录。

所有 20 个 Issue 都链接到一个合并 PR。`relevant_files` 只保留人工判断最接近
根因/需求入口的生产文件，不把所有改动文件机械地当作相关文件；若 PR 合并后文件
发生重构/迁移，则标注固定快照中的当前路径（例如 Telegram 的
`nanobot/channels/telegram/runtime.py`）。快照固定为：

```text
repository: HKUDS/nanobot
commit: 6a1a45d07a6de420ba87c419ae30fcb4af76d4d0
```

## 运行协议

1. 检出上述 commit，并把它作为 Agent workspace。
2. 设置 `DEEPSEEK_API_KEY`；如需更高 GitHub API 配额，再设置 `GITHUB_TOKEN`。
3. 配置只注册 `repoops_*` 工具；runner 还会在运行时移除其他工具。模型若尝试
   不存在的通用工具，该失败调用仍保留在 trajectory 并计入 Invalid Call Rate。
4. 每个 case 使用独立、ephemeral session。
5. 轨迹仅记录模型可观察的工具名、参数、状态、工具结果和最终答案，不保存隐藏
   reasoning；参数按敏感键名脱敏，工具输出、错误和最终答案另按常见 API key、
   GitHub token 与 Bearer credential 格式脱敏。
6. 人工标签只在运行结束后用于评分。

```bash
export DEEPSEEK_API_KEY='...'
export GITHUB_TOKEN='...'

uv run python -m nanobot.repoops.benchmark \
  --tasks eval/repoops_tasks.json \
  --config eval/deepseek_config.example.json \
  --workspace /absolute/path/to/nanobot-snapshot \
  --output-dir eval/runs/deepseek-v4-pro-baseline
```

演示集：

```bash
uv run python -m nanobot.repoops.benchmark \
  --cases eval/demo_cases.json \
  --config eval/deepseek_demo_config.example.json \
  --workspace /absolute/path/to/nanobot-snapshot \
  --output-dir eval/runs/deepseek-v4-pro-demo
```

`run_summary.json` 汇总模型、token usage、耗时和失败；`trajectories/*.json`
保存逐工具轨迹。Issue 基线额外生成 `predictions.json` 与 `metrics.json`。

仓库内已提交的正式运行结果：

- `runs/deepseek-v4-pro-baseline/`：20 条 Issue，含轨迹、预测、指标与汇总；
- `runs/deepseek-v4-pro-demo/`：3 Issue、3 PR、3 个指定 CI job 的逐任务演示轨迹；
  本轮 9/9 成功，共 78 次工具调用。

## 指标边界

- 分类准确率与 File Recall@5 使用合并 PR 的人工标签。
- 工具 precision/recall 使用工作流要求的最小工具集合。
- citation 只有在引用 ID 存在，且 source 与 excerpt 能在工具结果中找到时才算
  有效；它是保守的字符串可追溯检查，不等同于语义事实核验。
- 这个任务集来自一个仓库，不足以证明跨语言、跨规模仓库的泛化能力。
