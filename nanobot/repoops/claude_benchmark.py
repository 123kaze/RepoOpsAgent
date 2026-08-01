"""Run the historical Issue benchmark through a local Claude Code CLI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from time import monotonic
from typing import Any, cast

from nanobot.repoops.benchmark import (
    BenchmarkAnswer,
    BenchmarkCase,
    ToolTrace,
    Trajectory,
    _arguments_key,  # pyright: ignore[reportPrivateUsage]
    _redact,  # pyright: ignore[reportPrivateUsage]
    _redact_text,  # pyright: ignore[reportPrivateUsage]
    _task_case,  # pyright: ignore[reportPrivateUsage]
    _utc_now_iso,  # pyright: ignore[reportPrivateUsage]
    _write_json,  # pyright: ignore[reportPrivateUsage]
    parse_answer,
    trajectory_prediction,
)
from nanobot.repoops.evaluation import EvalPrediction, EvalTask, EvalToolCall, evaluate, load_tasks

_CATEGORIES = [
    "bug",
    "feature",
    "documentation",
    "question",
    "configuration",
    "performance",
    "security",
    "insufficient-information",
]
_STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
_ISSUE_VIEW = re.compile(r"\bgh\s+issue\s+view\b")
_ISSUE_SEARCH = re.compile(r"\bgh\s+(?:issue\s+list|search\s+issues)\b")
_WORKSPACE_SEARCH = re.compile(r"\b(?:rg|grep|find|git\s+grep)\b")


def answer_json_schema() -> dict[str, Any]:
    schema = BenchmarkAnswer.model_json_schema()
    category = cast(dict[str, Any], schema["properties"]["category"])
    category["enum"] = list(_CATEGORIES)
    return schema


def _claude_prompt(case: BenchmarkCase) -> str:
    return f"""
你是只读的 GitHub Issue 分析 Agent。分析真实历史 Issue，判断分类，定位当前固定代码
快照中最相关的实现文件，并严格区分证据支持的事实与尚待验证的假设。

任务：{case.prompt}
仓库：{case.repository}
Issue：#{case.number}
本地代码快照：{case.snapshot_sha}
工作目录已经固定在该快照。

只允许以下调查方式：
1. 用一次 `gh issue view {case.number} --repo {case.repository} --json number,title,body,state,labels,comments,url`
   获取 Issue；确有必要时再用一次只读的 `gh issue list` 或 `gh search issues`。
2. 用 Read 读取文件；Bash 只可执行只读的 rg/grep/find 搜索以及上述 gh issue 命令。
   不要执行会写文件的 shell 命令，不要调用 git，不要访问其他仓库。
3. 不要编辑、写入或删除文件，不要启动子 Agent。

效率约束：调查工具总计最多 8 次。先读取 Issue，再并行做本地搜索；最多读取 3 个精确
文件范围。信息不足时写入 missing_information，不要继续泛搜。

最终结果必须满足提供的 JSON Schema。files 最多 5 个仓库相对路径并按相关度排序。
confirmed_facts 和 hypotheses 的每条 claim 都要引用 evidence_ids。evidence.source 必须是
工具输出中出现过的原始 Issue URL 或仓库相对文件路径；excerpt 必须原样摘自工具输出。
这是只读分析，approval_required 必须为 false。不要查看或推断评测标准答案。
""".strip()


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return "" if value is None else str(value)
    parts: list[str] = []
    for raw in cast(list[object], value):
        if isinstance(raw, dict):
            block = cast(dict[str, Any], raw)
            text = block.get("text") or block.get("content")
            if isinstance(text, str):
                parts.append(text)
        elif isinstance(raw, str):
            parts.append(raw)
    return "\n".join(parts)


def _parse_stream_events(
    events: list[dict[str, Any]],
) -> tuple[str, list[ToolTrace], dict[str, Any], dict[str, int], str, str]:
    model = ""
    ordered_ids: list[str] = []
    traces: dict[str, ToolTrace] = {}
    result_payload: dict[str, Any] = {}

    for event in events:
        event_type = event.get("type")
        if event_type == "system" and event.get("subtype") == "init":
            model = str(event.get("model") or model)
            continue
        if event_type == "assistant":
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            message_data = cast(dict[str, Any], message)
            model = str(message_data.get("model") or model)
            content = message_data.get("content")
            if not isinstance(content, list):
                continue
            for raw_block in cast(list[object], content):
                if not isinstance(raw_block, dict):
                    continue
                block = cast(dict[str, Any], raw_block)
                if block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                if name == _STRUCTURED_OUTPUT_TOOL:
                    continue
                call_id = str(block.get("id") or f"missing-{len(ordered_ids) + 1}")
                if call_id in traces:
                    continue
                redacted = _redact(block.get("input") or {})
                arguments = (
                    cast(dict[str, Any], redacted)
                    if isinstance(redacted, dict)
                    else str(redacted)
                )
                traces[call_id] = ToolTrace(
                    sequence=len(ordered_ids) + 1,
                    iteration=len(ordered_ids),
                    tool_call_id=call_id,
                    name=name,
                    arguments=arguments,
                )
                ordered_ids.append(call_id)
            continue
        if event_type == "user":
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = cast(dict[str, Any], message).get("content")
            if not isinstance(content, list):
                continue
            for raw_block in cast(list[object], content):
                if not isinstance(raw_block, dict):
                    continue
                block = cast(dict[str, Any], raw_block)
                if block.get("type") != "tool_result":
                    continue
                call_id = str(block.get("tool_use_id") or "")
                trace = traces.get(call_id)
                if trace is None:
                    continue
                output = _redact_text(_content_text(block.get("content")))
                trace.output = output
                trace.output_sha256 = hashlib.sha256(output.encode()).hexdigest()
                is_error = bool(block.get("is_error"))
                trace.status = "error" if is_error else "ok"
                trace.error = output if is_error else ""
            continue
        if event_type == "result":
            result_payload = event

    structured = result_payload.get("structured_output")
    if isinstance(structured, dict):
        final_answer = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    else:
        final_answer = str(result_payload.get("result") or "")

    usage_raw = result_payload.get("usage")
    usage_data = cast(dict[str, Any], usage_raw) if isinstance(usage_raw, dict) else {}
    prompt_tokens = int(usage_data.get("input_tokens") or 0)
    completion_tokens = int(usage_data.get("output_tokens") or 0)
    cached_tokens = int(usage_data.get("cache_read_input_tokens") or 0)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": prompt_tokens + completion_tokens + cached_tokens,
    }
    run_error = ""
    if result_payload.get("is_error"):
        run_error = str(result_payload.get("result") or result_payload.get("subtype") or "error")
    return (
        model,
        [traces[call_id] for call_id in ordered_ids],
        result_payload,
        usage,
        _redact_text(final_answer),
        _redact_text(run_error),
    )


def _normalized_tool_name(trace: ToolTrace) -> str:
    if trace.name == "Read":
        return "repoops_read_file"
    if trace.name in {"Grep", "Glob"}:
        return "repoops_search_workspace"
    if trace.name == "Bash":
        command = ""
        if isinstance(trace.arguments, dict):
            command = str(trace.arguments.get("command") or "")
        if _ISSUE_VIEW.search(command):
            return "repoops_get_issue"
        if _ISSUE_SEARCH.search(command):
            return "repoops_search_issues"
        if _WORKSPACE_SEARCH.search(command):
            return "repoops_search_workspace"
    return f"claude_code:{trace.name}"


def claude_trajectory_prediction(task: EvalTask, trajectory: Trajectory) -> EvalPrediction:
    prediction = trajectory_prediction(task, trajectory)
    prediction.tool_calls = [
        EvalToolCall(
            name=_normalized_tool_name(trace),
            arguments_key=_arguments_key(trace.arguments),
        )
        for trace in trajectory.tool_trace
    ]
    prediction.invalid_tool_calls = sum(trace.status == "error" for trace in trajectory.tool_trace)
    return prediction


async def run_case(
    executable: str,
    settings: Path,
    workspace: Path,
    case: BenchmarkCase,
    *,
    model: str,
    effort: str,
    timeout: float,
    max_budget_usd: float,
) -> Trajectory:
    started_at = _utc_now_iso()
    wall_started = monotonic()
    schema = json.dumps(answer_json_schema(), ensure_ascii=False, separators=(",", ":"))
    command = [
        executable,
        "-p",
        "--bare",
        "--settings",
        str(settings),
        "--model",
        model,
        "--effort",
        effort,
        "--tools",
        "Bash,Read",
        "--allowedTools",
        "Read",
        "Bash",
        "Bash(gh issue view *)",
        "Bash(gh issue list *)",
        "Bash(gh search issues *)",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        schema,
        "--max-budget-usd",
        str(max_budget_usd),
        _claude_prompt(case),
    ]
    events: list[dict[str, Any]] = []
    stderr_text = ""
    exit_code = -1
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
        exit_code = int(process.returncode or 0)
        stderr_text = stderr_bytes.decode(errors="replace")
        for line in stdout_bytes.decode(errors="replace").splitlines():
            try:
                payload: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(cast(dict[str, Any], payload))
    except TimeoutError:
        if process is not None:
            process.kill()
            await process.wait()
        stderr_text = f"Claude Code timed out after {timeout:.0f}s"

    model_name, traces, result_payload, usage, final_answer, run_error = _parse_stream_events(
        events
    )
    if not run_error and exit_code != 0:
        run_error = stderr_text.strip() or f"Claude Code exited with status {exit_code}"
    if not run_error and not result_payload:
        run_error = stderr_text.strip() or "Claude Code did not emit a result event"
    run_error = _redact_text(run_error)

    parsed_answer: BenchmarkAnswer | None = None
    parse_error = ""
    if final_answer:
        try:
            parsed_answer = parse_answer(final_answer)
        except (TypeError, ValueError) as exc:
            parse_error = str(exc)
    elif not run_error:
        parse_error = "empty final answer"

    for trace in traces:
        trace.duration_ms = None
    return Trajectory(
        case_id=case.case_id,
        task_type=case.task_type,
        repository=case.repository,
        number=case.number,
        model=model_name or model,
        started_at=started_at,
        completed_at=_utc_now_iso(),
        duration_ms=round((monotonic() - wall_started) * 1000),
        source_url=case.source_url,
        snapshot_sha=case.snapshot_sha,
        prompt=_redact_text(_claude_prompt(case)),
        tool_trace=traces,
        final_answer=final_answer,
        parsed_answer=parsed_answer,
        parse_error=parse_error,
        usage=usage,
        stop_reason=str(result_payload.get("subtype") or "") or None,
        run_error=run_error,
    )


async def _run(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks)
    selected_ids = set(args.case_id or [])
    if selected_ids:
        tasks = [task for task in tasks if task.task_id in selected_ids]
        missing = sorted(selected_ids - {task.task_id for task in tasks})
        if missing:
            raise ValueError(f"Unknown case IDs: {', '.join(missing)}")
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        raise ValueError("No benchmark tasks selected")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    trajectories: list[Trajectory] = []
    for index, task in enumerate(tasks, start=1):
        print(f"[{index}/{len(tasks)}] {task.task_id}: {task.title}", flush=True)
        trajectory = await run_case(
            args.executable,
            args.settings,
            args.workspace,
            _task_case(task),
            model=args.model,
            effort=args.effort,
            timeout=args.timeout,
            max_budget_usd=args.max_budget_usd,
        )
        trajectories.append(trajectory)
        _write_json(
            args.output_dir / "trajectories" / f"{task.task_id}.json",
            trajectory.model_dump(mode="json"),
        )
        status = "ok" if not trajectory.run_error and not trajectory.parse_error else "failed"
        print(
            f"  {status}: {len(trajectory.tool_trace)} tools, "
            f"{trajectory.duration_ms / 1000:.1f}s",
            flush=True,
        )
        if args.fail_fast and status == "failed":
            break

    task_by_id = {task.task_id: task for task in tasks}
    completed_tasks = [task_by_id[item.case_id] for item in trajectories]
    predictions = [
        claude_trajectory_prediction(task_by_id[item.case_id], item)
        for item in trajectories
    ]
    metrics = evaluate(completed_tasks, predictions)
    _write_json(
        args.output_dir / "predictions.json",
        [prediction.model_dump(mode="json") for prediction in predictions],
    )
    _write_json(args.output_dir / "metrics.json", metrics.model_dump(mode="json"))
    summary = {
        "schema_version": "1.0",
        "generated_at": _utc_now_iso(),
        "agent": "Claude Code",
        "models": sorted({item.model for item in trajectories if item.model}),
        "case_count": len(trajectories),
        "successful_cases": sum(
            not item.run_error and not item.parse_error for item in trajectories
        ),
        "total_tool_calls": sum(len(item.tool_trace) for item in trajectories),
        "total_duration_ms": sum(item.duration_ms for item in trajectories),
        "total_usage": {
            key: sum(item.usage.get(key, 0) for item in trajectories)
            for key in sorted({key for item in trajectories for key in item.usage})
        },
        "metrics": metrics.model_dump(mode="json"),
        "cases": [
            {
                "case_id": item.case_id,
                "model": item.model,
                "tool_calls": len(item.tool_trace),
                "duration_ms": item.duration_ms,
                "run_error": item.run_error,
                "parse_error": item.parse_error,
            }
            for item in trajectories
        ],
    }
    _write_json(args.output_dir / "run_summary.json", summary)
    return 0 if summary["successful_cases"] == summary["case_count"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the RepoOps Issue benchmark through local Claude Code"
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude/settings.json")
    parser.add_argument("--executable", default="claude")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--effort", default="max")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
