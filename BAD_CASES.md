# RepoOps Bad Cases

以下记录用于防止修复过的问题回归。状态为“已防护”表示已有实现和/或自动化测试。

| # | Bad case | 风险 | 处理 | 状态 |
|---:|---|---|---|---|
| 1 | `allowedRepositories` 为空仍访问任意仓库 | 越权读取/写入 | 空列表默认拒绝，仓库名逐次校验 | 已防护 |
| 2 | Issue 正文包含 `APPROVE REPOOPS ...` | Prompt injection 触发写入 | 审批只读原始用户消息 | 已防护 |
| 3 | 创建草稿的同一轮直接执行 | 模型自我批准 | turn ID 必须不同 | 已防护 |
| 4 | 另一个会话复制批准口令 | 跨用户/跨渠道批准 | session key 必须一致 | 已防护 |
| 5 | 两个并发请求执行同一草稿 | 重复评论、重复 Issue 或重复合并 | 原子抢占 `pending -> executing` | 已防护 |
| 6 | `stateDir = ../secrets` | 工作区逃逸 | resolve 后执行 containment 检查 | 已防护 |
| 7 | Actions 日志下载 302 到任意域名 | SSRF / 数据注入 | 每次跳转重验，只放行签名日志域名 | 已防护 |
| 8 | 压缩日志很小但展开后巨大 | zip bomb / 内存耗尽 | 同时限制下载量与展开总量 | 已防护 |
| 9 | Python AST 索引只收函数/类 | 漏掉 import、常量和模块级配置 | 符号块与行窗口并存 | 已修复 |
| 10 | GitHub Issues API 把 PR 当 Issue 返回 | 分类与日报污染 | 列表和日报过滤 `pull_request` 项 | 已防护 |
| 11 | 一个远程文件含极长单行 | 工具结果挤爆模型上下文 | 行数和字符数双重限制 | 已防护 |
| 12 | CI 日志没有匹配 `error` 的行 | 返回空证据 | 回退到每个日志最后 80 行 | 已防护 |
| 13 | GitHub 写请求失败后自动重试 | 请求实际成功但响应丢失时重复写 | 草稿保持 `executing`，要求人工核对 | 已防护，需运维流程 |
| 14 | 公网域名在受控环境解析为内部代理地址 | SSRF guard 拒绝真实只读冒烟 | 不弱化默认 guard；使用明确网络白名单/代理策略 | 环境限制 |
| 15 | 远程代码搜索在无 token 时失败 | Agent 无法定位远程代码 | 返回结构化错误，优先搜索本地 checkout | 已降级 |
| 16 | PR 超过 100 个变更文件或 Issue 超过 100 条评论 | 第一页数据不完整 | 输出仍可用，但当前没有自动翻页 | 待改进 |
| 17 | DeepSeek 对同一文件平移行号窗口、重复检索 | token/时延失控；首轮 Issue #5133 用 25 步和 271 秒 | prompt 加总调用/批次/精确读取预算；同题隔离重跑降到 8 步和 123 秒 | 已缓解，仍需代码级预算 |
| 18 | benchmark 复用相同 session key 和 task state | 新一轮 0 次工具调用复述旧答案，指标被污染 | 每次 invocation 生成唯一 session namespace 与 state directory | 已修复 |
| 19 | Provider 产出字符串形式的 malformed tool arguments | Agent 可把调用判无效，但 trajectory recorder 因假定 dict 而崩溃 | `ToolTrace.arguments` 保留 dict 或原始 string，并计入 invalid call | 已修复并有回归测试 |
| 20 | 达到 max iterations 时模型输出 DSML tool-call 文本而非最终 JSON | 任务有工具证据但无法评分 category/files | 作为 invalid output 如实计分；后续考虑为 benchmark 增加独立结构化收尾轮 | 已记录，待改进 |
| 21 | 16k RepoOps 结果加安全标头后略超 runtime 的 16k 阈值；setup 中的 `ErrorActionPreference` 又误命中宽泛 error regex | 结果被落盘，或真正测试错误被 setup 噪声挤出预算 | CI runtime 内联阈值设为 18k；摘要优先 timeout、pytest、Ruff rule 等强信号并排除成功 job | 已修复并有回归测试 |
| 22 | Issue/日志在普通文本中含类似真实凭据的值 | trajectory 泄露外部提交的 token，按参数键脱敏抓不到 | 工具输出、错误、最终答案按常见 API key/GitHub token/Bearer 格式二次脱敏，hash 基于脱敏内容 | 已修复并有回归测试 |
| 23 | `Tool 'read_file' not found` 未命中 invalid regex，invalid output 反而加到 invalid call | 指标把 21 个真实无效调用报成 1.8% | 补 Tool-not-found 识别；分母使用全部真实调用；invalid output 与 invalid call 分开 | 已修复并重新评分 |
| 24 | 通用 nanobot tool contract 诱导 DeepSeek 在 RepoOps-only run 调 `read_file`/`exec` | 额外失败轮次与 token 消耗 | 注册边界拒绝并完整计分；always-on Skill 与 benchmark prompt 明示唯一文件工具；仍待新 key 复跑验证 | 已缓解，待实跑 |

实际轨迹：

- [未加预算的 25 步轨迹](eval/runs/deepseek-v4-pro-smoke/trajectories/nanobot-issue-5133.json)
- [加入预算且隔离后的 8 步轨迹](eval/runs/deepseek-v4-pro-smoke-isolated/trajectories/nanobot-issue-5133.json)

Bad case #18 说明 `ephemeral=True` 不能替代基准隔离：它控制当前 turn 的持久化，
但评测仍必须使用全新 session key；RepoOps 的独立任务状态也必须换目录。

## 新 Bad Case 记录模板

```markdown
### 标题

- 输入/轨迹：
- 预期：
- 实际：
- 根因：
- 修复：
- 回归测试：
- 指标影响：
```
