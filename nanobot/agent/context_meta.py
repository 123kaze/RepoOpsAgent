"""Persisted metadata for cache-stable session context."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict, cast

from nanobot.utils.helpers import estimate_message_tokens

SESSION_CONTEXT_SNAPSHOT_META = "_context_snapshot"
ARCHIVED_CONTEXTS_META = "_archived_contexts"
LOADED_SKILL_SNAPSHOTS_META = "_loaded_skill_snapshots"
CONTEXT_SNAPSHOT_VERSION = 1
CONTEXT_META_MESSAGE_KEY = "context_meta"

MAX_ARCHIVED_CONTEXTS = 8
MAX_ARCHIVED_CONTEXT_TOKENS = 8_000


class SessionContextSnapshot(TypedDict):
    """Validated first-party shape persisted under ``_context_snapshot``."""

    version: int
    system_prompt: str
    system_sha256: str
    memory_snapshot: str
    memory_sha256: str
    recent_history_snapshot: str
    recent_history_sha256: str
    tool_definitions: list[dict[str, Any]]
    tools_sha256: str


class ArchivedContextEntry(TypedDict):
    id: str
    text: str
    last_active: NotRequired[str]


def context_meta_data(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return validated framework metadata attached to a model message."""
    raw_meta = message.get("_meta")
    if not isinstance(raw_meta, Mapping):
        return None
    context_meta = cast(Mapping[str, Any], raw_meta).get(CONTEXT_META_MESSAGE_KEY)
    return cast(Mapping[str, Any], context_meta) if isinstance(context_meta, Mapping) else None


def is_context_meta_message(message: Mapping[str, Any]) -> bool:
    """Whether a user-role message carries framework context rather than user input."""
    context_meta = context_meta_data(message)
    return context_meta is not None and context_meta.get("isMeta") is True


def archived_context_message(
    text: str,
    *,
    last_active: str | None = None,
) -> dict[str, Any]:
    """Build a persisted, model-visible but UI-hidden archive message."""
    normalized = text.strip()
    archive_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    message: dict[str, Any] = {
        "role": "user",
        "content": (
            f'<archived_context id="{archive_id}">\n'
            f"{normalized}\n"
            "</archived_context>"
        ),
        "_meta": {
            CONTEXT_META_MESSAGE_KEY: {
                "isMeta": True,
                "kind": "archived_context",
                "archive_id": archive_id,
            }
        },
        "_hidden_history": {
            "kind": "archived_context",
            "archive_id": archive_id,
        },
    }
    if last_active:
        message["timestamp"] = last_active
    return message


def is_archived_context_message(message: Mapping[str, Any]) -> bool:
    marker = message.get("_hidden_history")
    if (
        isinstance(marker, Mapping)
        and cast(Mapping[str, Any], marker).get("kind") == "archived_context"
    ):
        return True
    content = message.get("content")
    return isinstance(content, str) and content.startswith("<archived_context ")


def archived_context_entries(metadata: Mapping[str, Any] | None) -> list[ArchivedContextEntry]:
    """Return validated append-only archive summaries from session metadata."""
    if not isinstance(metadata, Mapping):
        return []
    raw = metadata.get(ARCHIVED_CONTEXTS_META)
    if not isinstance(raw, list):
        return []
    entries: list[ArchivedContextEntry] = []
    for item in cast(list[object], raw):
        if not isinstance(item, Mapping):
            continue
        item_data = cast(Mapping[str, Any], item)
        text = item_data.get("text")
        archive_id = item_data.get("id")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(archive_id, str) or not archive_id.strip():
            archive_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        entry: ArchivedContextEntry = {"id": archive_id, "text": text}
        last_active = item_data.get("last_active")
        if isinstance(last_active, str) and last_active:
            entry["last_active"] = last_active
        entries.append(entry)
    return entries


def append_archived_context(
    metadata: dict[str, Any],
    text: str,
    *,
    last_active: str | None = None,
) -> bool:
    """Append one stable summary and prune only at the configured batch boundary."""
    normalized = text.strip()
    if not normalized or normalized == "(nothing)":
        return False
    archive_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    entries = archived_context_entries(metadata)
    if any(entry["id"] == archive_id for entry in entries):
        return False
    entry: ArchivedContextEntry = {"id": archive_id, "text": normalized}
    if last_active:
        entry["last_active"] = last_active
    entries.append(entry)

    # Archive summaries are compacted only when this bounded ledger crosses a
    # threshold. Detailed, older summaries remain recoverable in history.jsonl.
    while len(entries) > MAX_ARCHIVED_CONTEXTS or _archive_tokens(entries) > MAX_ARCHIVED_CONTEXT_TOKENS:
        entries.pop(0)
    metadata[ARCHIVED_CONTEXTS_META] = entries
    return True


def _archive_tokens(entries: list[ArchivedContextEntry]) -> int:
    return sum(
        estimate_message_tokens({"role": "user", "content": entry["text"]})
        for entry in entries
    )
