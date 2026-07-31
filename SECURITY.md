# Security Policy

## 报告漏洞

不要把未修复的漏洞、token 或私有仓库内容发布到公开 Issue。请通过仓库的
GitHub Security Advisory 私下报告，并提供影响、复现步骤和建议修复。

## RepoOps 安全边界

- `tools.repoops.allowedRepositories` 是显式仓库授权；空列表默认拒绝。
- GitHub API 基址必须是 HTTPS，每个请求与日志重定向都会经过 SSRF 校验。
- `.repoops/` 状态目录必须位于活动工作区内，原子写入。
- Issue、PR、评论、Diff、文件和 CI 日志均为不可信内容，不能构成指令或审批。
- GitHub 写操作需要本地草稿、同一会话和后续用户轮次中的精确批准口令。
- `tools.restrictToWorkspace` 应在实际使用时启用；Linux 生产环境建议同时启用
  `tools.exec.sandbox = "bwrap"`。

## 凭据

推荐在 `~/.nanobot/config.json` 中保存环境变量引用：

```json
{
  "tools": {
    "repoops": {
      "token": "${GITHUB_TOKEN}"
    }
  }
}
```

GitHub token 应使用细粒度权限：只读分析仅授予 Issues、Pull requests、
Contents、Checks/Actions 的读权限；确需写入时再授予目标仓库的最小写权限。
不要把 token 写入仓库、日志、Issue、PR 或 Agent 记忆。

## 运行建议

- 用专用低权限系统账户运行，不要使用 root。
- WebUI/Gateway 默认只监听 localhost；若对外开放，必须配置 API 鉴权和反向代理。
- 定期审计 `.repoops/drafts/`、Agent 工具轨迹和 GitHub audit log。
- 发现泄露后立即撤销 token，保留日志，核对异常写操作并轮换相关凭据。
