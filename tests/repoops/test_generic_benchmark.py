from nanobot.repoops.benchmark import BenchmarkAnswer, BenchmarkCase, ToolTrace, Trajectory
from nanobot.repoops.evaluation import EvalTask
from nanobot.repoops.generic_benchmark import build_prompt, generic_trajectory_prediction


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        case_id="issue-1",
        task_type="issue_analysis",
        repository="owner/repo",
        number=7,
        title="Parser crashes",
        prompt="Analyze the historical issue",
        source_url="https://github.com/owner/repo/issues/7",
        snapshot_sha="a" * 40,
    )


def _task() -> EvalTask:
    return EvalTask(
        task_id="issue-1",
        repository="owner/repo",
        issue_number=7,
        prompt="Analyze the historical issue",
        expected_category="bug",
        relevant_files=["parser.py"],
        expected_tools=["repoops_get_issue", "repoops_search_workspace"],
        expected_behavior="report_with_evidence",
    )


def test_vanilla_prompt_uses_only_generic_read_only_tools() -> None:
    prompt = build_prompt(_case(), "vanilla-nanobot")

    assert "`gh issue view 7 --repo owner/repo" in prompt
    assert "`grep`" in prompt
    assert "repoops_get_issue" not in prompt
    assert "不得查看或推断评测标准答案" in prompt


def test_github_mcp_prompt_uses_exact_read_only_tool_names() -> None:
    prompt = build_prompt(_case(), "github-mcp")

    assert "`mcp_github_issue_read`" in prompt
    assert "issue_number=7" in prompt
    assert "没有 shell/exec 工具" in prompt


def test_generic_prediction_normalizes_comparable_tool_roles() -> None:
    trajectory = Trajectory(
        case_id="issue-1",
        task_type="issue_analysis",
        repository="owner/repo",
        number=7,
        started_at="2026-08-01T00:00:00+00:00",
        completed_at="2026-08-01T00:00:01+00:00",
        duration_ms=1000,
        source_url="https://github.com/owner/repo/issues/7",
        snapshot_sha="a" * 40,
        prompt="Analyze",
        tool_trace=[
            ToolTrace(
                sequence=1,
                iteration=1,
                tool_call_id="call-1",
                name="exec",
                arguments={"command": "gh issue view 7 --repo owner/repo --json title"},
                status="ok",
            ),
            ToolTrace(
                sequence=2,
                iteration=2,
                tool_call_id="call-2",
                name="grep",
                arguments={"pattern": "Parser"},
                status="ok",
            ),
            ToolTrace(
                sequence=3,
                iteration=3,
                tool_call_id="call-3",
                name="read_file",
                arguments={"path": "parser.py"},
                status="error",
            ),
        ],
        final_answer='{"category":"bug","files":["parser.py"]}',
        parsed_answer=BenchmarkAnswer(category="bug", files=["parser.py"]),
    )

    prediction = generic_trajectory_prediction(_task(), trajectory)

    assert [call.name for call in prediction.tool_calls] == [
        "repoops_get_issue",
        "repoops_search_workspace",
        "repoops_read_file",
    ]
    assert prediction.invalid_tool_calls == 1
