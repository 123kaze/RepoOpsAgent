"""Dependency-free symbol-aware hybrid retrieval for a checked-out repository."""

from __future__ import annotations

import ast
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|[\u4e00-\u9fff]{2,}")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ALLOWED_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".md",
    ".py",
    ".rs",
    ".rst",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".nanobot",
    ".pytest_cache",
    ".repoops",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "sessions",
    "venv",
}


@dataclass(frozen=True)
class CodeChunk:
    path: str
    start_line: int
    end_line: int
    symbol: str
    text: str


@dataclass(frozen=True)
class SearchHit:
    chunk: CodeChunk
    score: float
    lexical_score: float
    semantic_score: float
    reason: str


def tokenize(text: str) -> list[str]:
    expanded = _CAMEL_BOUNDARY.sub(" ", text).replace("-", " ").replace(".", " ")
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(expanded):
        lowered = match.lower()
        tokens.append(lowered)
        if "_" in lowered:
            tokens.extend(part for part in lowered.split("_") if len(part) > 1)
    return tokens


def _character_trigrams(text: str) -> set[str]:
    normalized = " ".join(tokenize(text))
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {normalized[index : index + 3] for index in range(len(normalized) - 2)}


class WorkspaceIndexer:
    def __init__(
        self,
        workspace: Path,
        *,
        max_file_bytes: int = 1_000_000,
        chunk_lines: int = 80,
        overlap_lines: int = 10,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.max_file_bytes = max_file_bytes
        self.chunk_lines = chunk_lines
        self.overlap_lines = overlap_lines

    def index(self, relative_path: str = ".") -> list[CodeChunk]:
        root = (self.workspace / relative_path).resolve()
        try:
            root.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Retrieval path escapes the active workspace") from exc
        if not root.exists():
            raise ValueError(f"Retrieval path does not exist: {relative_path}")
        paths = [root] if root.is_file() else self._iter_files(root)
        chunks: list[CodeChunk] = []
        for path in paths:
            if path.suffix.lower() not in _ALLOWED_SUFFIXES:
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.relative_to(self.workspace).as_posix()
            chunks.extend(self._chunk_file(relative, text, path.suffix.lower()))
        return chunks

    def _iter_files(self, root: Path) -> list[Path]:
        return sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and not any(part in _IGNORED_DIRS for part in path.parts)
        )

    def _chunk_file(self, path: str, text: str, suffix: str) -> list[CodeChunk]:
        if suffix == ".py":
            python_chunks = self._python_chunks(path, text)
            if python_chunks:
                # Symbol chunks provide precise function/class matches. Line
                # chunks retain module-level constants, imports, and statements
                # that an AST-only index would otherwise drop.
                return python_chunks + self._line_chunks(path, text)
        return self._line_chunks(path, text)

    @staticmethod
    def _python_chunks(path: str, text: str) -> list[CodeChunk]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        lines = text.splitlines()
        chunks: list[CodeChunk] = []
        for node in tree.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = node.lineno
            end = node.end_lineno or start
            chunks.append(
                CodeChunk(
                    path=path,
                    start_line=start,
                    end_line=end,
                    symbol=node.name,
                    text="\n".join(lines[start - 1 : end]),
                )
            )
        return chunks

    def _line_chunks(self, path: str, text: str) -> list[CodeChunk]:
        lines = text.splitlines()
        if not lines:
            return []
        step = max(1, self.chunk_lines - self.overlap_lines)
        chunks: list[CodeChunk] = []
        for index in range(0, len(lines), step):
            end = min(len(lines), index + self.chunk_lines)
            chunks.append(
                CodeChunk(
                    path=path,
                    start_line=index + 1,
                    end_line=end,
                    symbol="",
                    text="\n".join(lines[index:end]),
                )
            )
            if end == len(lines):
                break
        return chunks


class HybridRetriever:
    """BM25 + local trigram similarity + exact symbol/path reranking."""

    def __init__(self, chunks: list[CodeChunk]) -> None:
        self.chunks = chunks
        self._documents = [
            tokenize(f"{chunk.path} {chunk.symbol} {chunk.text}") for chunk in chunks
        ]
        self._document_frequencies: Counter[str] = Counter()
        for document in self._documents:
            self._document_frequencies.update(set(document))
        self._average_length = (
            sum(len(document) for document in self._documents) / len(self._documents)
            if self._documents
            else 0.0
        )

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.chunks:
            return []
        query_grams = _character_trigrams(query)
        identifier_query = query.strip() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", query.strip()) else ""
        candidates: list[SearchHit] = []
        candidate_documents: dict[tuple[str, int, int, str], set[str]] = {}
        for chunk, document in zip(self.chunks, self._documents, strict=True):
            lexical = self._bm25(query_tokens, document)
            chunk_grams = _character_trigrams(f"{chunk.path} {chunk.symbol} {chunk.text[:2_000]}")
            union = query_grams | chunk_grams
            semantic = len(query_grams & chunk_grams) / len(union) if union else 0.0
            exact_symbol = bool(
                identifier_query
                and (
                    chunk.symbol.lower() == identifier_query.lower()
                    or identifier_query.lower() in chunk.path.lower()
                )
            )
            symbol_boost = 3.0 if exact_symbol else 0.0
            score = lexical + (semantic * 2.0) + symbol_boost
            if score <= 0:
                continue
            reason = (
                "exact symbol/path match"
                if exact_symbol
                else "BM25 + trigram similarity"
            )
            hit = SearchHit(
                chunk=chunk,
                score=score,
                lexical_score=lexical,
                semantic_score=semantic,
                reason=reason,
            )
            candidates.append(hit)
            candidate_documents[self._chunk_key(chunk)] = set(document)
        candidates.sort(
            key=lambda hit: (-hit.score, hit.chunk.path, hit.chunk.start_line)
        )
        return self._diversify_paths(
            candidates,
            top_k,
            query_tokens=query_tokens,
            candidate_documents=candidate_documents,
            document_frequencies=self._document_frequencies,
        )

    @staticmethod
    def _chunk_key(chunk: CodeChunk) -> tuple[str, int, int, str]:
        return (chunk.path, chunk.start_line, chunk.end_line, chunk.symbol)

    @staticmethod
    def _diversify_paths(
        candidates: list[SearchHit],
        top_k: int,
        *,
        query_tokens: list[str],
        candidate_documents: dict[tuple[str, int, int, str], set[str]],
        document_frequencies: Counter[str],
    ) -> list[SearchHit]:
        """Expose the best chunk from more files before repeating one file.

        Repository questions commonly span configuration, declarations, serialization, and
        runtime consumers. Returning ten adjacent chunks from one large test or implementation
        file hides that call chain from the agent even when other files scored well. The second
        pass still fills spare slots with additional chunks when the result set has few files.
        """
        if not candidates or top_k <= 0:
            return []

        selected: list[SearchHit] = [candidates[0]]
        deferred: list[SearchHit] = []
        seen_paths = {candidates[0].chunk.path}
        selected_keys = {HybridRetriever._chunk_key(candidates[0].chunk)}
        covered_tokens = set(
            candidate_documents.get(HybridRetriever._chunk_key(candidates[0].chunk), set())
        )

        # Multi-concept queries such as "retry serialize config runtime" should not be
        # monopolized by a file that repeats only the common words. Reserve a small portion of
        # the result set for the best path covering each rare, not-yet-covered query facet.
        facet_budget = min(max(0, top_k - 1), 3)
        query_token_set = set(query_tokens)
        unique_tokens = sorted(
            set(query_tokens),
            key=lambda token: (document_frequencies.get(token, 0), token),
        )
        for token in unique_tokens:
            if facet_budget <= 0:
                break
            if token in covered_tokens or not document_frequencies.get(token, 0):
                continue
            facet_matches = [
                hit
                for hit in candidates
                if hit.chunk.path not in seen_paths
                and token in candidate_documents.get(
                    HybridRetriever._chunk_key(hit.chunk), set()
                )
            ]
            facet_matches.sort(
                key=lambda hit: (
                    HybridRetriever._is_supporting_path(hit.chunk.path),
                    token not in set(tokenize(f"{hit.chunk.path} {hit.chunk.symbol}")),
                    -len(
                        set(tokenize(f"{hit.chunk.path} {hit.chunk.symbol}"))
                        & query_token_set
                    ),
                    -hit.score,
                    hit.chunk.path,
                    hit.chunk.start_line,
                )
            )
            facet_hit = facet_matches[0] if facet_matches else None
            if facet_hit is None:
                continue
            selected.append(facet_hit)
            seen_paths.add(facet_hit.chunk.path)
            selected_keys.add(HybridRetriever._chunk_key(facet_hit.chunk))
            # A facet-selected file may mention other query words only in an import or comment.
            # Mark the selected facet itself, leaving the remaining rare concepts eligible for
            # their own representative path.
            covered_tokens.add(token)
            facet_budget -= 1
            if len(selected) == top_k:
                return selected

        for hit in candidates:
            hit_key = HybridRetriever._chunk_key(hit.chunk)
            if hit_key in selected_keys:
                continue
            if hit.chunk.path in seen_paths:
                deferred.append(hit)
                continue
            selected.append(hit)
            seen_paths.add(hit.chunk.path)
            selected_keys.add(hit_key)
            if len(selected) == top_k:
                return selected
        if len(selected) < top_k:
            selected.extend(deferred[: top_k - len(selected)])
        return selected

    @staticmethod
    def _is_supporting_path(path: str) -> bool:
        parts = {part.lower() for part in Path(path).parts}
        supporting = {
            "doc",
            "docs",
            "example",
            "examples",
            "fixture",
            "fixtures",
            "test",
            "tests",
        }
        return bool(parts & supporting)

    def _bm25(self, query_tokens: list[str], document: list[str]) -> float:
        if not document or not self.chunks:
            return 0.0
        frequencies = Counter(document)
        length = len(document)
        average = self._average_length or 1.0
        k1 = 1.5
        b = 0.75
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            document_frequency = self._document_frequencies[token]
            inverse_document_frequency = math.log(
                1 + (len(self.chunks) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + k1 * (1 - b + b * length / average)
            score += inverse_document_frequency * frequency * (k1 + 1) / denominator
        return score
