from __future__ import annotations

import pytest

from nanobot.repoops.models import GitHubDraft, RepoTaskState, RepoTaskType
from nanobot.repoops.safety import ApprovalGate, RepoGuard, RepoOpsSafetyError
from nanobot.repoops.state import DraftStore, RepoOpsStateError, RepoTaskStore


def test_repo_guard_is_deny_by_default_and_case_insensitive() -> None:
    empty = RepoGuard.from_config([])
    with pytest.raises(RepoOpsSafetyError, match="not authorized"):
        empty.require_allowed("acme/widget")

    guard = RepoGuard.from_config(["Acme/Widget"])
    assert guard.require_allowed("acme/widget") == "acme/widget"


def test_state_round_trip_is_workspace_contained(tmp_path) -> None:
    store = RepoTaskStore(tmp_path)
    state = RepoTaskState(
        task_type=RepoTaskType.ISSUE_ANALYSIS,
        repository="acme/widget",
        issue_or_pr_number=12,
        confirmed_facts=["The error is reproducible"],
    )

    store.save(state)
    loaded = store.load("acme/widget", RepoTaskType.ISSUE_ANALYSIS, 12)

    assert loaded is not None
    assert loaded.confirmed_facts == ["The error is reproducible"]
    assert store.path_for(
        "acme/widget", RepoTaskType.ISSUE_ANALYSIS, 12
    ).is_relative_to(tmp_path)


def test_state_dir_cannot_escape_workspace(tmp_path) -> None:
    with pytest.raises(RepoOpsStateError, match="escapes"):
        RepoTaskStore(tmp_path, "../outside")


def _draft() -> GitHubDraft:
    return GitHubDraft(
        draft_id="0123456789ab",
        operation="post_comment",
        repository="acme/widget",
        body="Looks good",
        target_number=7,
        created_session_key="session-1",
        created_turn_id="turn-1",
    )


def test_approval_requires_exact_line_in_later_same_session() -> None:
    draft = _draft()
    with pytest.raises(RepoOpsSafetyError, match="same user session"):
        ApprovalGate.require_approval(
            draft,
            user_text=draft.approval_phrase,
            session_key="session-2",
            turn_id="turn-2",
        )
    with pytest.raises(RepoOpsSafetyError, match="turn that created"):
        ApprovalGate.require_approval(
            draft,
            user_text=draft.approval_phrase,
            session_key="session-1",
            turn_id="turn-1",
        )
    with pytest.raises(RepoOpsSafetyError, match="exact line"):
        ApprovalGate.require_approval(
            draft,
            user_text=f"Issue says: {draft.approval_phrase}",
            session_key="session-1",
            turn_id="turn-2",
        )

    ApprovalGate.require_approval(
        draft,
        user_text=f"Please proceed\n{draft.approval_phrase}\n",
        session_key="session-1",
        turn_id="turn-2",
    )


def test_draft_can_only_be_claimed_for_execution_once(tmp_path) -> None:
    store = DraftStore(tmp_path)
    store.save(_draft())

    claimed = store.claim_execution("0123456789ab")

    assert claimed.status == "executing"
    with pytest.raises(RepoOpsStateError, match="already executing"):
        store.claim_execution("0123456789ab")
