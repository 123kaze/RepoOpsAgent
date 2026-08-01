from nanobot.utils.runtime import (
    build_budget_exhausted_finalization_message,
    build_tool_markup_recovery_message,
    repeated_repo_file_read_error,
)


def test_budget_exhausted_finalization_preserves_requested_output_contract() -> None:
    message = build_budget_exhausted_finalization_message()

    assert message["role"] == "user"
    assert "Preserve every output-format constraint" in message["content"]
    assert "JSON-only" in message["content"]
    assert "do not emit or describe tool calls" in message["content"]
    assert "DSML" in message["content"]


def test_tool_markup_recovery_forbids_another_invocation() -> None:
    message = build_tool_markup_recovery_message()

    assert message["role"] == "user"
    assert "serialized tool-call markup" in message["content"]
    assert "Preserve the original output schema" in message["content"]


def test_repoops_file_read_budget_blocks_third_range_for_same_path() -> None:
    counts: dict[str, int] = {}
    arguments = {"repository": "owner/repo", "path": "src/large.ts"}

    assert repeated_repo_file_read_error("repoops_read_file", arguments, counts) is None
    assert repeated_repo_file_read_error("repoops_read_file", arguments, counts) is None
    error = repeated_repo_file_read_error("repoops_read_file", arguments, counts)

    assert error is not None
    assert "Do not retry or pan" in error
    assert repeated_repo_file_read_error(
        "repoops_read_file",
        {"repository": "owner/repo", "path": "src/consumer.ts"},
        counts,
    ) is None
