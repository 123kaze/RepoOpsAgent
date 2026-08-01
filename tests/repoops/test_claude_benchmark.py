from nanobot.repoops.benchmark import BenchmarkCase, ToolTrace
from nanobot.repoops.claude_benchmark import (
    _claude_prompt,
    _normalized_tool_name,
    _parse_stream_events,
    answer_json_schema,
)


def test_answer_schema_restricts_category() -> None:
    category = answer_json_schema()["properties"]["category"]

    assert "bug" in category["enum"]
    assert "security" in category["enum"]


def test_claude_prompt_uses_only_task_input_not_expected_labels() -> None:
    case = BenchmarkCase(
        case_id="issue-1",
        task_type="issue_analysis",
        repository="owner/repo",
        number=1,
        title="Issue",
        prompt="Analyze the report",
        source_url="https://github.com/owner/repo/issues/1",
        snapshot_sha="a" * 40,
    )

    prompt = _claude_prompt(case)

    assert "Analyze the report" in prompt
    assert "gh issue view 1 --repo owner/repo" in prompt
    assert "expected_category" not in prompt
    assert "relevant_files" not in prompt


def test_stream_parser_captures_tools_but_not_structured_output() -> None:
    events = [
        {"type": "system", "subtype": "init", "model": "deepseek-v4-pro"},
        {
            "type": "assistant",
            "message": {
                "model": "deepseek-v4-pro",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "Read",
                        "input": {"file_path": "/repo/a.py"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "source text",
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "final-1",
                        "name": "StructuredOutput",
                        "input": {"category": "bug"},
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": {"category": "bug"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]

    model, traces, _, usage, final_answer, run_error = _parse_stream_events(events)

    assert model == "deepseek-v4-pro"
    assert [trace.name for trace in traces] == ["Read"]
    assert traces[0].output == "source text"
    assert usage["total_tokens"] == 15
    assert final_answer == '{"category":"bug"}'
    assert run_error == ""
    assert _normalized_tool_name(traces[0]) == "repoops_read_file"


def test_shell_workspace_search_is_normalized_for_cross_agent_metrics() -> None:
    trace = ToolTrace(
        sequence=1,
        iteration=0,
        tool_call_id="call-1",
        name="Bash",
        arguments={"command": 'rg -n "heartbeat" nanobot/'},
    )

    assert _normalized_tool_name(trace) == "repoops_search_workspace"
