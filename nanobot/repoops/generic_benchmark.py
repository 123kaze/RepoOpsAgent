"""Benchmark pre-RepoOps nanobot with generic tools or the official GitHub MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanobot.repoops.benchmark import (
    BenchmarkCase,
    Trajectory,
    _arguments_key,  # pyright: ignore[reportPrivateUsage]
    _redact_text,  # pyright: ignore[reportPrivateUsage]
    _task_case,  # pyright: ignore[reportPrivateUsage]
    _write_json,  # pyright: ignore[reportPrivateUsage]
    parse_answer,
    trajectory_prediction,
)
from nanobot.repoops.evaluation import EvalPrediction, EvalTask, EvalToolCall, evaluate, load_tasks

PRE_REPOOPS_COMMIT = "6a1a45d07a6de420ba87c419ae30fcb4af76d4d0"
_ISSUE_VIEW = re.compile(r"\bgh\s+issue\s+view\b")
_ISSUE_SEARCH = re.compile(r"\bgh\s+(?:issue\s+list|search\s+issues)\b")


def _answer_contract() -> str:
    categories = (
        "bug|feature|documentation|question|configuration|performance|security|"
        "insufficient-information"
    )
    return f"""
完成调查后只输出一个 JSON 对象，不要使用 Markdown 代码块：
{{
  "category": "{categories}",
  "files": ["最多 5 个仓库相对路径，按相关度排序"],
  "confirmed_facts": [{{"claim": "证据直接支持的事实", "evidence_ids": ["E1"]}}],
  "hypotheses": [{{"claim": "仍需验证的假设", "evidence_ids": ["E2"]}}],
  "evidence": [{{
    "evidence_id": "E1",
    "source": "工具输出中出现的原始 Issue URL 或仓库相对路径",
    "locator": "Issue 标识或行号范围",
    "excerpt": "从工具结果逐字复制的连续短片段"
  }}],
  "missing_information": ["尚未验证的信息"],
  "recommended_actions": ["下一步"],
  "approval_required": false
}}
分类边界：bug 是已有预期行为失效；feature 是请求新增当前快照尚不存在的能力、配置项
或 API；configuration 只表示已有配置的使用、迁移或调优问题。不要因为网络失败或根因
未确认而把标题已经明确的 bug/feature 降级为 insufficient-information。files 和
evidence.source 只能使用工具结果实际出现的路径；excerpt 不得改写或用省略号拼接。
不得查看或推断评测标准答案。这是只读评测，不得修改工作区或 GitHub。
""".strip()


def build_prompt(case: BenchmarkCase, agent: str) -> str:
    owner, repository = case.repository.split("/", 1)
    if agent == "vanilla-nanobot":
        tool_instructions = f"""
使用 `$github` Skill。已注册的只读调查工具只有 `exec`、`grep`、`find_files`、
`list_dir` 和 `read_file`。`exec` 被硬性 allowlist 限制，只能执行一次
`gh issue view {case.number} --repo {case.repository} --json number,title,body,state,labels,comments,url`
或必要时一次只读 Issue 搜索；代码必须使用本地 grep/find/read 工具调查。
"""
    else:
        tool_instructions = f"""
GitHub 数据只能调用官方 GitHub MCP Server 的只读工具：先调用
`mcp_github_issue_read`，参数 method=`get`、owner=`{owner}`、repo=`{repository}`、
issue_number={case.number}；确有必要时再调用一次 `mcp_github_search_issues`。本地代码
只能调用 `grep`、`find_files`、`list_dir` 和 `read_file`。没有 shell/exec 工具。
"""
    return f"""
你是只读的 GitHub Issue 分析 Agent。分析真实历史 Issue，判断分类，定位固定代码快照
中最相关的实现文件，并严格区分证据支持的事实与尚待验证的假设。

任务：{case.prompt}
Issue 标题：{case.title}
仓库：{case.repository}
Issue：#{case.number}
本地代码快照：{case.snapshot_sha}
工作目录已经固定在该 pre-fix commit。

{tool_instructions.strip()}

效率约束：调查工具总计最多 10 次、最多 6 个工具批次。先获取 Issue，再做最多两次
本地搜索；最多读取 3 个精确文件范围。若 feature 目标名称在 pre-fix 快照不存在，选择
类型和作用域相近的现有选项追踪“声明/CLI → 序列化 → 配置解析 → 运行时消费”，不要
反复搜索缺失名称。信息不足时写入 missing_information，不要继续泛搜。

{_answer_contract()}
""".strip()


def _normalized_tool_name(name: str, arguments: dict[str, Any] | str) -> str:
    if name == "read_file":
        return "repoops_read_file"
    if name in {"grep", "find_files", "list_dir"}:
        return "repoops_search_workspace"
    if name == "mcp_github_issue_read":
        return "repoops_get_issue"
    if name == "mcp_github_search_issues":
        return "repoops_search_issues"
    if name == "exec":
        command = (
            str(arguments.get("command") or arguments.get("cmd") or "")
            if isinstance(arguments, dict)
            else ""
        )
        if _ISSUE_VIEW.search(command):
            return "repoops_get_issue"
        if _ISSUE_SEARCH.search(command):
            return "repoops_search_issues"
    return f"generic:{name}"


def generic_trajectory_prediction(task: EvalTask, trajectory: Trajectory) -> EvalPrediction:
    prediction = trajectory_prediction(task, trajectory)
    prediction.tool_calls = [
        EvalToolCall(
            name=_normalized_tool_name(trace.name, trace.arguments),
            arguments_key=_arguments_key(trace.arguments),
        )
        for trace in trajectory.tool_trace
    ]
    prediction.invalid_tool_calls = sum(trace.status == "error" for trace in trajectory.tool_trace)
    return prediction


async def _command(*arguments: str, cwd: Path | None = None) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        int(process.returncode or 0),
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def _tracked_status(workspace: Path) -> str:
    return_code, stdout, stderr = await _command(
        "git",
        "-C",
        str(workspace),
        "status",
        "--porcelain",
        "--untracked-files=no",
    )
    if return_code != 0:
        raise RuntimeError(stderr.strip() or stdout.strip())
    return stdout.strip()


async def _runtime_commit(runtime_root: Path) -> str:
    return_code, stdout, stderr = await _command(
        "git", "-C", str(runtime_root), "rev-parse", "HEAD"
    )
    if return_code != 0:
        raise RuntimeError(stderr.strip() or stdout.strip())
    return stdout.strip()


def _failed_trajectory(case: BenchmarkCase, prompt: str, error: str) -> Trajectory:
    now = datetime.now(UTC).isoformat()
    return Trajectory(
        case_id=case.case_id,
        task_type=case.task_type,
        repository=case.repository,
        number=case.number,
        started_at=now,
        completed_at=now,
        duration_ms=0,
        source_url=case.source_url,
        snapshot_sha=case.snapshot_sha,
        prompt=prompt,
        tool_trace=[],
        final_answer="",
        parse_error="",
        run_error=_redact_text(error),
    )


async def _run_worker(
    args: argparse.Namespace,
    case: BenchmarkCase,
    trajectory_path: Path,
) -> Trajectory:
    prompt = build_prompt(case, args.agent)
    if await _tracked_status(args.workspace):
        return _failed_trajectory(case, prompt, "workspace had tracked changes before run")

    case_payload = {
        "case_id": case.case_id,
        "repository": case.repository,
        "number": case.number,
        "source_url": case.source_url,
        "snapshot_sha": case.snapshot_sha,
        "benchmark_prompt": prompt,
    }
    worker = Path(__file__).resolve().parents[2] / "scripts/pre_repoops_benchmark_worker.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(args.runtime_root.resolve())
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(worker),
        "--agent",
        args.agent,
        "--config",
        str(args.config.resolve()),
        "--workspace",
        str(args.workspace.resolve()),
        "--runtime-root",
        str(args.runtime_root.resolve()),
        "--case-json",
        json.dumps(case_payload, ensure_ascii=False, separators=(",", ":")),
        "--output",
        str(trajectory_path.resolve()),
        "--timeout",
        str(args.timeout),
        cwd=args.workspace,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=args.timeout + 60)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return _failed_trajectory(case, prompt, "worker exceeded outer timeout")

    if process.returncode != 0 or not trajectory_path.exists():
        detail = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        return _failed_trajectory(
            case,
            prompt,
            f"worker failed with exit {process.returncode}: {detail}",
        )

    trajectory = Trajectory.model_validate_json(trajectory_path.read_text(encoding="utf-8"))
    if trajectory.final_answer:
        try:
            trajectory.parsed_answer = parse_answer(trajectory.final_answer)
            trajectory.parse_error = ""
        except (TypeError, ValueError) as exc:
            trajectory.parse_error = str(exc)
    elif not trajectory.run_error:
        trajectory.parse_error = "empty final answer"

    dirty_after = await _tracked_status(args.workspace)
    if dirty_after:
        suffix = "workspace tracked files changed during read-only benchmark"
        trajectory.run_error = f"{trajectory.run_error}; {suffix}".strip("; ")
    return trajectory


async def _run(args: argparse.Namespace) -> int:
    if await _runtime_commit(args.runtime_root) != args.expected_runtime_commit:
        raise ValueError(f"runtime root must be pre-RepoOps commit {args.expected_runtime_commit}")
    tasks = load_tasks(args.tasks)
    selected = [task for task in tasks if not args.case_id or task.task_id in args.case_id]
    missing = sorted(set(args.case_id or []) - {task.task_id for task in selected})
    if missing:
        raise ValueError(f"Unknown case IDs: {', '.join(missing)}")
    if len(selected) != 1:
        raise ValueError("generic benchmark shards require exactly one --case-id")

    task = selected[0]
    case = _task_case(task)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trajectory_path = args.output_dir / "trajectories" / f"{case.case_id}.json"
    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    trajectory = await _run_worker(args, case, trajectory_path)
    _write_json(trajectory_path, trajectory.model_dump(mode="json"))

    prediction = generic_trajectory_prediction(task, trajectory)
    metrics = evaluate([task], [prediction])
    _write_json(
        args.output_dir / "predictions.json",
        [prediction.model_dump(mode="json")],
    )
    _write_json(args.output_dir / "metrics.json", metrics.model_dump(mode="json"))
    successful = not trajectory.run_error and not trajectory.parse_error
    agent_label = (
        "Pre-RepoOps nanobot + GitHub MCP Server"
        if args.agent == "github-mcp"
        else "Pre-RepoOps nanobot"
    )
    summary = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "agent": agent_label,
        "runtime_commit": args.expected_runtime_commit,
        "models": [trajectory.model] if trajectory.model else [],
        "case_count": 1,
        "successful_cases": int(successful),
        "total_tool_calls": len(trajectory.tool_trace),
        "total_duration_ms": trajectory.duration_ms,
        "total_usage": trajectory.usage,
        "metrics": metrics.model_dump(mode="json"),
        "cases": [
            {
                "case_id": trajectory.case_id,
                "task_type": trajectory.task_type,
                "model": trajectory.model,
                "tool_calls": len(trajectory.tool_trace),
                "duration_ms": trajectory.duration_ms,
                "run_error": trajectory.run_error,
                "parse_error": trajectory.parse_error,
            }
        ],
    }
    _write_json(args.output_dir / "run_summary.json", summary)
    status = "ok" if successful else "failed"
    print(
        f"[{status}] {case.case_id}: {len(trajectory.tool_trace)} tools, "
        f"{trajectory.duration_ms / 1000:.1f}s",
        flush=True,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a task through the pre-RepoOps nanobot runtime"
    )
    parser.add_argument(
        "--agent",
        choices=("vanilla-nanobot", "github-mcp"),
        required=True,
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expected-runtime-commit", default=PRE_REPOOPS_COMMIT)
    parser.add_argument("--timeout", type=float, default=600.0)
    raise SystemExit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
