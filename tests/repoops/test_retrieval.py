from __future__ import annotations

from nanobot.repoops.retrieval import HybridRetriever, WorkspaceIndexer, tokenize


def test_tokenize_splits_snake_and_camel_case() -> None:
    tokens = tokenize("RepoTaskState approval_gate")

    assert {"repo", "task", "state", "approval_gate", "approval", "gate"} <= set(
        tokens
    )


def test_exact_symbol_is_reranked_first_and_dependencies_are_ignored(tmp_path) -> None:
    (tmp_path / "service.py").write_text(
        "def process_task(value: str) -> str:\n"
        "    return value.strip()\n\n"
        "def unrelated() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    dependency = tmp_path / "node_modules"
    dependency.mkdir()
    (dependency / "noise.py").write_text(
        "def process_task(value):\n    raise RuntimeError()\n",
        encoding="utf-8",
    )

    chunks = WorkspaceIndexer(tmp_path).index()
    hits = HybridRetriever(chunks).search("process_task")

    assert hits
    assert hits[0].chunk.path == "service.py"
    assert hits[0].chunk.symbol == "process_task"
    assert hits[0].reason == "exact symbol/path match"
    assert all("node_modules" not in hit.chunk.path for hit in hits)


def test_index_path_cannot_escape_workspace(tmp_path) -> None:
    indexer = WorkspaceIndexer(tmp_path)

    try:
        indexer.index("../")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("workspace escape should fail")


def test_runtime_state_directories_are_not_indexed(tmp_path) -> None:
    (tmp_path / "source.py").write_text("def target_symbol():\n    return 1\n")
    for directory_name in (".nanobot", ".repoops", "sessions"):
        runtime_dir = tmp_path / directory_name
        runtime_dir.mkdir()
        (runtime_dir / "state.py").write_text(
            "def contaminated_target_symbol():\n    return 'old answer'\n"
        )

    chunks = WorkspaceIndexer(tmp_path).index()

    assert {chunk.path for chunk in chunks} == {"source.py"}
