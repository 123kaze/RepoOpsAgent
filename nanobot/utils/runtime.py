"""Runtime-specific helper functions and constants."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from loguru import logger

from nanobot.utils.helpers import stringify_text_blocks

_MAX_REPEAT_EXTERNAL_LOOKUPS = 2
_MAX_REPO_FILE_READS_PER_PATH = 2

# Third same-target workspace violation in a turn escalates to "stop retrying".
_MAX_REPEAT_WORKSPACE_VIOLATIONS = 2
_LENGTH_RECOVERY_TAIL_CHARS = 64

EMPTY_FINAL_RESPONSE_MESSAGE = (
    "I completed the tool steps but couldn't produce a final answer. "
    "Please try again or narrow the task."
)

FINALIZATION_RETRY_PROMPT = (
    "Please provide your response to the user based on the conversation above."
)

BUDGET_EXHAUSTED_FINALIZATION_PROMPT = (
    "The tool-call budget for this turn is exhausted and no tools are available "
    "in this finalization call. Based only on the conversation and tool results "
    "above, answer the user's original request now. Preserve every output-format "
    "constraint from the original request (for example, JSON-only or a requested "
    "schema). Output only the final user-facing answer: do not emit or describe "
    "tool calls, DSML, XML tool tags, function names, or tool arguments. Do not "
    "claim the task is complete unless the evidence above clearly shows it is "
    "complete; represent missing information in the format the user requested."
)

TOOL_MARKUP_RECOVERY_PROMPT = (
    "Your previous finalization response still contained serialized tool-call markup. "
    "That response cannot be shown to the user, and tools remain unavailable. Rewrite it now "
    "as the final user-facing answer only. Preserve the original output schema exactly. Do not "
    "emit DSML, XML/function tags, tool names, invocations, parameters, or commentary about this "
    "retry."
)

LENGTH_RECOVERY_PROMPT = (
    "The previous assistant response was cut off. Continue the same response from its "
    "exact endpoint. Output only new continuation text in the same language and style. "
    "Do not acknowledge this instruction, restart the response, repeat its title or any "
    "existing text, recap, or apologize."
)

SUSTAINED_GOAL_CONTINUE_PROMPT = (
    "You have an active sustained goal. Please continue working toward the "
    "objective using your tools, or call update_goal with action='complete' "
    "if the work is truly finished."
)


def empty_tool_result_message(tool_name: str) -> str:
    """Short prompt-safe marker for tools that completed without visible output."""
    return f"({tool_name} completed with no output)"


def ensure_nonempty_tool_result(tool_name: str, content: Any) -> Any:
    """Replace semantically empty tool results with a short marker string."""
    if content is None:
        return empty_tool_result_message(tool_name)
    if isinstance(content, str) and not content.strip():
        return empty_tool_result_message(tool_name)
    if isinstance(content, list):
        if not content:
            return empty_tool_result_message(tool_name)
        text_payload = stringify_text_blocks(cast(list[Any], content))
        if text_payload is not None and not text_payload.strip():
            return empty_tool_result_message(tool_name)
    return cast(Any, content)


def is_blank_text(content: str | None) -> bool:
    """True when *content* is missing or only whitespace."""
    return content is None or not content.strip()


def build_finalization_retry_message() -> dict[str, str]:
    """A short no-tools-allowed prompt for final answer recovery."""
    return {"role": "user", "content": FINALIZATION_RETRY_PROMPT}


def build_budget_exhausted_finalization_message() -> dict[str, str]:
    """Prompt the model for a no-tools final response after budget exhaustion."""
    return {"role": "user", "content": BUDGET_EXHAUSTED_FINALIZATION_PROMPT}


def build_tool_markup_recovery_message() -> dict[str, str]:
    """Ask for one clean no-tools rewrite after serialized tool markup leaks."""
    return {"role": "user", "content": TOOL_MARKUP_RECOVERY_PROMPT}


def build_length_recovery_message(content: str) -> dict[str, str]:
    """Prompt the model to continue after hitting output token limit."""
    tail = content[-_LENGTH_RECOVERY_TAIL_CHARS:]
    prompt = (
        f"{LENGTH_RECOVERY_PROMPT}\n\n"
        "The following tail was already delivered to the user. Treat it as immutable "
        "context and do not output it again:\n"
        "<already_delivered_tail>\n"
        f"{tail}\n"
        "</already_delivered_tail>\n"
        "Begin with the text that belongs immediately after this tail."
    )
    return {"role": "user", "content": prompt}


def build_goal_continue_message(custom: str | None = None) -> dict[str, str]:
    """Prompt the model to continue when a sustained goal is still active."""
    return {"role": "user", "content": custom or SUSTAINED_GOAL_CONTINUE_PROMPT}


def external_lookup_signature(tool_name: str, arguments: Any) -> str | None:
    """Stable signature for repeated external lookups we want to throttle."""
    if not isinstance(arguments, dict):
        return None
    arguments = cast(dict[str, Any], arguments)
    if tool_name == "web_fetch":
        url = str(arguments.get("url") or "").strip()
        if url:
            return f"web_fetch:{url.lower()}"
    if tool_name == "web_search":
        query = str(arguments.get("query") or arguments.get("search_term") or "").strip()
        if query:
            return f"web_search:{query.lower()}"
    return None


def repeated_external_lookup_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Block repeated external lookups after a small retry budget."""
    signature = external_lookup_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_EXTERNAL_LOOKUPS:
        return None
    logger.warning(
        "Blocking repeated external lookup {} on attempt {}",
        signature[:160],
        count,
    )
    return (
        "Error: repeated external lookup blocked. "
        "Use the results you already have to answer, or try a meaningfully different source."
    )


def repeated_repo_file_read_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Stop an agent from panning through one repository file indefinitely.

    RepoOps can read up to 1,000 lines in one bounded call. Two ranges leave room for a focused
    follow-up while forcing subsequent investigation to use retrieval or another file in the
    call chain. This is scoped to the RepoOps tool so ordinary interactive file reads keep their
    existing behavior.
    """
    if tool_name != "repoops_read_file" or not isinstance(arguments, dict):
        return None
    params = cast(dict[str, Any], arguments)
    path = str(params.get("path") or "").strip()
    if not path:
        return None
    repository = str(params.get("repository") or "").strip().lower()
    ref = str(params.get("ref") or "HEAD").strip().lower()
    signature = f"{repository}:{ref}:{path}"
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPO_FILE_READS_PER_PATH:
        return None
    logger.warning("Blocking repeated RepoOps file read {} on attempt {}", signature, count)
    return (
        "Error: per-file RepoOps read budget exhausted. You already inspected two ranges from "
        f"'{path}'. Do not retry or pan through another range in this file. Use the existing "
        "evidence, search for a related caller/configuration/serialization file, or finalize."
    )


# Workspace-boundary violations are soft errors, with per-target throttling.

_OUTSIDE_PATH_PATTERN = re.compile(r"(?:^|[\s|>'\"])((?:/[^\s\"'>;|<]+)|(?:~[^\s\"'>;|<]+))")


def workspace_violation_signature(
    tool_name: str,
    arguments: Any,
) -> str | None:
    """Return a stable cross-tool signature for the outside-workspace target."""
    if not isinstance(arguments, dict):
        return None
    arguments = cast(dict[str, Any], arguments)
    for key in ("path", "file_path", "target", "source", "destination"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return _normalize_violation_target(val.strip())

    if tool_name in {"exec", "shell"}:
        cmd = str(arguments.get("command") or "").strip()
        if cmd:
            match = _OUTSIDE_PATH_PATTERN.search(cmd)
            if match:
                return _normalize_violation_target(match.group(1))
        cwd = str(arguments.get("working_dir") or "").strip()
        if cwd:
            return _normalize_violation_target(cwd)

    return None


def _normalize_violation_target(raw: str) -> str:
    """Normalize *raw* path so that equivalent spellings collide on the same key."""
    try:
        normalized = Path(raw).expanduser().resolve().as_posix()
    except Exception:
        normalized = raw.replace("\\", "/")
    return f"violation:{normalized}".lower()


def repeated_workspace_violation_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Return an escalated error after repeated bypass attempts."""
    signature = workspace_violation_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_WORKSPACE_VIOLATIONS:
        return None
    logger.warning(
        "Escalating repeated workspace bypass attempt {} (attempt {})",
        signature[:160],
        count,
    )
    target = signature.split("violation:", 1)[1] if "violation:" in signature else signature
    return (
        "Error: refusing repeated workspace-bypass attempts.\n"
        f"You have tried to access '{target}' (or an equivalent path) "
        f"{count} times in this turn. This is a hard policy boundary -- "
        "switching tools, shell tricks, working_dir overrides, symlinks, "
        "or base64 piping will NOT change the answer. Stop retrying. "
        "If the user genuinely needs this resource, tell them you cannot "
        "access it and ask how they want to proceed (e.g. copy the file "
        "into the workspace, or disable restrict_to_workspace for this run)."
    )
