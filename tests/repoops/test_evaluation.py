from __future__ import annotations

from nanobot.repoops.evaluation import (
    EvalPrediction,
    EvalTask,
    EvalToolCall,
    evaluate,
)


def test_metrics_cover_quality_safety_and_efficiency() -> None:
    tasks = [
        EvalTask(
            task_id="one",
            issue_number=1,
            prompt="Analyze",
            expected_category="bug",
            relevant_files=["a.py", "b.py"],
            expected_tools=["repoops_get_issue", "repoops_search_workspace"],
            expected_behavior="report_with_evidence",
            requires_approval=True,
        )
    ]
    predictions = [
        EvalPrediction(
            task_id="one",
            category="bug",
            files=["a.py", "other.py"],
            tool_calls=[
                EvalToolCall(name="repoops_get_issue", arguments_key="1"),
                EvalToolCall(name="repoops_get_issue", arguments_key="1"),
            ],
            invalid_tool_calls=1,
            evidence_claims=4,
            cited_claims=3,
            citations=3,
            hallucinated_citations=1,
            approval_required=True,
        )
    ]

    metrics = evaluate(tasks, predictions)

    assert metrics.classification_accuracy == 1.0
    assert metrics.file_recall_at_5 == 0.5
    assert metrics.tool_precision == 1.0
    assert metrics.tool_recall == 0.5
    assert metrics.invalid_call_rate == 1 / 2
    assert metrics.duplicate_call_rate == 0.5
    assert metrics.evidence_completeness == 0.75
    assert metrics.hallucination_rate == 1 / 3
    assert metrics.approval_gate_accuracy == 1.0
    assert metrics.average_steps == 2.0
