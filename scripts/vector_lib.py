"""Shared dense-retrieval helpers: chunking, vector index load/search, and RRF
fusion with the BM25 results from bm25_lib.

Used by scripts/build_vector.py (indexing) and slicer-skill-search-mcp.py
(querying). The model is BAAI/bge-small-en-v1.5 served via fastembed (ONNX,
no PyTorch). Vectors are unit-normalized, so cosine similarity reduces to a
dot product.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from bm25_lib import SearchHit  # noqa: E402

# MiniLM-L6-v2 is the workhorse small embedding model: 22M params, 384-dim
# (same as BGE-small), 256-token context. Roughly 5–10× faster than
# BAAI/bge-small-en-v1.5 on CPU at the cost of a few percent on benchmark
# precision — a good trade in a hybrid stack where BM25 carries the
# keyword-precise leg.
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384

# Chunking parameters sized to MiniLM-L6's 256-token context (~800 chars).
# Sizing to fit (rather than overflow and truncate) lets the model see every
# char, and improves retrieval precision since each vector represents a
# focused region instead of a bag of mixed contexts.
DEFAULT_MAX_LINES = 20
DEFAULT_OVERLAP_LINES = 4
DEFAULT_MAX_CHARS = 800


def chunk_lines(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[tuple[int, int, str]]:
    """Split text into overlapping chunks. Returns (line_start, line_end, text)
    with 1-based inclusive line numbers.
    """
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return []
    if n <= max_lines:
        body = "\n".join(lines)
        return [(1, n, body[:max_chars])]

    chunks: list[tuple[int, int, str]] = []
    start = 0
    step = max(1, max_lines - overlap_lines)
    while start < n:
        end = min(start + max_lines, n)
        body = "\n".join(lines[start:end])
        chunks.append((start + 1, end, body[:max_chars]))
        if end >= n:
            break
        start += step
    return chunks


@dataclass
class Chunk:
    """One indexed chunk: where it came from, so we can return the snippet later."""
    path: str
    line_start: int
    line_end: int


class VectorIndex:
    """Loaded dense index: numpy vectors + chunk metadata + lazy-loaded model.

    Layout on disk:
      <index_dir>/vectors.npy        — float32 [N, D], rows L2-normalized
      <index_dir>/chunks.jsonl       — one record per row: {path, l0, l1}
      <index_dir>/manifest.json      — model name, dim, doc/chunk counts, build time
    """

    def __init__(self, name: str, index_dir: Path, doc_root: Path):
        self.name = name
        self.index_dir = index_dir
        self.doc_root = doc_root
        self._vectors: np.ndarray | None = None
        self._chunks: list[Chunk] | None = None
        self._model = None  # type: ignore[var-annotated]

    @property
    def vectors(self) -> np.ndarray:
        if self._vectors is None:
            self._vectors = np.load(self.index_dir / "vectors.npy", mmap_mode="r")
        return self._vectors

    @property
    def chunks(self) -> list[Chunk]:
        if self._chunks is None:
            self._chunks = []
            with (self.index_dir / "chunks.jsonl").open() as f:
                for line in f:
                    rec = json.loads(line)
                    self._chunks.append(Chunk(rec["path"], rec["l0"], rec["l1"]))
        return self._chunks

    def _embed_query(self, query: str) -> np.ndarray:
        if self._model is None:
            from fastembed import TextEmbedding  # imported lazily — avoids cold-start
            self._model = TextEmbedding(EMBED_MODEL)
        # query_embed yields one np.ndarray per input; bge expects no special prefix
        # for retrieval queries, but adding "query: " is the documented convention.
        vec = next(self._model.query_embed([query]))
        return np.asarray(vec, dtype=np.float32)

    def search(
        self, query: str, top_k: int = 10, file_dedup: bool = True,
    ) -> list[SearchHit]:
        q = self._embed_query(query)
        # vectors are pre-normalized → cosine similarity is just dot product
        scores = self.vectors @ q
        # Take more than top_k so dedup-by-file still leaves enough results
        oversample = top_k * 5 if file_dedup else top_k
        oversample = min(oversample, len(scores))
        # argpartition for an unordered top set, then sort just those
        part = np.argpartition(scores, -oversample)[-oversample:]
        ordered = part[np.argsort(-scores[part])]

        seen_paths: set[str] = set()
        hits: list[SearchHit] = []
        for idx in ordered:
            ck = self.chunks[int(idx)]
            if file_dedup and ck.path in seen_paths:
                continue
            seen_paths.add(ck.path)
            abs_path = self.doc_root / ck.path
            snippet = _read_lines(abs_path, ck.line_start, ck.line_end)
            hits.append(SearchHit(
                path=ck.path,
                abs_path=str(abs_path),
                score=float(scores[idx]),
                line=ck.line_start,
                snippet=snippet,
            ))
            if len(hits) >= top_k:
                break
        return hits


def _read_lines(path: Path, line_start: int, line_end: int, max_lines: int = 8) -> str:
    """Read up to max_lines starting from line_start (1-based inclusive)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    start = max(0, line_start - 1)
    # Honor the chunk's actual range but cap snippet length so the agent isn't
    # flooded with a 40-line chunk in every result
    end = min(len(lines), start + min(max_lines, line_end - line_start + 1))
    return "\n".join(lines[start:end])


def rrf_fuse(
    hit_lists: list[list[SearchHit]],
    weights: list[float] | None = None,
    k: int = 60,
    top_k: int = 10,
) -> list[SearchHit]:
    """Reciprocal Rank Fusion across multiple ranked SearchHit lists.

    score(d) = Σ_i  weight_i / (k + rank_i(d))    # 1-based rank

    Hits are matched by `path`. When the same path appears in multiple lists,
    its representative SearchHit is taken from the list where it ranked best
    (so the snippet/line come from whichever signal liked it most).
    """
    if weights is None:
        weights = [1.0] * len(hit_lists)

    fused_scores: dict[str, float] = {}
    best_hit: dict[str, tuple[int, SearchHit]] = {}  # path → (rank, hit)
    for w, hits in zip(weights, hit_lists):
        for rank, hit in enumerate(hits, start=1):
            contribution = w / (k + rank)
            fused_scores[hit.path] = fused_scores.get(hit.path, 0.0) + contribution
            prev = best_hit.get(hit.path)
            if prev is None or rank < prev[0]:
                best_hit[hit.path] = (rank, hit)

    # Sort paths by fused score descending; produce SearchHits with fused score.
    ordered = sorted(fused_scores.items(), key=lambda kv: -kv[1])[:top_k]
    out: list[SearchHit] = []
    for path, fused in ordered:
        _, h = best_hit[path]
        out.append(SearchHit(
            path=h.path,
            abs_path=h.abs_path,
            score=round(fused, 6),
            line=h.line,
            snippet=h.snippet,
        ))
    return out


def embed_batches(
    texts: list[str],
    model,
    batch_size: int = 256,
    parallel: int | None = None,
    progress_every: int = 2000,
) -> np.ndarray:
    """Embed all texts and stack into one contiguous float32 array.

    Hands the full list to fastembed and lets its internal batching +
    parallelism pipeline the work. Logs throughput periodically so a build
    that takes minutes shows progress instead of looking hung.
    """
    import time as _time
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)

    rows: list[np.ndarray] = []
    total = len(texts)
    t0 = _time.time()
    last_logged = 0
    for i, v in enumerate(model.embed(texts, batch_size=batch_size, parallel=parallel)):
        rows.append(np.asarray(v, dtype=np.float32))
        if (i + 1) - last_logged >= progress_every:
            last_logged = i + 1
            elapsed = _time.time() - t0
            rate = (i + 1) / elapsed
            eta = (total - i - 1) / rate if rate > 0 else 0
            print(f"  embedded {i + 1}/{total}  ({rate:.0f} chunks/s, "
                  f"~{eta:.0f}s remaining)", flush=True)
    return np.stack(rows, axis=0)
