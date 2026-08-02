"""Run one benchmark task per repository snapshot and merge the trajectories."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from nanobot.repoops.benchmark import trajectory_prediction
from nanobot.repoops.benchmark_merge import merge_runs
from nanobot.repoops.evaluation import EvalTask, load_tasks

_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_COMMIT = re.compile(r"[0-9a-fA-F]{40}")


@dataclass(frozen=True)
class PreparedTask:
    task: EvalTask
    workspace: Path
    shard_dir: Path


def repository_slug(repository: str) -> str:
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError(f"Invalid GitHub repository: {repository}")
    return repository.replace("/", "__")


async def _command(*arguments: str, cwd: Path | None = None) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        int(process.returncode or 0),
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def _checked_command(*arguments: str, cwd: Path | None = None) -> str:
    return_code, stdout, stderr = await _command(*arguments, cwd=cwd)
    if return_code != 0:
        rendered = " ".join(arguments[:3])
        detail = stderr.strip() or stdout.strip()
        raise RuntimeError(f"Command failed ({rendered}): {detail}")
    return stdout.strip()


async def prepare_repository(repository: str, cache_root: Path) -> Path:
    cache_root.mkdir(parents=True, exist_ok=True)
    source = cache_root / repository_slug(repository)
    if not source.exists():
        await _checked_command(
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            f"https://github.com/{repository}.git",
            str(source),
        )
    git_dir = await _checked_command("git", "-C", str(source), "rev-parse", "--git-dir")
    if not git_dir:
        raise RuntimeError(f"Repository cache is not a Git repository: {source}")
    return source


async def prepare_worktree(task: EvalTask, source: Path, worktree_root: Path) -> Path:
    if not _COMMIT.fullmatch(task.snapshot_sha):
        raise ValueError(f"Task {task.task_id} does not have a full snapshot SHA")
    worktree_root.mkdir(parents=True, exist_ok=True)
    workspace = worktree_root / task.task_id
    if workspace.exists():
        current = await _checked_command("git", "-C", str(workspace), "rev-parse", "HEAD")
        if current != task.snapshot_sha:
            raise RuntimeError(
                f"Existing worktree {workspace} is {current}, expected {task.snapshot_sha}"
            )
        return workspace
    await _checked_command(
        "git",
        "-C",
        str(source),
        "worktree",
        "add",
        "--detach",
        str(workspace),
        task.snapshot_sha,
    )
    return workspace


def benchmark_command(args: argparse.Namespace, prepared: PreparedTask) -> list[str]:
    module = "nanobot.repoops.benchmark"
    if args.agent == "claude-code":
        module = "nanobot.repoops.claude_benchmark"
    elif args.agent in {"vanilla-nanobot", "github-mcp"}:
        module = "nanobot.repoops.generic_benchmark"
    common = [
        sys.executable,
        "-m",
        module,
        "--tasks",
        str(args.tasks),
        "--case-id",
        prepared.task.task_id,
        "--workspace",
        str(prepared.workspace),
        "--output-dir",
        str(prepared.shard_dir),
        "--timeout",
        str(args.timeout),
    ]
    if args.agent == "repoops":
        return [*common, "--config", str(args.config)]
    if args.agent in {"vanilla-nanobot", "github-mcp"}:
        return [
            *common,
            "--agent",
            args.agent,
            "--config",
            str(args.config),
            "--runtime-root",
            str(args.runtime_root),
        ]
    return [
        *common,
        "--settings",
        str(args.settings),
        "--executable",
        args.executable,
        "--model",
        args.model,
        "--effort",
        args.effort,
        "--max-budget-usd",
        str(args.max_budget_usd),
    ]


async def run_prepared_task(
    args: argparse.Namespace,
    prepared: PreparedTask,
    semaphore: asyncio.Semaphore,
) -> None:
    trajectory = prepared.shard_dir / "trajectories" / f"{prepared.task.task_id}.json"
    if args.resume and trajectory.exists():
        print(f"[resume] {prepared.task.task_id}", flush=True)
        return
    prepared.shard_dir.mkdir(parents=True, exist_ok=True)
    async with semaphore:
        return_code, stdout, stderr = await _command(*benchmark_command(args, prepared))
    summary = prepared.shard_dir / "run_summary.json"
    if not summary.exists():
        detail = stderr.strip() or stdout.strip()
        raise RuntimeError(f"Benchmark failed for {prepared.task.task_id}: {detail}")
    status = "ok" if return_code == 0 else "recorded failure"
    print(f"[{status}] {prepared.task.task_id}\n{stdout.strip()}", flush=True)


async def run(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks)
    if not tasks:
        raise ValueError("No cross-language tasks found")
    if args.jobs < 1:
        raise ValueError("--jobs must be at least 1")
    if args.output_dir.exists() and not args.resume:
        existing = list(args.output_dir.iterdir())
        if existing:
            raise ValueError(f"Output directory is not empty: {args.output_dir}")

    sources: dict[str, Path] = {}
    for repository in sorted({task.repository for task in tasks}):
        sources[repository] = await prepare_repository(repository, args.repository_cache)

    prepared_tasks: list[PreparedTask] = []
    shard_root = args.output_dir / "_shards"
    for task in tasks:
        workspace = await prepare_worktree(
            task,
            sources[task.repository],
            args.worktree_root,
        )
        prepared_tasks.append(
            PreparedTask(
                task=task,
                workspace=workspace,
                shard_dir=shard_root / task.task_id,
            )
        )

    semaphore = asyncio.Semaphore(args.jobs)
    await asyncio.gather(
        *(run_prepared_task(args, prepared, semaphore) for prepared in prepared_tasks)
    )

    agent = "RepoOps Agent"
    prediction_builder = trajectory_prediction
    provenance = None
    if args.agent == "claude-code":
        from nanobot.repoops.claude_benchmark import claude_trajectory_prediction

        agent = "Claude Code"
        prediction_builder = claude_trajectory_prediction
    elif args.agent in {"vanilla-nanobot", "github-mcp"}:
        from nanobot.repoops.generic_benchmark import (
            PRE_REPOOPS_COMMIT,
            generic_trajectory_prediction,
        )

        agent = (
            "Pre-RepoOps nanobot + GitHub MCP Server"
            if args.agent == "github-mcp"
            else "Pre-RepoOps nanobot"
        )
        prediction_builder = generic_trajectory_prediction
        provenance = {
            "nanobot_runtime_commit": PRE_REPOOPS_COMMIT,
            "adapter": args.agent,
        }
        if args.agent == "github-mcp":
            lock_path = Path(__file__).resolve().parents[2] / "eval/github_mcp_server.lock.json"
            provenance["github_mcp_server"] = json.loads(lock_path.read_text(encoding="utf-8"))
    summary = merge_runs(
        [prepared.shard_dir for prepared in prepared_tasks],
        args.output_dir,
        tasks=tasks,
        agent=agent,
        prediction_builder=prediction_builder,
        provenance=provenance,
    )
    print(
        f"Merged {summary['case_count']} {agent} cases: "
        f"{summary['successful_cases']} structured successes",
        flush=True,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run cross-language tasks in per-task pre-fix Git worktrees"
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument(
        "--agent",
        choices=("repoops", "claude-code", "vanilla-nanobot", "github-mcp"),
        required=True,
    )
    parser.add_argument("--repository-cache", type=Path, required=True)
    parser.add_argument("--worktree-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude/settings.json")
    parser.add_argument("--executable", default="claude")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--effort", default="max")
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.agent != "claude-code" and args.config is None:
        parser.error(f"--config is required for --agent {args.agent}")
    if args.agent in {"vanilla-nanobot", "github-mcp"} and args.runtime_root is None:
        parser.error(f"--runtime-root is required for --agent {args.agent}")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
