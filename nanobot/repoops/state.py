"""Durable, workspace-contained RepoOps task and draft storage."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel

from nanobot.repoops.models import (
    DraftStatus,
    GitHubDraft,
    RepoTaskState,
    RepoTaskType,
    utc_now_iso,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_STATE_LOCK = threading.RLock()


class RepoOpsStateError(ValueError):
    """Raised for unsafe or invalid RepoOps persisted state."""


def _contained_root(workspace: Path, relative_dir: str) -> Path:
    relative = Path(relative_dir)
    if relative.is_absolute():
        raise RepoOpsStateError("RepoOps stateDir must be relative to the active workspace")
    workspace_root = workspace.expanduser().resolve()
    root = (workspace_root / relative).resolve()
    try:
        root.relative_to(workspace_root)
    except ValueError as exc:
        raise RepoOpsStateError("RepoOps stateDir escapes the active workspace") from exc
    return root


def _atomic_write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = model.model_dump_json(indent=2)
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_model(path: Path, model_cls: type[_ModelT]) -> _ModelT:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepoOpsStateError(f"Cannot read RepoOps state {path.name}: {exc}") from exc
    return model_cls.model_validate(payload)


def _repository_key(repository: str) -> str:
    return repository.lower().replace("/", "__")


class RepoTaskStore:
    def __init__(self, workspace: Path, state_dir: str = ".repoops") -> None:
        self.root = _contained_root(workspace, state_dir) / "tasks"
        self._lock = _STATE_LOCK

    def path_for(
        self,
        repository: str,
        task_type: RepoTaskType,
        number: int,
    ) -> Path:
        if number <= 0:
            raise RepoOpsStateError("Issue or PR number must be positive")
        return self.root / _repository_key(repository) / f"{task_type.value}-{number}.json"

    def load(
        self,
        repository: str,
        task_type: RepoTaskType,
        number: int,
    ) -> RepoTaskState | None:
        path = self.path_for(repository, task_type, number)
        with self._lock:
            if not path.exists():
                return None
            return _read_model(path, RepoTaskState)

    def save(self, state: RepoTaskState) -> RepoTaskState:
        state.updated_at = utc_now_iso()
        path = self.path_for(
            state.repository,
            state.task_type,
            state.issue_or_pr_number,
        )
        with self._lock:
            _atomic_write_model(path, state)
        return state

    def get_or_create(
        self,
        repository: str,
        task_type: RepoTaskType,
        number: int,
    ) -> RepoTaskState:
        state = self.load(repository, task_type, number)
        if state is not None:
            return state
        state = RepoTaskState(
            repository=repository,
            task_type=task_type,
            issue_or_pr_number=number,
        )
        return self.save(state)


class DraftStore:
    def __init__(self, workspace: Path, state_dir: str = ".repoops") -> None:
        self.root = _contained_root(workspace, state_dir) / "drafts"
        self._lock = _STATE_LOCK

    def path_for(self, draft_id: str) -> Path:
        if not re_fullmatch_draft_id(draft_id):
            raise RepoOpsStateError("Invalid RepoOps draft id")
        return self.root / f"{draft_id}.json"

    def save(self, draft: GitHubDraft) -> GitHubDraft:
        with self._lock:
            _atomic_write_model(self.path_for(draft.draft_id), draft)
        return draft

    def load(self, draft_id: str) -> GitHubDraft:
        path = self.path_for(draft_id)
        with self._lock:
            if not path.exists():
                raise RepoOpsStateError(f"RepoOps draft {draft_id!r} was not found")
            return _read_model(path, GitHubDraft)

    def claim_execution(self, draft_id: str) -> GitHubDraft:
        """Atomically move a pending draft to executing before network I/O."""
        with self._lock:
            draft = self.load(draft_id)
            if draft.status is not DraftStatus.PENDING:
                raise RepoOpsStateError(
                    f"RepoOps draft {draft.draft_id!r} is already {draft.status.value}"
                )
            draft.status = DraftStatus.EXECUTING
            return self.save(draft)

    def mark_executed(self, draft: GitHubDraft) -> GitHubDraft:
        if draft.status is not DraftStatus.EXECUTING:
            raise RepoOpsStateError(
                f"RepoOps draft {draft.draft_id!r} is not executing"
            )
        draft.status = DraftStatus.EXECUTED
        draft.executed_at = utc_now_iso()
        return self.save(draft)


def re_fullmatch_draft_id(value: str) -> bool:
    return len(value) == 12 and all(char in "0123456789abcdef" for char in value)
