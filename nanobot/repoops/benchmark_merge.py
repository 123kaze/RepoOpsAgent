"""Merge independently executed RepoOps benchmark shards."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from nanobot.repoops.benchmark import Trajectory, parse_answer, trajectory_prediction
from nanobot.repoops.evaluation import EvalPrediction, EvalTask, evaluate, load_tasks

PredictionBuilder = Callable[[EvalTask, Trajectory], EvalPrediction]


def _load_trajectory(path: Path) -> Trajectory:
    trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
    if trajectory.parsed_answer is None and trajectory.final_answer:
        try:
            trajectory.parsed_answer = parse_answer(trajectory.final_answer)
            trajectory.parse_error = ""
        except (TypeError, ValueError):
            pass
    return trajectory


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def merge_runs(
    input_dirs: list[Path],
    output_dir: Path,
    *,
    tasks: list[EvalTask] | None = None,
    agent: str = "RepoOps Agent",
    prediction_builder: PredictionBuilder = trajectory_prediction,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trajectories: list[Trajectory] = []
    seen_ids: set[str] = set()
    for input_dir in input_dirs:
        for path in sorted((input_dir / "trajectories").glob("*.json")):
            trajectory = _load_trajectory(path)
            if trajectory.case_id in seen_ids:
                raise ValueError(f"Duplicate case ID across shards: {trajectory.case_id}")
            seen_ids.add(trajectory.case_id)
            trajectories.append(trajectory)

    if not trajectories:
        raise ValueError("No trajectories found in input shards")
    trajectories.sort(key=lambda item: item.case_id)
    trajectory_dir = output_dir / "trajectories"
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    for input_dir in input_dirs:
        for path in sorted((input_dir / "trajectories").glob("*.json")):
            shutil.copy2(path, trajectory_dir / path.name)

    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "agent": agent,
        "models": sorted({item.model for item in trajectories if item.model}),
        "case_count": len(trajectories),
        "successful_cases": sum(
            not item.run_error and not item.parse_error for item in trajectories
        ),
        "total_tool_calls": sum(len(item.tool_trace) for item in trajectories),
        "total_duration_ms": sum(item.duration_ms for item in trajectories),
        "total_usage": {
            key: sum(item.usage.get(key, 0) for item in trajectories)
            for key in sorted({key for item in trajectories for key in item.usage})
        },
        "cases": [
            {
                "case_id": item.case_id,
                "task_type": item.task_type,
                "model": item.model,
                "tool_calls": len(item.tool_trace),
                "duration_ms": item.duration_ms,
                "run_error": item.run_error,
                "parse_error": item.parse_error,
            }
            for item in trajectories
        ],
        "shard_count": len(input_dirs),
    }
    if provenance:
        summary["provenance"] = provenance

    if tasks is not None:
        tasks_by_id = {task.task_id: task for task in tasks}
        unknown = sorted(seen_ids - tasks_by_id.keys())
        if unknown:
            raise ValueError(f"Trajectories do not match tasks: {', '.join(unknown)}")
        selected_tasks = [tasks_by_id[item.case_id] for item in trajectories]
        predictions = [
            prediction_builder(tasks_by_id[item.case_id], item)
            for item in trajectories
        ]
        metrics = evaluate(selected_tasks, predictions)
        _write_json(
            output_dir / "predictions.json",
            [prediction.model_dump(mode="json") for prediction in predictions],
        )
        _write_json(output_dir / "metrics.json", metrics.model_dump(mode="json"))
        summary["metrics"] = metrics.model_dump(mode="json")

    _write_json(output_dir / "run_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge RepoOps benchmark shards")
    parser.add_argument("--input-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument(
        "--agent",
        choices=("repoops", "claude-code"),
        default="repoops",
        help="Select the prediction normalizer and summary label",
    )
    args = parser.parse_args()
    tasks = load_tasks(args.tasks) if args.tasks else None
    agent = "RepoOps Agent"
    prediction_builder = trajectory_prediction
    if args.agent == "claude-code":
        from nanobot.repoops.claude_benchmark import claude_trajectory_prediction

        agent = "Claude Code"
        prediction_builder = claude_trajectory_prediction
    summary = merge_runs(
        cast(list[Path], args.input_dir),
        args.output_dir,
        tasks=tasks,
        agent=agent,
        prediction_builder=prediction_builder,
    )
    print(
        f"Merged {summary['case_count']} cases; "
        f"{summary['successful_cases']} successful."
    )


if __name__ == "__main__":
    main()
