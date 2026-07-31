"""Deterministic RepoOps benchmark schema, metrics, and CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field


class EvalToolCall(BaseModel):
    name: str
    arguments_key: str = ""


class EvalTask(BaseModel):
    task_id: str
    title: str = ""
    repository: str = ""
    issue_number: int = Field(gt=0)
    prompt: str
    expected_category: str
    relevant_files: list[str]
    expected_tools: list[str]
    expected_behavior: str
    requires_approval: bool = False
    source_url: str = ""
    reference_pr: int | None = Field(default=None, gt=0)
    reference_pr_url: str = ""
    snapshot_sha: str = ""


class EvalPrediction(BaseModel):
    task_id: str
    category: str
    files: list[str] = Field(default_factory=list)
    tool_calls: list[EvalToolCall] = Field(default_factory=list)
    invalid_tool_calls: int = Field(default=0, ge=0)
    evidence_claims: int = Field(default=0, ge=0)
    cited_claims: int = Field(default=0, ge=0)
    citations: int = Field(default=0, ge=0)
    hallucinated_citations: int = Field(default=0, ge=0)
    approval_required: bool = False


class EvalMetrics(BaseModel):
    task_count: int
    classification_accuracy: float
    file_recall_at_5: float
    tool_precision: float
    tool_recall: float
    invalid_call_rate: float
    duplicate_call_rate: float
    evidence_completeness: float
    hallucination_rate: float
    approval_gate_accuracy: float
    average_steps: float


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def evaluate(
    tasks: list[EvalTask],
    predictions: list[EvalPrediction],
) -> EvalMetrics:
    predictions_by_id = {prediction.task_id: prediction for prediction in predictions}
    if len(predictions_by_id) != len(predictions):
        raise ValueError("Predictions contain duplicate task_id values")
    missing = [task.task_id for task in tasks if task.task_id not in predictions_by_id]
    if missing:
        raise ValueError(f"Missing predictions for tasks: {', '.join(missing)}")

    classification_hits = 0
    file_recall_total = 0.0
    tool_precision_total = 0.0
    tool_recall_total = 0.0
    invalid_calls = 0
    duplicate_calls = 0
    total_calls = 0
    evidence_claims = 0
    cited_claims = 0
    citations = 0
    hallucinated_citations = 0
    approval_hits = 0

    for task in tasks:
        prediction = predictions_by_id[task.task_id]
        classification_hits += prediction.category == task.expected_category

        expected_files = set(task.relevant_files)
        predicted_files = set(prediction.files[:5])
        file_recall_total += _ratio(len(expected_files & predicted_files), len(expected_files))

        expected_tools = set(task.expected_tools)
        predicted_tools = {call.name for call in prediction.tool_calls}
        tool_precision_total += _ratio(
            len(expected_tools & predicted_tools), len(predicted_tools)
        )
        tool_recall_total += _ratio(
            len(expected_tools & predicted_tools), len(expected_tools)
        )

        seen_calls: set[tuple[str, str]] = set()
        for call in prediction.tool_calls:
            key = (call.name, call.arguments_key)
            if key in seen_calls:
                duplicate_calls += 1
            seen_calls.add(key)
        total_calls += len(prediction.tool_calls)
        invalid_calls += prediction.invalid_tool_calls
        evidence_claims += prediction.evidence_claims
        cited_claims += min(prediction.cited_claims, prediction.evidence_claims)
        citations += prediction.citations
        hallucinated_citations += min(
            prediction.hallucinated_citations,
            prediction.citations,
        )
        approval_hits += prediction.approval_required == task.requires_approval

    task_count = len(tasks)
    return EvalMetrics(
        task_count=task_count,
        classification_accuracy=_ratio(classification_hits, task_count),
        file_recall_at_5=_ratio(file_recall_total, task_count),
        tool_precision=_ratio(tool_precision_total, task_count),
        tool_recall=_ratio(tool_recall_total, task_count),
        invalid_call_rate=_ratio(invalid_calls, total_calls),
        duplicate_call_rate=_ratio(duplicate_calls, total_calls),
        evidence_completeness=_ratio(cited_claims, evidence_claims),
        hallucination_rate=_ratio(hallucinated_citations, citations),
        approval_gate_accuracy=_ratio(approval_hits, task_count),
        average_steps=_ratio(total_calls, task_count),
    )


def load_tasks(path: Path) -> list[EvalTask]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Task file must contain a JSON array")
    return [EvalTask.model_validate(item) for item in cast(list[object], payload)]


def load_predictions(path: Path) -> list[EvalPrediction]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Prediction file must contain a JSON array")
    return [EvalPrediction.model_validate(item) for item in cast(list[object], payload)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RepoOps benchmark predictions")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    metrics = evaluate(load_tasks(args.tasks), load_predictions(args.predictions))
    output = metrics.model_dump_json(indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
