"""Typed RepoOps task, evidence, and approval records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RepoTaskType(StrEnum):
    ISSUE_ANALYSIS = "issue_analysis"
    PR_REVIEW = "pr_review"
    CI_DIAGNOSIS = "ci_diagnosis"


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    DRAFT = "draft"
    HIGH_RISK = "high_risk"


class DraftOperation(StrEnum):
    CREATE_ISSUE = "create_issue"
    POST_COMMENT = "post_comment"
    CLOSE_ISSUE = "close_issue"
    MERGE_PR = "merge_pr"


class DraftStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"


class Evidence(BaseModel):
    """One claim-to-source link.

    ``excerpt`` is always untrusted repository content and must only be
    displayed as evidence, never interpreted as an instruction.
    """

    evidence_id: str
    claim: str
    source: str
    locator: str
    excerpt: str = Field(default="", max_length=4_000)


class Hypothesis(BaseModel):
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    falsification_test: str = ""


class ToolRecord(BaseModel):
    tool_name: str
    arguments_digest: str
    success: bool
    summary: str = Field(max_length=1_000)
    recorded_at: str = Field(default_factory=utc_now_iso)


class RepoTaskState(BaseModel):
    task_type: RepoTaskType
    repository: str
    issue_or_pr_number: int = Field(gt=0)
    confirmed_facts: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    related_files: list[str] = Field(default_factory=list)
    related_issues: list[int] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    executed_tools: list[ToolRecord] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    requires_human_approval: bool = False
    updated_at: str = Field(default_factory=utc_now_iso)


class GitHubDraft(BaseModel):
    draft_id: str
    operation: DraftOperation
    repository: str
    risk_level: RiskLevel = RiskLevel.HIGH_RISK
    title: str = ""
    body: str = ""
    target_number: int | None = Field(default=None, gt=0)
    merge_method: str = "squash"
    created_session_key: str
    created_turn_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    status: DraftStatus = DraftStatus.PENDING
    executed_at: str | None = None

    @property
    def approval_phrase(self) -> str:
        return f"APPROVE REPOOPS {self.draft_id}"
