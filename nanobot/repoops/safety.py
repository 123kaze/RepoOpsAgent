"""Repository authorization and two-turn approval gates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nanobot.repoops.models import GitHubDraft

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RepoOpsSafetyError(ValueError):
    """Raised when repository authorization or approval fails."""


@dataclass(frozen=True)
class RepoGuard:
    allowed_repositories: frozenset[str]

    @classmethod
    def from_config(cls, repositories: list[str]) -> RepoGuard:
        normalized: set[str] = set()
        for repository in repositories:
            value = repository.strip()
            if not _REPOSITORY_RE.fullmatch(value):
                raise RepoOpsSafetyError(
                    f"Invalid repository allowlist entry {repository!r}; expected owner/repo"
                )
            normalized.add(value.lower())
        return cls(allowed_repositories=frozenset(normalized))

    def require_allowed(self, repository: str) -> str:
        value = repository.strip()
        if not _REPOSITORY_RE.fullmatch(value):
            raise RepoOpsSafetyError(
                f"Invalid repository {repository!r}; expected owner/repo"
            )
        if value.lower() not in self.allowed_repositories:
            configured = ", ".join(sorted(self.allowed_repositories)) or "(none)"
            raise RepoOpsSafetyError(
                f"Repository {value!r} is not authorized. "
                f"Configure tools.repoops.allowedRepositories; current allowlist: {configured}"
            )
        return value


class ApprovalGate:
    """Require an exact approval line from the user in a later turn."""

    @staticmethod
    def require_approval(
        draft: GitHubDraft,
        *,
        user_text: str,
        session_key: str,
        turn_id: str,
    ) -> None:
        if session_key != draft.created_session_key:
            raise RepoOpsSafetyError(
                "Approval must come from the same user session that created the draft"
            )
        if turn_id == draft.created_turn_id:
            raise RepoOpsSafetyError("A draft cannot be approved in the turn that created it")

        expected = re.escape(draft.approval_phrase)
        if re.search(rf"(?m)^\s*{expected}\s*$", user_text) is None:
            raise RepoOpsSafetyError(
                "High-risk action not approved. The user must send this exact line in a new turn: "
                f"{draft.approval_phrase}"
            )
