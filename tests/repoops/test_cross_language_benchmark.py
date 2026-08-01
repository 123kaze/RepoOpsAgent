from argparse import Namespace
from pathlib import Path

import pytest

from nanobot.repoops.cross_language_benchmark import (
    PreparedTask,
    benchmark_command,
    repository_slug,
)
from nanobot.repoops.evaluation import EvalTask


def _task() -> EvalTask:
    return EvalTask(
        task_id="cobra-issue-1",
        repository="spf13/cobra",
        language="Go",
        issue_number=1,
        prompt="Analyze",
        expected_category="bug",
        relevant_files=["command.go"],
        expected_tools=["repoops_get_issue"],
        expected_behavior="report_with_evidence",
        snapshot_sha="a" * 40,
    )


def _args(agent: str) -> Namespace:
    return Namespace(
        agent=agent,
        tasks=Path("tasks.json"),
        timeout=600,
        config=Path("config.json"),
        settings=Path("settings.json"),
        executable="claude",
        model="deepseek-v4-pro",
        effort="max",
        max_budget_usd=2.0,
    )


def test_repository_slug_rejects_non_github_shape() -> None:
    assert repository_slug("spf13/cobra") == "spf13__cobra"
    with pytest.raises(ValueError, match="Invalid GitHub repository"):
        repository_slug("https://example.com/repo")


def test_repoops_command_uses_one_task_and_its_worktree(tmp_path: Path) -> None:
    prepared = PreparedTask(_task(), tmp_path / "workspace", tmp_path / "shard")
    command = benchmark_command(_args("repoops"), prepared)

    assert "nanobot.repoops.benchmark" in command
    assert command[command.index("--case-id") + 1] == "cobra-issue-1"
    assert command[command.index("--workspace") + 1] == str(tmp_path / "workspace")
    assert command[command.index("--config") + 1] == "config.json"


def test_claude_command_uses_same_task_and_worktree(tmp_path: Path) -> None:
    prepared = PreparedTask(_task(), tmp_path / "workspace", tmp_path / "shard")
    command = benchmark_command(_args("claude-code"), prepared)

    assert "nanobot.repoops.claude_benchmark" in command
    assert command[command.index("--case-id") + 1] == "cobra-issue-1"
    assert command[command.index("--model") + 1] == "deepseek-v4-pro"
    assert command[command.index("--settings") + 1] == "settings.json"
