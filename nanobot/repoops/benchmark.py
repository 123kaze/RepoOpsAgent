"""Run reproducible RepoOps Agent benchmarks and capture observable trajectories."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from json_repair import loads as repair_json
from pydantic import BaseModel, Field

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.tools.base import ToolResult
from nanobot.nanobot import Nanobot
from nanobot.providers.base import ToolCallRequest
from nanobot.repoops.evaluation import (
    EvalPrediction,
    EvalTask,
    EvalToolCall,
    evaluate,
    load_tasks,
)
from nanobot.sdk.types import StreamEvent

_CATEGORY_VALUES = (
    "bug",
    "feature",
    "documentation",
    "question",
    "configuration",
    "performance",
    "security",
    "insufficient-information",
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|password|secret|token)"
)
_SENSITIVE_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        "[REDACTED_API_KEY]",
    ),
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
)
_INVALID_CALL = re.compile(
    r"(?i)(unknown tool|missing required|unexpected parameter|"
    r"should be (?:string|integer|number|boolean|array|object)|"
    r"must be (?:one of|at least|at most|>=|<=)|invalid tool)"
    r"|tool .{0,120} not found"
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _redact_text(value: str) -> str:
    for pattern, replacement in _SENSITIVE_TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _redact(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _redact(item)
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in cast(list[object], value)]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _arguments_key(arguments: object) -> str:
    encoded = json.dumps(
        _redact(arguments),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


class BenchmarkClaim(BaseModel):
    claim: str
    evidence_ids: list[str] = Field(default_factory=list)


class BenchmarkEvidence(BaseModel):
    evidence_id: str
    source: str
    locator: str
    excerpt: str = ""


class BenchmarkAnswer(BaseModel):
    category: str
    files: list[str] = Field(default_factory=list)
    confirmed_facts: list[BenchmarkClaim] = Field(default_factory=list)
    hypotheses: list[BenchmarkClaim] = Field(default_factory=list)
    evidence: list[BenchmarkEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    approval_required: bool = False


class BenchmarkCase(BaseModel):
    case_id: str
    task_type: str
    repository: str
    number: int = Field(gt=0)
    title: str
    prompt: str
    source_url: str
    snapshot_sha: str
    run_id: int | None = Field(default=None, gt=0)
    job_id: int | None = Field(default=None, gt=0)
    reference_url: str = ""


class ToolTrace(BaseModel):
    sequence: int
    iteration: int
    tool_call_id: str
    name: str
    arguments: dict[str, Any] | str
    status: str = "started"
    output: str = ""
    output_sha256: str = ""
    error: str = ""
    duration_ms: int | None = None


class Trajectory(BaseModel):
    schema_version: str = "1.0"
    case_id: str
    task_type: str
    repository: str
    number: int
    model: str = ""
    started_at: str
    completed_at: str
    duration_ms: int
    source_url: str
    snapshot_sha: str
    prompt: str
    tool_trace: list[ToolTrace]
    final_answer: str
    parsed_answer: BenchmarkAnswer | None = None
    parse_error: str = ""
    usage: dict[str, int] = Field(default_factory=dict)
    stop_reason: str | None = None
    run_error: str = ""


class _TraceHook(AgentHook):
    """Capture raw bounded tool results without recording hidden model reasoning."""

    def __init__(self) -> None:
        super().__init__()
        self.outputs: dict[str, tuple[str, bool, int]] = {}
        self.started: dict[str, float] = {}

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        self.started[tool_call.id] = monotonic()

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        started = self.started.get(tool_call.id, monotonic())
        self.outputs[tool_call.id] = (
            "" if result is None else str(result),
            False,
            round((monotonic() - started) * 1000),
        )

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        started = self.started.get(tool_call.id, monotonic())
        self.outputs[tool_call.id] = (
            str(error),
            isinstance(error, ToolResult) and error.is_error,
            round((monotonic() - started) * 1000),
        )


def _extract_json_object(content: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", content):
        try:
            value, _ = decoder.raw_decode(content[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "category" in value:
            return cast(dict[str, Any], value)
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        repaired = repair_json(content[start : end + 1], skip_json_loads=True)
        if isinstance(repaired, dict) and "category" in repaired:
            return repaired
    raise ValueError("final answer did not contain a valid JSON object")


def parse_answer(content: str) -> BenchmarkAnswer:
    return BenchmarkAnswer.model_validate(_extract_json_object(content))


def _answer_contract() -> str:
    categories = "|".join(_CATEGORY_VALUES)
    return f"""
只调用当前已注册的 `repoops_*` 工具。读取文件必须调用 `repoops_read_file`；
`read_file`、`exec`、`grep` 和 `find_files` 在此 profile 中不可用。
完成工具调查并更新任务状态后，只输出一个 JSON 对象，不要使用 Markdown 代码块：
{{
  "category": "{categories}",
  "files": ["最多 5 个仓库相对路径，按相关度排序"],
  "confirmed_facts": [
    {{"claim": "只写证据直接支持的事实", "evidence_ids": ["E1"]}}
  ],
  "hypotheses": [
    {{"claim": "仍需验证的根因假设及置信度", "evidence_ids": ["E2"]}}
  ],
  "evidence": [
    {{
      "evidence_id": "E1",
      "source": "原样复制 URL 或仓库相对文件路径",
      "locator": "Issue/PR/CI 标识或行号范围",
      "excerpt": "从工具结果原样复制的短片段"
    }}
  ],
  "missing_information": ["尚未验证的信息"],
  "recommended_actions": ["下一步"],
  "approval_required": false
}}
不得查看或推断评测标准答案；不得调用 shell、通用文件或 web 工具绕过 repoops_*。
这是只读评测，不要创建草稿或执行任何 GitHub 写操作。
""".strip()


def _task_case(task: EvalTask) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=task.task_id,
        task_type="issue_analysis",
        repository=task.repository,
        number=task.issue_number,
        title=task.title,
        prompt=task.prompt,
        source_url=task.source_url,
        snapshot_sha=task.snapshot_sha,
        reference_url=task.reference_pr_url,
    )


def load_cases(path: Path) -> list[BenchmarkCase]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Case file must contain a JSON array")
    return [BenchmarkCase.model_validate(item) for item in cast(list[object], payload)]


def _build_prompt(case: BenchmarkCase) -> str:
    workflow = {
        "issue_analysis": "$repoops-issue-analysis",
        "pr_review": "$repoops-pr-review",
        "ci_diagnosis": "$repoops-ci-diagnosis",
    }.get(case.task_type, "$repoops")
    budgets = {
        "issue_analysis": """
效率约束（也是评测内容）：总计最多 8 次工具调用、最多 5 个工具批次。
批次 1 并行读取 Issue 和 task state；批次 2 并行做一次相似 Issue 搜索和一次
workspace 搜索（top_k <= 10）；随后最多读取 2 个精确文件范围；再更新一次 state；
最后立即输出 JSON。已有本地快照，不调用 repoops_search_code，不重复或平移同一文件的
行号窗口。信息不足时写入 missing_information，不要为追求完美继续搜索。
""",
        "pr_review": """
效率约束（也是评测内容）：总计最多 10 次工具调用、最多 5 个工具批次。先并行读取
PR、diff 和 task state；再读取 CI 状态，并用一次 workspace 搜索定位完整上下文；
最多补读 2 个精确文件范围；更新一次 state 后立即输出 JSON。不要重复查询。
""",
        "ci_diagnosis": f"""
效率约束（也是评测内容）：总计最多 8 次工具调用、最多 5 个工具批次。先并行读取
PR #{case.number} 的 CI 状态、run {case.run_id or 0} 的失败日志和 task state；
再用一次 workspace 搜索定位首个因果错误，最多补读 2 个精确文件范围；更新一次
state 后立即输出 JSON。state 最多保存 5 条事实、3 条证据和 3 个下一步，每条不超过
240 字；最终 JSON 控制在 2,000 字以内。若日志已给出精确文件、行号和 lint rule，
跳过 diff、search 和 read。必须优先解释首个因果错误，不追逐级联噪声。
""",
    }.get(case.task_type, "")
    return (
        f"使用 {workflow} 工作流。{case.prompt}\n"
        f"仓库：{case.repository}\n"
        f"对象编号：#{case.number}\n"
        f"本地代码快照：{case.snapshot_sha}\n\n"
        f"{budgets.strip()}\n\n"
        f"{_answer_contract()}"
    )


def _update_trace(
    traces_by_id: dict[str, ToolTrace],
    ordered_ids: list[str],
    event: StreamEvent,
) -> None:
    call_id = event.tool_call_id or f"missing-{len(ordered_ids) + 1}"
    redacted_arguments = _redact(event.arguments or {})
    arguments = (
        cast(dict[str, Any], redacted_arguments)
        if isinstance(redacted_arguments, dict)
        else str(redacted_arguments)
    )
    if event.type == "tool.started":
        if call_id not in traces_by_id:
            traces_by_id[call_id] = ToolTrace(
                sequence=len(ordered_ids) + 1,
                iteration=event.iteration or 0,
                tool_call_id=call_id,
                name=event.name or "",
                arguments=arguments,
            )
            ordered_ids.append(call_id)
        return
    if event.type not in {"tool.completed", "tool.failed"}:
        return
    trace = traces_by_id.get(call_id)
    if trace is None:
        trace = ToolTrace(
            sequence=len(ordered_ids) + 1,
            iteration=event.iteration or 0,
            tool_call_id=call_id,
            name=event.name or "",
            arguments=arguments,
        )
        traces_by_id[call_id] = trace
        ordered_ids.append(call_id)
    trace.status = "ok" if event.type == "tool.completed" else "error"
    trace.error = _redact_text(event.error or "")


async def run_case(
    bot: Nanobot,
    case: BenchmarkCase,
    *,
    timeout: float,
    session_namespace: str,
) -> Trajectory:
    started_at = _utc_now_iso()
    wall_started = monotonic()
    trace_hook = _TraceHook()
    traces_by_id: dict[str, ToolTrace] = {}
    ordered_ids: list[str] = []
    model = ""
    final_answer = ""
    usage: dict[str, int] = {}
    stop_reason: str | None = None
    run_error = ""

    try:
        stream = await bot.run_streamed(
            _build_prompt(case),
            session_key=f"sdk:repoops-benchmark:{session_namespace}:{case.case_id}",
            ephemeral=True,
            hooks=[trace_hook],
        )

        async def _consume() -> None:
            nonlocal model
            async for event in stream.stream_events():
                if event.type == "run.started":
                    model = str(event.metadata.get("model") or "")
                _update_trace(traces_by_id, ordered_ids, event)

        await asyncio.wait_for(_consume(), timeout=timeout)
        result = await stream.wait()
        final_answer = _redact_text(result.content)
        usage = dict(result.usage)
        stop_reason = result.stop_reason
        run_error = _redact_text(result.error or "")
    except Exception as exc:
        run_error = _redact_text(f"{type(exc).__name__}: {exc}")

    for call_id, (output, is_error, duration_ms) in trace_hook.outputs.items():
        trace = traces_by_id.get(call_id)
        if trace is None:
            continue
        output = _redact_text(output)
        trace.output = output
        trace.output_sha256 = hashlib.sha256(output.encode()).hexdigest()
        trace.duration_ms = duration_ms
        if is_error:
            trace.status = "error"
            trace.error = output

    parsed_answer: BenchmarkAnswer | None = None
    parse_error = ""
    if final_answer:
        try:
            parsed_answer = parse_answer(final_answer)
        except (ValueError, TypeError) as exc:
            parse_error = str(exc)
    elif not run_error:
        parse_error = "empty final answer"

    return Trajectory(
        case_id=case.case_id,
        task_type=case.task_type,
        repository=case.repository,
        number=case.number,
        model=model,
        started_at=started_at,
        completed_at=_utc_now_iso(),
        duration_ms=round((monotonic() - wall_started) * 1000),
        source_url=case.source_url,
        snapshot_sha=case.snapshot_sha,
        prompt=_redact_text(_build_prompt(case)),
        tool_trace=[traces_by_id[call_id] for call_id in ordered_ids],
        final_answer=final_answer,
        parsed_answer=parsed_answer,
        parse_error=parse_error,
        usage=usage,
        stop_reason=stop_reason,
        run_error=run_error,
    )


def _citation_supported(evidence: BenchmarkEvidence, tool_outputs: str) -> bool:
    source = evidence.source.strip()
    excerpt = " ".join(evidence.excerpt.split())
    normalized_outputs = " ".join(tool_outputs.split())
    if not source or source not in tool_outputs:
        return False
    if not excerpt or excerpt[:80] in normalized_outputs:
        return True
    excerpt_tokens = {
        token.lower()
        for token in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}|\d+",
            excerpt,
        )
    }
    output_tokens = {
        token.lower()
        for token in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}|\d+",
            normalized_outputs,
        )
    }
    if len(excerpt_tokens) < 3:
        return False
    return len(excerpt_tokens & output_tokens) / len(excerpt_tokens) >= 0.6


def trajectory_prediction(task: EvalTask, trajectory: Trajectory) -> EvalPrediction:
    answer = trajectory.parsed_answer
    calls = [
        EvalToolCall(name=trace.name, arguments_key=_arguments_key(trace.arguments))
        for trace in trajectory.tool_trace
    ]
    invalid_calls = sum(
        not isinstance(trace.arguments, dict)
        or (trace.status == "error" and bool(_INVALID_CALL.search(trace.error)))
        for trace in trajectory.tool_trace
    )
    if answer is None:
        return EvalPrediction(
            task_id=task.task_id,
            category="__invalid_output__",
            tool_calls=calls,
            invalid_tool_calls=invalid_calls,
        )

    claims = [*answer.confirmed_facts, *answer.hypotheses]
    evidence_by_id = {item.evidence_id: item for item in answer.evidence}
    cited_ids = [evidence_id for claim in claims for evidence_id in claim.evidence_ids]
    tool_outputs = "\n".join(trace.output for trace in trajectory.tool_trace)
    hallucinated = {
        evidence_id
        for evidence_id in cited_ids
        if evidence_id not in evidence_by_id
        or not _citation_supported(evidence_by_id[evidence_id], tool_outputs)
    }
    return EvalPrediction(
        task_id=task.task_id,
        category=answer.category,
        files=answer.files[:5],
        tool_calls=calls,
        invalid_tool_calls=invalid_calls,
        evidence_claims=len(claims),
        cited_claims=sum(bool(claim.evidence_ids) for claim in claims),
        citations=len(cited_ids),
        hallucinated_citations=sum(
            evidence_id in hallucinated for evidence_id in cited_ids
        ),
        approval_required=answer.approval_required,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def _run(args: argparse.Namespace) -> int:
    tasks: list[EvalTask] | None = None
    if args.tasks:
        tasks = load_tasks(args.tasks)
        cases = [_task_case(task) for task in tasks]
    else:
        cases = load_cases(args.cases)

    selected_ids = set(args.case_id or [])
    if selected_ids:
        cases = [case for case in cases if case.case_id in selected_ids]
        missing = sorted(selected_ids - {case.case_id for case in cases})
        if missing:
            raise ValueError(f"Unknown case IDs: {', '.join(missing)}")
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        raise ValueError("No benchmark cases selected")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_namespace = uuid4().hex[:12]
    bot = Nanobot.from_config(args.config, workspace=args.workspace)
    for tool_name in list(bot._loop.tools.tool_names):  # pyright: ignore[reportPrivateUsage]
        if not tool_name.startswith("repoops_"):
            bot._loop.tools.unregister(tool_name)  # pyright: ignore[reportPrivateUsage]
            continue
        tool = bot._loop.tools.get(tool_name)  # pyright: ignore[reportPrivateUsage]
        runtime = getattr(tool, "runtime", None)
        tool_config = getattr(runtime, "config", None)
        if tool_config is not None:
            tool_config.state_dir = f".repoops/benchmark/{run_namespace}"

    trajectories: list[Trajectory] = []
    try:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case.case_id}: {case.title}", flush=True)
            trajectory = await run_case(
                bot,
                case,
                timeout=args.timeout,
                session_namespace=run_namespace,
            )
            trajectories.append(trajectory)
            _write_json(
                args.output_dir / "trajectories" / f"{case.case_id}.json",
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
    finally:
        await bot.aclose()

    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": _utc_now_iso(),
        "agent": "RepoOps Agent",
        "models": sorted({item.model for item in trajectories if item.model}),
        "case_count": len(trajectories),
        "successful_cases": sum(
            not item.run_error and not item.parse_error for item in trajectories
        ),
        "total_tool_calls": sum(len(item.tool_trace) for item in trajectories),
        "total_usage": {
            key: sum(item.usage.get(key, 0) for item in trajectories)
            for key in sorted({key for item in trajectories for key in item.usage})
        },
        "cases": [
            {
                "case_id": item.case_id,
                "task_type": item.task_type,
                "model": item.model,
                "tool_calls": len(item.tool_trace),
                "duration_ms": item.duration_ms,
                "run_error": item.run_error,
                "parse_error": item.parse_error,
            }
            for item in trajectories
        ],
    }

    if tasks is not None:
        task_by_id = {task.task_id: task for task in tasks}
        selected_tasks = [task_by_id[item.case_id] for item in trajectories]
        predictions = [
            trajectory_prediction(task_by_id[item.case_id], item)
            for item in trajectories
        ]
        _write_json(
            args.output_dir / "predictions.json",
            [prediction.model_dump(mode="json") for prediction in predictions],
        )
        if len(selected_tasks) == len(predictions):
            metrics = evaluate(selected_tasks, predictions)
            _write_json(args.output_dir / "metrics.json", metrics.model_dump(mode="json"))
            summary["metrics"] = metrics.model_dump(mode="json")

    _write_json(args.output_dir / "run_summary.json", summary)
    return 0 if summary["successful_cases"] == summary["case_count"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run RepoOps Agent cases and save observable tool trajectories"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tasks", type=Path, help="Issue benchmark task JSON")
    source.add_argument("--cases", type=Path, help="Generic Issue/PR/CI case JSON")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
