"""Run one read-only benchmark case against a pre-RepoOps nanobot checkout.

This worker intentionally has no imports from ``nanobot.repoops`` so the caller can
put an older nanobot checkout first on ``PYTHONPATH``.  It writes the same observable
trajectory shape as the current benchmark; parsing and scoring happen in the current
checkout after the subprocess exits.
"""

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

import nanobot as nanobot_package
from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.tools.base import ToolResult
from nanobot.nanobot import Nanobot
from nanobot.providers.base import ToolCallRequest
from nanobot.sdk.types import StreamEvent

_SENSITIVE_KEY = re.compile(r"(?i)(api[_-]?key|authorization|cookie|password|secret|token)")
_SENSITIVE_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
        "Bearer [REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
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


class _TraceHook(AgentHook):
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


def _update_trace(
    traces: dict[str, dict[str, Any]],
    ordered_ids: list[str],
    event: StreamEvent,
) -> None:
    call_id = event.tool_call_id or f"missing-{len(ordered_ids) + 1}"
    arguments = _redact(event.arguments or {})
    if not isinstance(arguments, dict):
        arguments = str(arguments)
    if event.type == "tool.started":
        if call_id not in traces:
            traces[call_id] = {
                "sequence": len(ordered_ids) + 1,
                "iteration": event.iteration or 0,
                "tool_call_id": call_id,
                "name": event.name or "",
                "arguments": arguments,
                "status": "started",
                "output": "",
                "output_sha256": "",
                "error": "",
                "duration_ms": None,
            }
            ordered_ids.append(call_id)
        return
    if event.type not in {"tool.completed", "tool.failed"}:
        return
    if call_id not in traces:
        traces[call_id] = {
            "sequence": len(ordered_ids) + 1,
            "iteration": event.iteration or 0,
            "tool_call_id": call_id,
            "name": event.name or "",
            "arguments": arguments,
            "status": "started",
            "output": "",
            "output_sha256": "",
            "error": "",
            "duration_ms": None,
        }
        ordered_ids.append(call_id)
    traces[call_id]["status"] = "ok" if event.type == "tool.completed" else "error"
    traces[call_id]["error"] = _redact_text(event.error or "")


async def _run(args: argparse.Namespace) -> int:
    package_file = nanobot_package.__file__
    if not Path(package_file).resolve().is_relative_to(args.runtime_root.resolve()):
        raise RuntimeError("worker did not import nanobot from the requested runtime root")

    case = cast(dict[str, Any], json.loads(args.case_json))
    allowed = {"read_file", "list_dir", "find_files", "grep"}
    if args.agent == "vanilla-nanobot":
        allowed.add("exec")

    bot = Nanobot.from_config(args.config, workspace=args.workspace)
    for tool_name in list(bot._loop.tools.tool_names):  # pyright: ignore[reportPrivateUsage]
        if tool_name not in allowed:
            bot._loop.tools.unregister(tool_name)  # pyright: ignore[reportPrivateUsage]

    started_at = _utc_now_iso()
    wall_started = monotonic()
    trace_hook = _TraceHook()
    traces: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    model = ""
    final_answer = ""
    usage: dict[str, int] = {}
    stop_reason: str | None = None
    run_error = ""

    try:
        stream = await bot.run_streamed(
            str(case["benchmark_prompt"]),
            session_key=f"sdk:pre-repoops:{args.agent}:{case['case_id']}",
            ephemeral=True,
            hooks=[trace_hook],
        )

        async def _consume() -> None:
            nonlocal model
            async for event in stream.stream_events():
                if event.type == "run.started":
                    model = str(event.metadata.get("model") or "")
                _update_trace(traces, ordered_ids, event)

        await asyncio.wait_for(_consume(), timeout=args.timeout)
        result = await stream.wait()
        final_answer = _redact_text(result.content)
        usage = {str(key): int(value) for key, value in result.usage.items()}
        stop_reason = result.stop_reason
        run_error = _redact_text(result.error or "")
    except Exception as exc:
        run_error = _redact_text(f"{type(exc).__name__}: {exc}")
    finally:
        await bot.aclose()

    for call_id, (output, is_error, duration_ms) in trace_hook.outputs.items():
        trace = traces.get(call_id)
        if trace is None:
            continue
        output = _redact_text(output)
        trace["output"] = output
        trace["output_sha256"] = hashlib.sha256(output.encode()).hexdigest()
        trace["duration_ms"] = duration_ms
        if is_error:
            trace["status"] = "error"
            trace["error"] = output

    trajectory = {
        "schema_version": "1.0",
        "case_id": str(case["case_id"]),
        "task_type": "issue_analysis",
        "repository": str(case["repository"]),
        "number": int(case["number"]),
        "model": model,
        "started_at": started_at,
        "completed_at": _utc_now_iso(),
        "duration_ms": round((monotonic() - wall_started) * 1000),
        "source_url": str(case["source_url"]),
        "snapshot_sha": str(case["snapshot_sha"]),
        "prompt": _redact_text(str(case["benchmark_prompt"])),
        "tool_trace": [traces[call_id] for call_id in ordered_ids],
        "final_answer": final_answer,
        "parsed_answer": None,
        "parse_error": "",
        "usage": usage,
        "stop_reason": stop_reason,
        "run_error": run_error,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--agent",
        choices=("vanilla-nanobot", "github-mcp"),
        required=True,
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--case-json", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    raise SystemExit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
