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


def test_search_diversifies_files_before_repeating_chunks(tmp_path) -> None:
    (tmp_path / "large.py").write_text(
        "\n".join(f"target_symbol = {index}" for index in range(220)),
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "def consume_target_symbol():\n    return target_symbol\n",
        encoding="utf-8",
    )
    (tmp_path / "config.py").write_text(
        "TARGET_SYMBOL_SETTING = 'target_symbol'\n",
        encoding="utf-8",
    )

    chunks = WorkspaceIndexer(tmp_path, chunk_lines=40, overlap_lines=5).index()
    hits = HybridRetriever(chunks).search("target_symbol", top_k=3)

    assert len(hits) == 3
    assert len({hit.chunk.path for hit in hits}) == 3


def test_search_reserves_results_for_rare_query_facets(tmp_path) -> None:
    (tmp_path / "cli.ts").write_text(
        " ".join(["retry cli argument parse config"] * 100),
        encoding="utf-8",
    )
    (tmp_path / "serialize.ts").write_text(
        "export function serializeConfig() { return retry }\n",
        encoding="utf-8",
    )
    (tmp_path / "runtime.ts").write_text(
        "export interface RuntimeConfig { retry: Retry }\n",
        encoding="utf-8",
    )

    chunks = WorkspaceIndexer(tmp_path).index()
    hits = HybridRetriever(chunks).search(
        "retry cli argument parse serialize config runtime type",
        top_k=3,
    )

    assert {hit.chunk.path for hit in hits} == {
        "cli.ts",
        "serialize.ts",
        "runtime.ts",
    }
