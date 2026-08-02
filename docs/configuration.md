# RepoOps Configuration

RepoOps 使用 nanobot 的配置加载器，默认读取 `~/.nanobot/config.json`。配置同时
接受 camelCase 和 snake_case。下面是 DeepSeek V4 Pro 的最小安全配置：

```json
{
  "agents": {
    "defaults": {
      "workspace": "/absolute/path/to/checkout",
      "model": "deepseek-v4-pro",
      "provider": "deepseek",
      "temperature": 0.1,
      "maxToolIterations": 16,
      "timezone": "Asia/Shanghai"
    }
  },
  "providers": {
    "deepseek": {
      "apiKey": "${DEEPSEEK_API_KEY}",
      "apiBase": "https://api.deepseek.com"
    }
  },
  "tools": {
    "restrictToWorkspace": true,
    "repoops": {
      "enable": true,
      "allowedRepositories": ["owner/repository"],
      "token": "${GITHUB_TOKEN}",
      "apiBase": "https://api.github.com",
      "stateDir": ".repoops",
      "timeout": 30,
      "maxDownloadBytes": 5000000,
      "maxOutputChars": 60000,
      "statusBarEnabled": true,
      "statusBarToolBudget": 10,
      "statusBarRepeatLimit": 3,
      "statusBarNoProgressLimit": 3
    }
  }
}
```

```bash
export DEEPSEEK_API_KEY='...'
export GITHUB_TOKEN='...'
uv run repoops webui
```

公开仓库的普通读操作可以不设置 `GITHUB_TOKEN`，但代码搜索、Actions 日志和
API rate limit 在认证后更稳定。任何 GitHub 写操作都要求 token 和独立审批。
不要把明文密钥写进配置、轨迹或 Git。

## RepoOps 字段

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `enable` | `true` | 注册 RepoOps 工具 |
| `allowedRepositories` | `[]` | 显式授权的 `owner/repo`；空列表拒绝所有仓库 |
| `token` | `""` | GitHub token，支持 `${GITHUB_TOKEN}` |
| `apiBase` | `https://api.github.com` | GitHub.com 或授权的 GHES HTTPS API |
| `stateDir` | `.repoops` | 工作区内状态目录，不能使用绝对路径或 `..` |
| `timeout` | `30` | API 超时秒数，范围 1–120 |
| `maxDownloadBytes` | `5000000` | Actions 日志压缩包及展开总量上限 |
| `maxOutputChars` | `60000` | 单个 RepoOps 工具输出上限 |
| `statusBarEnabled` | `true` | 每轮向模型副本注入代码生成的状态栏 |
| `statusBarToolBudget` | `10` | 默认工具预算；评测可按任务在调用属性中覆盖 |
| `statusBarRepeatLimit` | `3` | 相同工具与规范化参数达到此次数后要求停止原样重试 |
| `statusBarNoProgressLimit` | `3` | 连续无新增证据达到此次数后要求换策略或收尾 |

状态栏不会保存到历史。预算和熔断字段是模型决策约束，不会替代 Runner 的迭代上限；
GitHub 写入安全也不依赖它，仍由草稿—审批—执行状态机硬性保证。

`allowedRepositories` 是 capability boundary，不是提示词。模型即使调用 shell、
web 或构造其他 URL，也不应获得 RepoOps 之外的仓库权限。正式 benchmark 会进一步
移除所有非 `repoops_*` 工具，测量的就是项目自身能力。

## Token 权限

只读分析通常需要：

- Contents: read
- Issues: read
- Pull requests: read
- Actions: read
- Checks / Commit statuses: read

创建本地草稿无需 GitHub token。执行 Issue、评论或 PR 操作才需要对应写权限。
RepoOps 没有“自动同意模型写入”的配置开关。

## Benchmark 配置

[示例配置](../eval/deepseek_config.example.json) 会关闭 shell、通用文件和 web
工具，只允许 `repoops_*`。runner 还会为每次 invocation 创建唯一的 ephemeral
session 和 `.repoops/benchmark/<id>` 状态目录。`.repoops` 被 workspace indexer
排除，因此评测状态不会成为后续代码检索的候选。

```bash
uv run python -m nanobot.repoops.benchmark \
  --tasks eval/repoops_tasks.json \
  --config eval/deepseek_config.example.json \
  --workspace /absolute/path/to/nanobot-snapshot \
  --output-dir eval/runs/deepseek-v4-pro-baseline
```

正式数据固定在 `HKUDS/nanobot@6a1a45d07a6de420ba87c419ae30fcb4af76d4d0`。
不要用包含当前开发改动的 workspace 跑基线。

## 日报自动化

通过 nanobot Cron 调用只读日报工具：

```text
每天 09:00 生成 owner/repository 的 RepoOps 日报，只读取数据，不执行写操作。
```

日报包含时间窗口内的新 Issue、待处理 PR、失败 workflow run 和 stale Issue。
定时任务不能批准或执行 GitHub 写操作。
