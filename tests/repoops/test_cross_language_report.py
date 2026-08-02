import json
from pathlib import Path

from nanobot.repoops.benchmark import BenchmarkAnswer, Trajectory
from nanobot.repoops.cross_language_report import build_report, summarize_run
from nanobot.repoops.evaluation import EvalPrediction, EvalTask


def test_summarize_run_groups_success_and_usage(tmp_path: Path) -> None:
    task = EvalTask(
        task_id="go-issue-1",
        repository="owner/go-repo",
        language="Go",
        issue_number=1,
        prompt="Analyze",
        expected_category="bug",
        relevant_files=["main.go"],
        expected_tools=[],
        expected_behavior="report_with_evidence",
        snapshot_sha="a" * 40,
    )
    prediction = EvalPrediction(task_id=task.task_id, category="bug", files=["main.go"])
    trajectory = Trajectory(
        case_id=task.task_id,
        task_type="issue_analysis",
        repository=task.repository,
        number=1,
        model="deepseek-v4-pro",
        started_at="2026-08-01T00:00:00+00:00",
        completed_at="2026-08-01T00:00:01+00:00",
        duration_ms=1000,
        source_url="https://github.com/owner/go-repo/issues/1",
        snapshot_sha="a" * 40,
        prompt="Analyze",
        tool_trace=[],
        final_answer='{"category":"bug"}',
        parsed_answer=BenchmarkAnswer(category="bug", files=["main.go"]),
        usage={"total_tokens": 123},
    )
    (tmp_path / "trajectories").mkdir()
    (tmp_path / "trajectories" / "go-issue-1.json").write_text(
        trajectory.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / "predictions.json").write_text(
        json.dumps([prediction.model_dump(mode="json")]), encoding="utf-8"
    )
    (tmp_path / "run_summary.json").write_text(
        json.dumps({"agent": "RepoOps Agent", "models": ["deepseek-v4-pro"]}),
        encoding="utf-8",
    )

    summary = summarize_run([task], tmp_path)

    assert summary["overall"]["structured_successes"] == 1
    assert summary["overall"]["metrics"]["classification_accuracy"] == 1.0
    assert summary["overall"]["total_usage"]["total_tokens"] == 123
    assert summary["by_language"]["Go"]["task_count"] == 1
    assert summary["provenance"] == {}


def test_build_report_adds_optional_generic_baselines(tmp_path: Path) -> None:
    task = EvalTask(
        task_id="go-issue-1",
        repository="owner/go-repo",
        language="Go",
        issue_number=1,
        prompt="Analyze",
        expected_category="bug",
        relevant_files=["main.go"],
        expected_tools=[],
        expected_behavior="report_with_evidence",
        snapshot_sha="a" * 40,
    )
    prediction = EvalPrediction(task_id=task.task_id, category="bug", files=["main.go"])
    trajectory = Trajectory(
        case_id=task.task_id,
        task_type="issue_analysis",
        repository=task.repository,
        number=1,
        model="deepseek-v4-pro",
        started_at="2026-08-01T00:00:00+00:00",
        completed_at="2026-08-01T00:00:01+00:00",
        duration_ms=1000,
        source_url="https://github.com/owner/go-repo/issues/1",
        snapshot_sha="a" * 40,
        prompt="Analyze",
        tool_trace=[],
        final_answer='{"category":"bug"}',
        parsed_answer=BenchmarkAnswer(category="bug", files=["main.go"]),
    )
    for name in ("repoops", "claude", "vanilla", "mcp"):
        run_dir = tmp_path / name
        (run_dir / "trajectories").mkdir(parents=True)
        (run_dir / "trajectories" / "go-issue-1.json").write_text(
            trajectory.model_dump_json(), encoding="utf-8"
        )
        (run_dir / "predictions.json").write_text(
            json.dumps([prediction.model_dump(mode="json")]), encoding="utf-8"
        )
        (run_dir / "run_summary.json").write_text(
            json.dumps({"agent": name, "models": ["deepseek-v4-pro"]}),
            encoding="utf-8",
        )

    report = build_report(
        [task],
        tmp_path / "repoops",
        tmp_path / "claude",
        vanilla_run_dir=tmp_path / "vanilla",
        github_mcp_run_dir=tmp_path / "mcp",
    )

    assert set(report["agents"]) == {
        "repoops",
        "claude_code",
        "pre_repoops_nanobot",
        "pre_repoops_nanobot_github_mcp",
    }
