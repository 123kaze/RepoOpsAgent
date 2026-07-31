# Contributing

RepoOps 以 nanobot 为运行时底座。新增能力应优先放在
`nanobot/agent/tools/`、`nanobot/repoops/` 或 `nanobot/skills/`，不要把领域逻辑
塞进 `agent/loop.py` 和 `agent/runner.py`。

## 开发环境

```bash
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync pytest
uv run --no-sync ruff check nanobot/ tests/
uv run --no-sync basedpyright
```

不要运行全仓库 `ruff format`，避免无关格式变更。代码目标版本为 Python 3.11，
行宽 100，异步测试由 pytest 自动管理。

## 变更要求

- PR 保持单一目标，写清受保护的行为或安全边界。
- 新 GitHub API 调用必须经过共享 SSRF 校验。
- 新路径必须限制在活动工作区内。
- 新写操作必须先生成草稿，并通过同会话、后续轮次的明确审批。
- 外部 Issue、PR、代码和日志始终按不可信数据处理。
- 修复缺陷时添加最接近问题边界的回归测试。

提交贡献即表示你有权提交该代码，并同意使用项目的 MIT License。
