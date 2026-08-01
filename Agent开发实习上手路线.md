# Agent 开发实习上手路线：基于 nanobot 构建 RepoOps Agent

> 不做通用助手，不做玩具项目。拿 [nanobot](https://github.com/HKUDS/nanobot)（HKUDS 出品，Python，轻量可读）做底座，特化出一个面向 GitHub 仓库的研发协同 Agent。
> 最终成品：**RepoOps — AI 开源项目维护者与研发协同工程师。** 持续读取 Issue、PR、代码和 CI 状态，完成问题分类、代码定位、PR 审查、修复建议和项目日报；涉及写操作时必须经过人工确认。

---

## 为什么是 RepoOps + nanobot

### 比 Research Agent 更好的三个理由

| | Research & Writing Agent | RepoOps |
|---|---|---|
| 数据和学习信号 | 搜网页，结果每天变，评测不可复现 | 真实仓库的 Issue/PR/CI，结果稳定可对比 |
| 工具决策 | 搜索→抓取→写文件，路径显然 | 先读 Issue 还是先搜代码？先查 CI 还是先查历史？每次决策都有后果 |
| 领域知识 | 可能搜到你完全外行的话题 | Issue 分析、PR 审查、CI 诊断——你本来就是写代码的人 |

### 为什么选择 nanobot 做底座

nanobot 已经具备你需要的所有基础设施：
- Agent 工具循环、文件、Shell、Web、MCP、定时任务和子 Agent
- 会话历史和长期记忆 (Dream)
- 长期运行的 Gateway
- 多聊天渠道、WebUI
- OpenAI 兼容 API、模型路由
- 工作区访问、Shell 沙箱、网络安全边界

**你不需要重写 CLI、WebUI、消息总线、Agent 循环、会话存储、Provider 适配。** 你只需要在 nanobot 运行时上增加面向研发协作的工具、状态、Skill、评测和安全流程。

### 对比其他路线

| 路线 | 手写所有代码 | 调 LangChain API | 基于 nanobot 特化 |
|------|------------|-----------------|------------------|
| 能跑起来 | 要四周 | 两天 | 第一周就能跑 |
| 理解深度 | 最深 | 最浅 | 深（你是改代码，不是调 API）|
| 面试说服力 | 中（自嗨项目） | 低（只会用框架） | 高（改了一个生产级项目） |
| 项目区分度 | 低（谁都能写） | 极低 | 高（领域特化+真实数据） |

---

## 最终成品

基于 nanobot 内核改造的 **RepoOps Agent**，能接收 GitHub 仓库的 Issue/PR/CI 查询，自主规划工具调用路径，输出带证据的结论。涉及写操作时必须经过人工确认。

```
用户: "分析仓库中最新的 Bug Issue，并给出排查建议"

┌─────────────────────────────────────────────┐
│           nanobot 内核（不改动）              │
│  agent loop · message bus · API client       │
│  tool registry · MCP client · gateway        │
│  Dream (长期记忆) · cron/heartbeat           │
└──────────────────┬──────────────────────────┘
                   │ 你在这之上特化
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
  GitHub 工具    代码检索      评测系统
  · list issues  · BM25 搜索   · 20 条 Issue 评测集
  · get PR diff  · RAG 检索    · 分类/召回/工具准确率
  · read CI logs · Embedding   · 证据完整率/幻觉率

  任务状态机      安全控制      自动化
  · RepoTaskState · 只读/低风险/  · Cron 日报
  · 证据链        · 高风险分级  · CI 异常告警
  · 假设管理      · 人工确认    · 长期未响应提醒
```

---

## 学习方式：先读后改，不停对照

**每做一个模块的三步循环：**

1. **先读 nanobot 源码** — 找到对应模块，理解它的设计和接口
2. **关闭 nanobot，自己写** — 基于理解做特化实现，不复制粘贴
3. **打开 nanobot，对比** — 你为什么和它不一样？谁的设计更好？

---

## 项目目录结构

```
repoops/
│
├── nanobot/                        # nanobot 源码（只读，参考对照）
│   # git clone 到本地
│
├── tools/                          # 研发协作工具集
│   ├── base.py                     # 工具基类
│   ├── github_issues.py            # Issue 读取/搜索/分类
│   ├── github_prs.py               # PR 读取/审查
│   ├── github_ci.py                # CI 状态/日志
│   ├── github_code.py              # 代码搜索/文件读取
│   └── github_write.py             # 写操作（Issue/评论/PR草稿）
│
├── skills/                         # 场景 Skill
│   ├── registry.py                 # Skill 注册表
│   ├── issue_analysis/
│   │   ├── SKILL.md                # Issue 分析工作流
│   │   └── tools.py
│   ├── pr_review/
│   │   ├── SKILL.md                # PR 审查工作流
│   │   └── tools.py
│   └── ci_diagnosis/
│       ├── SKILL.md                # CI 故障诊断工作流
│       └── tools.py
│
├── state/                          # 结构化任务状态
│   ├── repo_task.py                # RepoTaskState 数据模型
│   ├── evidence.py                 # 证据链管理
│   └── hypothesis.py               # 假设管理
│
├── rag/                            # 代码与文档 RAG
│   ├── chunker.py                  # 代码专用分块策略
│   ├── embedder.py                 # embedding 封装
│   ├── retriever.py                # 稀疏+稠密+混合（代码→符号搜索优先）
│   ├── reranker.py                 # Cross-encoder 精排
│   └── indexer.py                  # 项目文档/代码/历史Issue索引
│
├── safety/                         # 权限与安全
│   ├── tool_classifier.py          # 只读/低风险/高风险三级分类
│   ├── approval_gate.py            # 人工确认门控
│   └── repo_guard.py               # 仓库级授权
│
├── automation/                     # 自动化任务
│   ├── daily_digest.py             # 日报生成
│   └── stale_monitor.py            # 长期未响应监控
│
├── eval/                           # 评测系统
│   ├── tasks.json                  # 20 条标注 Issue
│   ├── run.py                      # 自动跑任务
│   ├── metrics.py                  # 多维度指标
│   └── judge.py                    # LLM-as-Judge
│
├── config.py
├── main.py
│
├── RepoOps项目文档/源码学习与设计笔记.md  # nanobot 源码阅读笔记
├── ARCHITECTURE.md                 # 架构文档
├── RepoOps项目文档/失败案例与风险清单.md  # Bad case 记录
└── EVAL_REPORT.md                  # 评测报告
```

---

## 第零天：安装 nanobot + 跑通 + 读懂核心循环

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | sh
nanobot webui
# → http://127.0.0.1:8765 → Settings → Models → 配 API
```

然后做三件事：

**1. 让 nanobot 完成一个多步骤任务，观察轨迹。**

在 WebUI 里观察它的 thinking、tool_call、tool_result 的完整轨迹。

**2. 找到核心循环的源码位置。**

```bash
git clone https://github.com/HKUDS/nanobot.git /tmp/nanobot-src
```

在源码里找到：Agent 主循环、工具注册和执行入口、消息列表管理、Dream 长期记忆。

**3. 画一张调用图。** 用户输入 → 经过哪些模块 → 怎么到 LLM → tool_call 怎么返回 → 怎么执行 → 怎么回到循环。

---

## 第一周：核心闭环 — Issue 分析 + PR 审查

**目标：只读闭环跑通。输入 Issue/PR 编号，Agent 自主读取、搜索、分析，输出带证据的结论。**

### 周一：搭建骨架 + GitHub 只读工具

```bash
mkdir -p repoops/tools repoops/skills repoops/state repoops/rag repoops/safety repoops/automation repoops/eval
cd repoops
python -m venv venv && source venv/bin/activate
pip install openai faiss-cpu rank-bm25 PyGithub pymupdf --break-system-packages
```

**实现 6 个只读工具（原子性，一个工具做一件事）**：

```
list_repository_issues(repo, state, labels, limit)
get_issue_detail(repo, issue_number)
search_similar_issues(repo, query)
search_repository_code(repo, query)
read_repository_file(repo, path, start_line, end_line)
get_pull_request(repo, pr_number)
get_pull_request_diff(repo, pr_number)
get_ci_status(repo, pr_number)
get_ci_failure_logs(repo, run_id)
```

**工具设计遵守 ACI 原则：每个工具是 Agent 的一个目标动作，不是 GitHub API 的薄封装。**
不要做一个 `solve_github_problem()`——所有决策藏在工具内部，体现不出 Agent 能力。

**参考 nanobot**：它的工具注册机制。工具 schema 是工具对象自带的，不是写死在 prompt 里。你的工具只读部分可以直接跑通。

### 周二：Issue 分析 Skill

**`skills/issue_analysis/SKILL.md`** — 定义完整工作流：

```markdown
## Issue 分析工作流

1. 获取 Issue 详情（标题、描述、标签、环境信息）
2. 判断信息是否完整——缺失复现步骤？缺失版本号？缺失日志？
3. 如果缺失关键信息 → 生成需要补充的问题列表 → 标记 requires_human_approval
4. 搜索相似历史 Issue（semantic + keyword）
5. 根据 Issue 描述搜索相关代码文件
6. 查看相关 Commit
7. 生成分析报告：问题分类 + 可能涉及文件 + 判断依据 + 缺失信息 + 建议
```

**`skills/issue_analysis/tools.py`** — 这个 Skill 独有的工具（如果有的话）。

### 周三：PR 审查 Skill

**`skills/pr_review/SKILL.md`**：

```markdown
## PR 审查工作流

1. 读取 PR 描述和变更文件
2. 阅读 Diff
3. 查询相关代码上下文（变更函数/类的完整实现）
4. 查询 CI 测试结果
5. 检查是否修改了公开接口
6. 输出四类审查意见：
   - 确定问题（有明确证据）
   - 潜在风险（需要进一步确认）
   - 建议改进（风格/性能优化）
   - 未能验证的猜测（明确标注不确定性）
```

输出必须严格区分"确定"和"猜测"。这体现你知道：Agent 不应该把所有推测包装成确定事实。

### 周四：结构化任务状态

**`state/repo_task.py`** — 为每个 Issue/PR 维护状态，不依赖对话历史：

```python
class RepoTaskState(BaseModel):
    task_type: str                    # "issue_analysis" | "pr_review" | "ci_diagnosis"
    repository: str
    issue_or_pr_number: int
    confirmed_facts: list[str]        # "Python 3.11", "错误出现在并发场景"
    missing_information: list[str]    # "未提供 Python 版本", "缺少完整堆栈"
    related_files: list[str]          # 通过代码搜索找到的相关文件
    related_issues: list[int]         # 搜索到的相似历史 Issue
    hypotheses: list[Hypothesis]      # 假设列表，每条有置信度
    executed_tools: list[ToolRecord]  # 已执行工具及结果摘要
    evidence: list[Evidence]          # 每条结论对应的证据
    next_actions: list[str]           # 下一步计划
    requires_human_approval: bool     # 是否需要人工确认
```

**为什么需要这个？** 聊天历史在上下文压缩后会丢失细节。Agent 可能忘记已查过的文件、重复读取同一个 PR、或把假设误认为事实。结构化状态持久存储，是 Agent 的"外挂工作记忆"。

### 周五：错误处理 + 去重 + 轨迹记录

- 工具调用失败 → 结构化错误返回 → Agent 自主恢复
- 连续 3 次同一工具同一参数 → 强制终止
- 完整 Agent 轨迹记录 → 为第二周评测积累数据

### 第一周最终效果

```
输入: Issue #42
→ Agent 读取 Issue 详情
→ 搜索相似历史 Issue
→ 搜索相关代码
→ 输出带证据的分析报告：
  问题分类: Bug / 并发问题
  可能涉及文件: service/task_runner.py, tests/test_task_runner.py
  判断依据: [代码行号]、[历史Issue #28]、[CI日志]
  缺失信息: Python版本、并发数
  建议: 先让提交者补充信息
```

### 第一周验收标准

- [ ] nanobot 已安装，你用它跑过至少一个多步骤任务
- [ ] 6 个 GitHub 只读工具全部可用
- [ ] Issue 分析 Skill 跑通 3 个真实 Issue
- [ ] PR 审查 Skill 跑通 3 个真实 PR
- [ ] 结构化任务状态持久化
- [ ] 重复调用检测生效
- [ ] `RepoOps项目文档/源码学习与设计笔记.md` 至少 10 条“我和 nanobot 的设计差异+分析”
- [ ] 5 条 bad case 记录
- [ ] 你能口述 nanobot 核心循环的数据流

---

## 第二周：工程化 — RAG + CI + 自动化 + 安全 + 评测

**目标：从"能跑"到"能看"。加上 RAG、CI 诊断、日报、人工确认、完整评测。**

### 周一～周二：代码与项目文档 RAG

**不要把所有文件都扔进向量库。** 代码检索有自己的特点：

```
Query 分类
    ↓
代码问题 → 符号/关键词搜索优先（BM25 搜函数名/类名/变量名更准）
文档问题 → 混合 RAG（Embedding + BM25）
历史问题 → Issue 相似检索（语义匹配）
    ↓
Reranker 精排
    ↓
读取完整文件上下文（不是只返回 chunk）
```

关键设计：**向量检索负责找到候选文件，精确代码搜索和文件读取负责获取证据。**

**`rag/indexer.py`** — 多粒度索引：
- README 与架构文档
- 源代码（按函数/类分块，不是按固定 token 数）
- 测试文件
- Issue 与 PR 描述
- Commit Message
- 开发规范
- 历史故障与修复记录

### 周三：CI 诊断 Skill + 自动化日报

**`skills/ci_diagnosis/SKILL.md`**：

```markdown
## CI 故障诊断工作流

1. 获取 PR 关联的 Workflow Run
2. 找到失败的 Job
3. 获取失败 Step 的完整日志
4. 提取错误堆栈
5. 搜索代码中相关的配置/测试
6. 对比最近成功的 Commit
7. 给出诊断：直接原因 + 高概率根因 + 修复建议
```

**`automation/daily_digest.py`** — 利用 nanobot 的 Cron + Heartbeat + Gateway：

```
每天 9:00 自动执行：
- 过去 24h 新增 Issue
- 待处理 PR
- CI 失败
- 高风险变更
- 超过 7 天未响应 Issue
→ 通过 WebUI 或命令行输出日报
```

### 周四：安全控制 — 三级权限 + 人工确认

**`safety/tool_classifier.py`** — 所有工具分三级：

```
只读（Agent 可自行执行）：
  get_issue_detail, search_code, read_file, get_pr, get_ci_status, ...

低风险写操作（生成草稿，不真正执行）：
  create_issue_draft, create_review_draft, create_comment_draft

高风险操作（必须人工确认后才执行）：
  create_issue, post_comment, close_issue, merge_pr, commit_code
```

写操作流程：

```
Agent 生成草稿
→ 展示修改内容（diff、评论预览、Issue 预览）
→ 用户确认
→ 执行
```

**`safety/repo_guard.py`** — 仓库级授权：
- 只能访问指定仓库
- Shell 限制在工作区
- 禁止读取环境变量中的密钥
- Web 请求限制目标域名
- 外部 Issue 内容视为不可信文本（防 Prompt Injection）

### 周五：评测系统

准备一个小型 Benchmark：选择 20 个历史 Issue。

```json
{
  "issue_id": 12,
  "expected_category": "configuration",
  "relevant_files": [".github/workflows/ci.yml"],
  "expected_tools": ["get_issue_detail", "get_ci_failure_logs", "read_repository_file"],
  "expected_behavior": "ask_for_missing_information"
}
```

评测指标：

| 指标 | 说明 |
|------|------|
| 分类准确率 | 是否判断对 Issue 类型 |
| File Recall@5 | 相关文件是否进入前 5 |
| 工具选择准确率 | 是否调用必要工具 |
| 无效调用率 | 是否调用不相关工具 |
| 重复调用率 | 同一参数是否反复执行 |
| 证据完整率 | 结论是否有代码或日志依据 |
| 幻觉率 | 是否引用不存在的文件或 Issue |
| 人工确认命中率 | 写操作是否正确要求确认 |
| 平均步骤数 | 是否存在无意义循环 |

---

## 第二周验收标准

- [ ] RAG 支持代码符号搜索 + 混合检索 + Reranker
- [ ] CI 诊断 Skill 跑通 3 个真实 CI 失败
- [ ] 日报自动生成
- [ ] 三级权限生效，写操作需要人工确认
- [ ] 20 条 Issue 评测跑通，所有指标有数据
- [ ] `docs/architecture.md` + `RepoOps项目文档/失败案例与风险清单.md` +
  `RepoOps项目文档/项目评估报告.md` 三份文档完成
- [ ] Bad case 不少于 10 条
- [ ] 评测数据可对比（改 RAG 参数前后 Recall 变化、加去重前后重复调用率变化）

---

## 面试时怎么讲这个项目

### 架构叙述（2 分钟版本）

> 我基于 nanobot 做了一个特化的 GitHub 仓库维护 Agent。nanobot 提供模型、工具、记忆、渠道和定时任务运行时，我在之上做了研发场景的二次开发。
>
> 工具层我实现了 15 个带 schema 的领域工具，覆盖 Issue、PR、CI、代码检索、任务状态和写操作审批。工具设计遵守 ACI 原则——每个工具是 Agent 的一个目标动作，不是 API 的薄封装。
>
> 状态管理层我设计了 RepoTaskState——为每个 Issue 和 PR 维护确认的事实、缺失信息、假设、证据链和已执行工具。这让 Agent 在长对话中不丢失上下文，也避免把假设当事实。
>
> RAG 层采用 AST 符号分块、BM25 与 trigram 的轻量混合检索，先按唯一路径聚合，再补充稀有查询词覆盖；精确读取固定在任务对应的 pre-fix SHA。当前没有把未实现的向量库或 cross-encoder 包装成项目能力。
>
> 安全层所有工具分三级——只读自动执行、低风险生成草稿、高风险需要人工确认。外部 Issue 内容视为不可信文本防止 Prompt Injection。
>
> 评测层覆盖 Python、Go、TypeScript、Rust 四个公开仓库。当前主结果是 15 条逐任务 pre-fix 跨语言 Issue：RepoOps 使用 DeepSeek V4 Pro 达到 15/15 结构化成功、100.0% 分类和 93.1% File Recall@5；同主模型 Claude Code 为 13/15、86.7% 和 77.1%。

### 面试追问：你觉得最核心的一个设计决策是什么

> 我认为是"不把所有文件都向量化"的检索策略。很多人做代码 RAG 就是把整个仓库扔进向量库，但代码检索和文档检索有本质区别。搜"task_runner"这种精确符号，BM25 比向量检索准得多——向量检索可能返回不包含这个符号但"语义相近"的文件，对开发者来说完全是噪音。
>
> 我的实现把同一路径的多个 chunk 先聚合，再保留稀有查询词命中的候选，并保证 top-k 工具输出是有效 JSON。跨语言 v3 到 v4 的 File Recall@5 从 79.1% 提升到 93.1%，Invalid Call Rate 从 0.76% 降到 0.73%；但这是整组工程优化后的结果，不能把全部增益只归因于单个检索改动。

### 面试追问：这个项目和直接配一个 GitHub MCP Server 有什么区别

> 配一个 MCP Server 是 10 分钟的事——Agent 能调 API，但不知道先调哪一个、不知道怎么串联、不知道什么时候该停下来问用户补充信息。我的 RepoOps 有五个额外的层：
>
> 第一是 Skill 层——Issue 分析、PR 审查、CI 诊断是完整工作流。第二是工具层——15 个受 schema 约束的领域动作。第三是状态层——持久化 facts、hypotheses、evidence 和轨迹。第四是安全层——allowlist、SSRF 防护与跨轮审批。第五是评测层——真实历史任务、固定快照和失败轨迹审计。
>
> 这些工程边界是完整 Agent 系统与单纯接通 API 的区别；Skill 只是其中的 SOP 层。

---

## 简历描述

> **RepoOps 开源仓库维护与研发协同 Agent｜Python / nanobot / RAG / GitHub API**
>
> 基于 nanobot 二次开发 GitHub 仓库维护 Agent，实现 15 个 GitHub/RAG/状态工具、符号优先混合检索、证据持久化、SSRF 防护和跨轮人工审批。构建 Python/Go/TypeScript/Rust 四仓库真实历史 Issue 评测集；在 15 条逐任务 pre-fix 对照中取得 15/15 结构化成功、100.0% 分类、93.1% File Recall@5，同主模型 Claude Code 为 13/15、86.7%、77.1%，调查调用减少 37.7%。

---

## 技术栈

| 组件 | 工具 | 说明 |
|------|------|------|
| Agent 底座 | nanobot | 不改动内核，在此之上特化 |
| LLM API | nanobot Provider / DeepSeek compatible endpoint | 复用 Provider 抽象，凭据只从环境变量读取 |
| GitHub 交互 | httpx + GitHub REST | Issue/PR/CI 与受审批保护的写操作 |
| 代码搜索 | Python AST + BM25 + trigram | 符号优先、路径聚合与模糊召回 |
| 任务状态 | Pydantic + 原子 JSON | facts/hypotheses/evidence/tool trace |
| 评测 | pytest + 自定义 benchmark harness | 固定快照、完整轨迹与 10 项指标 |
| 文档处理 | pymupdf + python-docx | PDF + Word |

**不用学**：LangChain/LlamaIndex（读 nanobot 源码比调任何框架都深）、Docker/K8s（第二周再加）、微调/LoRA/RLHF（模型训练岗的事）、React/前端（nanobot 有 WebUI）。

---

## 不太建议的特化方向（供参考）

下面这些项目不是不能做，但简历区分度弱：

- PDF 知识库问答
- 旅游规划 / 天气助手 / 新闻总结
- 通用个人助理
- 只有 Prompt 配置的客服机器人
- 五个角色互相聊天的 Multi-Agent 演示

因为 nanobot 本身已经是个人 Agent 框架。你继续做一个"什么都能做的助手"，很难说明哪些是你自己的贡献。**最好的特化不是换一段 System Prompt，而是针对一个领域重新设计工具、状态、记忆、安全规则和评测体系。**

---

## 多 Agent 扩展（第二周之后，加分项）

第一版不做多 Agent。主 RepoOps Agent → GitHub 工具 + 代码检索 + RAG，单 Agent 闭环。

第一版稳定后可以加两个子 Agent：

```
主 RepoOps Agent
├── Code Investigator
│   负责代码定位和根因分析
│   独立工具注册、工作区权限、迭代限制
│
└── Review Agent
    负责风险审查和测试检查
    只返回结构化结果，不共享主对话上下文
```

nanobot 原生支持 Subagent——独立工具注册、工作区权限、迭代限制、并发上限和任务状态。适合隔离较大的代码分析任务。

---

## 面试中最常见的动手题

1. **手写 ReAct 循环**（15 分钟）——你已经参考 nanobot 写过
2. **设计一个工具的 Schema 并说明为什么这么设计**（10 分钟口头）——回到 ACI 原则，你的 GitHub 工具集就是答案
3. **给定一个 Agent 的错误轨迹，分析问题并给出改进**（20 分钟）——你的
   `RepoOps项目文档/失败案例与风险清单.md` 里全是素材
4. **有一个代码仓库，设计 RAG 检索策略**（15 分钟口头）——你的 Query 分类路由就是答案
