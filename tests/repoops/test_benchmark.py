from __future__ import annotations

import pytest

from nanobot.repoops.benchmark import (
    BenchmarkAnswer,
    BenchmarkCase,
    BenchmarkClaim,
    BenchmarkEvidence,
    ToolTrace,
    Trajectory,
    _benchmark_snapshot,
    _build_prompt,
    _redact,
    _redact_text,
    parse_answer,
    trajectory_prediction,
)
from nanobot.repoops.benchmark_merge import merge_runs
from nanobot.repoops.evaluation import EvalTask


def _task() -> EvalTask:
    return EvalTask(
        task_id="issue-1",
        repository="owner/repo",
        issue_number=1,
        prompt="Analyze",
        expected_category="bug",
        relevant_files=["nanobot/agent/runner.py"],
        expected_tools=["repoops_get_issue", "repoops_search_workspace"],
        expected_behavior="report_with_evidence",
    )


def _trajectory(answer: BenchmarkAnswer | None) -> Trajectory:
    return Trajectory(
        case_id="issue-1",
        task_type="issue_analysis",
        repository="owner/repo",
        number=1,
        model="deepseek-v4-pro",
        started_at="2026-07-31T00:00:00+00:00",
        completed_at="2026-07-31T00:00:01+00:00",
        duration_ms=1000,
        source_url="https://github.com/owner/repo/issues/1",
        snapshot_sha="a" * 40,
        prompt="Analyze",
        tool_trace=[
            ToolTrace(
                sequence=1,
                iteration=1,
                tool_call_id="call-1",
                name="repoops_get_issue",
                arguments={"repository": "owner/repo", "issue_number": 1},
                status="ok",
                output=(
                    "https://github.com/owner/repo/issues/1 "
                    "finish_reason length recovery"
                ),
                output_sha256="digest",
            ),
            ToolTrace(
                sequence=2,
                iteration=2,
                tool_call_id="call-2",
                name="repoops_search_workspace",
                arguments={"query": "length recovery"},
                status="ok",
                output=(
                    'path "nanobot/agent/runner.py" '
                    "finish_reason length recovery branch"
                ),
                output_sha256="digest",
            ),
        ],
        final_answer="{}",
        parsed_answer=answer,
    )


def test_parse_answer_accepts_json_after_a_short_prefix() -> None:
    answer = parse_answer(
        'result:\n{"category":"bug","files":["a.py"],"approval_required":false}'
    )

    assert answer.category == "bug"
    assert answer.files == ["a.py"]


def test_benchmark_requires_one_snapshot_per_invocation() -> None:
    first = BenchmarkCase(
        case_id="one",
        task_type="issue_analysis",
        repository="owner/repo",
        number=1,
        title="One",
        prompt="Analyze",
        source_url="https://github.com/owner/repo/issues/1",
        snapshot_sha="a" * 40,
    )
    second = first.model_copy(
        update={"case_id": "two", "number": 2, "snapshot_sha": "b" * 40}
    )

    assert _benchmark_snapshot([first]) == "a" * 40
    with pytest.raises(ValueError, match="cannot use multiple snapshots"):
        _benchmark_snapshot([first, second])


def test_parse_answer_repairs_a_malformed_string_in_a_fenced_object() -> None:
    answer = parse_answer(
        'done\n```json\n{"category":"bug","confirmed_facts":['
        '{"claim":"unterminated,"evidence_ids":["E1"]}],'
        '"approval_required":false}\n```'
    )

    assert answer.category == "bug"
    assert answer.confirmed_facts


def test_prediction_derives_trace_and_verified_citation_metrics() -> None:
    answer = BenchmarkAnswer(
        category="bug",
        files=["nanobot/agent/runner.py"],
        confirmed_facts=[
            BenchmarkClaim(claim="The branch handles length recovery", evidence_ids=["E1"])
        ],
        hypotheses=[
            BenchmarkClaim(claim="The retry path is selected first", evidence_ids=["MISSING"])
        ],
        evidence=[
            BenchmarkEvidence(
                evidence_id="E1",
                source="nanobot/agent/runner.py",
                locator="length recovery branch",
                excerpt="finish_reason length recovery branch",
            )
        ],
    )

    prediction = trajectory_prediction(_task(), _trajectory(answer))

    assert prediction.category == "bug"
    assert prediction.files == ["nanobot/agent/runner.py"]
    assert [call.name for call in prediction.tool_calls] == [
        "repoops_get_issue",
        "repoops_search_workspace",
    ]
    assert prediction.evidence_claims == 2
    assert prediction.cited_claims == 2
    assert prediction.citations == 2
    assert prediction.hallucinated_citations == 1


def test_invalid_final_output_is_not_mislabeled_as_an_invalid_tool_call() -> None:
    prediction = trajectory_prediction(_task(), _trajectory(None))

    assert prediction.category == "__invalid_output__"
    assert prediction.invalid_tool_calls == 0


def test_merge_runs_recomputes_metrics_from_trajectories(tmp_path) -> None:
    answer = BenchmarkAnswer(
        category="bug",
        files=["nanobot/agent/runner.py"],
    )
    trajectory = _trajectory(answer)
    shard = tmp_path / "shard" / "trajectories"
    shard.mkdir(parents=True)
    (shard / "issue-1.json").write_text(trajectory.model_dump_json())

    output = tmp_path / "merged"
    summary = merge_runs([tmp_path / "shard"], output, tasks=[_task()])

    assert summary["case_count"] == 1
    assert summary["metrics"]["classification_accuracy"] == 1.0
    assert (output / "metrics.json").exists()


def test_merge_runs_accepts_an_agent_specific_prediction_builder(tmp_path) -> None:
    answer = BenchmarkAnswer(
        category="bug",
        files=["nanobot/agent/runner.py"],
    )
    trajectory = _trajectory(answer)
    shard = tmp_path / "shard" / "trajectories"
    shard.mkdir(parents=True)
    (shard / "issue-1.json").write_text(trajectory.model_dump_json())

    calls: list[str] = []

    def prediction_builder(task: EvalTask, item: Trajectory):
        calls.append(item.case_id)
        return trajectory_prediction(task, item)

    summary = merge_runs(
        [tmp_path / "shard"],
        tmp_path / "merged",
        tasks=[_task()],
        agent="Claude Code",
        prediction_builder=prediction_builder,
        provenance={"runtime_commit": "a" * 40},
    )

    assert summary["agent"] == "Claude Code"
    assert summary["provenance"]["runtime_commit"] == "a" * 40
    assert calls == ["issue-1"]


def test_string_tool_arguments_are_preserved_and_scored_invalid() -> None:
    trajectory = _trajectory(
        BenchmarkAnswer(category="bug", files=["nanobot/agent/runner.py"])
    )
    trajectory.tool_trace[0].arguments = '{"repository": "owner/repo"'

    prediction = trajectory_prediction(_task(), trajectory)

    assert prediction.invalid_tool_calls == 1


def test_missing_tool_error_is_scored_as_one_invalid_call() -> None:
    trajectory = _trajectory(
        BenchmarkAnswer(category="bug", files=["nanobot/agent/runner.py"])
    )
    trajectory.tool_trace[0].name = "read_file"
    trajectory.tool_trace[0].status = "error"
    trajectory.tool_trace[0].error = "Tool 'read_file' not found. Available: repoops_read_file"

    prediction = trajectory_prediction(_task(), trajectory)

    assert prediction.invalid_tool_calls == 1


def test_trace_redaction_covers_argument_values_and_unstructured_text() -> None:
    fake_key = "sk-" + "x" * 24

    assert _redact({"note": f"use {fake_key}"}) == {
        "note": "use [REDACTED_API_KEY]"
    }
    assert _redact_text(f"Authorization: Bearer {'a' * 24}") == (
        "Authorization: Bearer [REDACTED]"
    )


def test_benchmark_prompt_names_the_only_valid_file_tool() -> None:
    case = BenchmarkCase(
        case_id="issue-1",
        task_type="issue_analysis",
        repository="owner/repo",
        number=1,
        title="Issue",
        prompt="Analyze",
        source_url="https://github.com/owner/repo/issues/1",
        snapshot_sha="a" * 40,
    )

    prompt = _build_prompt(case)

    assert "读取文件必须调用 `repoops_read_file`" in prompt
    assert "`read_file`、`exec`、`grep` 和 `find_files`" in prompt
    assert "新增一个配置开关" in prompt
    assert "逐字复制一个" in prompt
    assert f"Issue 标题：{case.title}" in prompt
    assert "不要把标题已经明确的" in prompt
    assert "runner 硬性固定" in prompt
    assert "pre-fix 快照不存在" in prompt
    assert "声明/CLI → 序列化 → 配置解析" in prompt
    assert "serialize config" in prompt
