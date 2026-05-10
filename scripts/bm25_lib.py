"""Shared BM25 tokenizer and search helpers.

Used by both `scripts/build_bm25.py` (indexing) and
`slicer-skill-search-mcp.py` (querying). Keeping them in one place ensures
the same tokenization rules at index time and query time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import bm25s

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+")
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str) -> list[str]:
    """BM25 tokenizer for mixed code+prose.

    Emits each identifier as a whole token (so `vtkMRMLScalarVolumeNode` matches
    exactly) AND the CamelCase / snake_case parts (so a query for `volume node`
    matches it too). Lowercases everything. No stemming, no stopword removal.
    """
    out: list[str] = []
    for m in WORD_RE.finditer(text):
        w = m.group(0)
        wl = w.lower()
        out.append(wl)
        if "_" in w:
            for part in w.split("_"):
                if part:
                    out.append(part.lower())
        if any(c.isupper() for c in w) and any(c.islower() for c in w):
            for sub in CAMEL_RE.findall(w):
                sl = sub.lower()
                if sl and sl != wl:
                    out.append(sl)
    return out


@dataclass
class SearchHit:
    path: str          # path relative to the index root (e.g. slicer-source/)
    abs_path: str      # absolute path on this machine
    score: float
    line: int          # 1-based line number of the best snippet, 0 if none
    snippet: str       # ~5-line excerpt centered on the best query-term hit

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "abs_path": self.abs_path,
            "score": round(self.score, 3),
            "line": self.line,
            "snippet": self.snippet,
        }


class Index:
    """Loaded BM25 index plus the on-disk root for the documents it indexes."""

    def __init__(self, name: str, index_dir: Path, doc_root: Path):
        self.name = name
        self.index_dir = index_dir
        self.doc_root = doc_root
        self._retriever: bm25s.BM25 | None = None

    @property
    def retriever(self) -> bm25s.BM25:
        if self._retriever is None:
            self._retriever = bm25s.BM25.load(str(self.index_dir), load_corpus=True)
        return self._retriever

    def search(self, query: str, top_k: int = 10, snippet_lines: int = 5) -> list[SearchHit]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        q_token_set = set(q_tokens)
        docs, scores = self.retriever.retrieve(
            [q_tokens], k=top_k, show_progress=False
        )
        hits: list[SearchHit] = []
        for doc, score in zip(docs[0], scores[0]):
            rel = doc["path"]
            abs_path = self.doc_root / rel
            line, snippet = _best_snippet(abs_path, q_token_set, snippet_lines)
            hits.append(SearchHit(
                path=rel,
                abs_path=str(abs_path),
                score=float(score),
                line=line,
                snippet=snippet,
            ))
        return hits


def _best_snippet(path: Path, query_tokens: set[str], n_lines: int) -> tuple[int, str]:
    """Return (1-based line, ~n_lines excerpt) centered on the best query-term hit.

    If no line matches any query token, returns the first non-empty line and
    a window from there.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (0, "")
    lines = text.splitlines()
    if not lines:
        return (0, "")

    best_idx = -1
    best_score = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        line_tokens = set(tokenize(line))
        s = len(line_tokens & query_tokens)
        if s > best_score:
            best_score = s
            best_idx = i

    if best_idx < 0:
        for i, line in enumerate(lines):
            if line.strip():
                best_idx = i
                break
        if best_idx < 0:
            return (0, "")

    half = n_lines // 2
    start = max(0, best_idx - half)
    end = min(len(lines), start + n_lines)
    start = max(0, end - n_lines)
    snippet = "\n".join(lines[start:end])
    return (best_idx + 1, snippet)
