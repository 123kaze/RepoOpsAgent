"""Build grouped metrics for the cross-language agent benchmark."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, cast

from nanobot.repoops.benchmark import Trajectory
from nanobot.repoops.evaluation import (
    EvalPrediction,
    EvalTask,
    evaluate,
    load_predictions,
    load_tasks,
)


def _load_trajectories(run_dir: Path) -> dict[str, Trajectory]:
    trajectories: dict[str, Trajectory] = {}
    for path in sorted((run_dir / "trajectories").glob("*.json")):
        payload: object = json.loads(path.read_text(encoding="utf-8"))
        trajectory = Trajectory.model_validate(payload)
        trajectories[trajectory.case_id] = trajectory
    return trajectories


def _group_summary(
    tasks: list[EvalTask],
    predictions: dict[str, EvalPrediction],
    trajectories: dict[str, Trajectory],
) -> dict[str, Any]:
    selected_predictions = [predictions[task.task_id] for task in tasks]
    metrics = evaluate(tasks, selected_predictions)
    selected_trajectories = [trajectories[task.task_id] for task in tasks]
    successful_ids = {
        trajectory.case_id
        for trajectory in selected_trajectories
        if not trajectory.run_error and not trajectory.parse_error
    }
    successful_tasks = [task for task in tasks if task.task_id in successful_ids]
    successful_metrics = None
    if successful_tasks:
        successful_metrics = evaluate(
            successful_tasks,
            [predictions[task.task_id] for task in successful_tasks],
        )
    durations = [trajectory.duration_ms for trajectory in selected_trajectories]
    tool_calls = [len(trajectory.tool_trace) for trajectory in selected_trajectories]
    usage_keys = sorted(
        {
            key
            for trajectory in selected_trajectories
            for key in trajectory.usage
        }
    )
    return {
        "task_count": len(tasks),
        "structured_successes": len(successful_tasks),
        "structured_success_rate": len(successful_tasks) / len(tasks) if tasks else 0.0,
        "metrics": metrics.model_dump(mode="json"),
        "successful_output_metrics": (
            successful_metrics.model_dump(mode="json") if successful_metrics else None
        ),
        "total_tool_calls": sum(tool_calls),
        "total_duration_ms": sum(durations),
        "mean_duration_ms": round(statistics.mean(durations)) if durations else 0,
        "median_duration_ms": round(statistics.median(durations)) if durations else 0,
        "max_duration_ms": max(durations, default=0),
        "median_tool_calls": statistics.median(tool_calls) if tool_calls else 0,
        "max_tool_calls": max(tool_calls, default=0),
        "total_usage": {
            key: sum(trajectory.usage.get(key, 0) for trajectory in selected_trajectories)
            for key in usage_keys
        },
        "failed_cases": [
            {
                "task_id": trajectory.case_id,
                "run_error": trajectory.run_error,
                "parse_error": trajectory.parse_error,
                "tool_calls": len(trajectory.tool_trace),
                "duration_ms": trajectory.duration_ms,
            }
            for trajectory in selected_trajectories
            if trajectory.run_error or trajectory.parse_error
        ],
    }


def summarize_run(tasks: list[EvalTask], run_dir: Path) -> dict[str, Any]:
    predictions_list = load_predictions(run_dir / "predictions.json")
    predictions = {prediction.task_id: prediction for prediction in predictions_list}
    trajectories = _load_trajectories(run_dir)
    task_ids = {task.task_id for task in tasks}
    if set(predictions) != task_ids or set(trajectories) != task_ids:
        raise ValueError(f"Run directory does not contain exactly the task set: {run_dir}")
    summary_payload: object = json.loads(
        (run_dir / "run_summary.json").read_text(encoding="utf-8")
    )
    summary = cast(dict[str, Any], summary_payload)

    by_language = {
        language: _group_summary(
            [task for task in tasks if task.language == language],
            predictions,
            trajectories,
        )
        for language in sorted({task.language for task in tasks})
    }
    by_repository = {
        repository: _group_summary(
            [task for task in tasks if task.repository == repository],
            predictions,
            trajectories,
        )
        for repository in sorted({task.repository for task in tasks})
    }
    return {
        "agent": summary.get("agent", ""),
        "models": summary.get("models", []),
        "provenance": summary.get("provenance", {}),
        "overall": _group_summary(tasks, predictions, trajectories),
        "by_language": by_language,
        "by_repository": by_repository,
    }


def build_report(
    tasks: list[EvalTask],
    repoops_run_dir: Path,
    claude_run_dir: Path,
    *,
    vanilla_run_dir: Path | None = None,
    github_mcp_run_dir: Path | None = None,
) -> dict[str, Any]:
    agents = {
        "repoops": summarize_run(tasks, repoops_run_dir),
        "claude_code": summarize_run(tasks, claude_run_dir),
    }
    if vanilla_run_dir is not None:
        agents["pre_repoops_nanobot"] = summarize_run(tasks, vanilla_run_dir)
    if github_mcp_run_dir is not None:
        agents["pre_repoops_nanobot_github_mcp"] = summarize_run(tasks, github_mcp_run_dir)
    return {
        "schema_version": "1.0",
        "task_count": len(tasks),
        "repositories": sorted({task.repository for task in tasks}),
        "languages": sorted({task.language for task in tasks}),
        "snapshot_strategy": "per-task pre-fix first parent of the merged reference PR",
        "agents": agents,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Group cross-language benchmark metrics")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--repoops-run-dir", type=Path, required=True)
    parser.add_argument("--claude-run-dir", type=Path, required=True)
    parser.add_argument("--vanilla-run-dir", type=Path)
    parser.add_argument("--github-mcp-run-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        load_tasks(args.tasks),
        args.repoops_run_dir,
        args.claude_run_dir,
        vanilla_run_dir=args.vanilla_run_dir,
        github_mcp_run_dir=args.github_mcp_run_dir,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
