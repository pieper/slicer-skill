#!/usr/bin/env python3
"""MCP server exposing hybrid (lexical + dense) search over the slicer-skill corpus.

Companion to slicer-mcp-server.py, which runs *inside* a live Slicer session
and exposes scene/control tools. This server runs as a stdio child process of
the agent and exposes ranked retrieval over the indexed local corpora.

Setup is handled by ./setup.sh: it creates the .venv next to this script,
installs dependencies, builds both the BM25 and vector indexes, and registers
this server in .mcp.json. No manual paths to configure.

Two tools — `search_source` and `search_discourse` — each support three modes:
  - "lexical":  BM25 only (fast, exact-keyword/identifier matches)
  - "vector":   dense embeddings only (semantic / paraphrase matches)
  - "hybrid":   Reciprocal Rank Fusion of both signals (default when both
                indexes exist; falls back to lexical if vector is missing)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ─── Self-bootstrap: re-exec under the sibling .venv ─────────────────────────
# We compare sys.prefix (the venv dir when active) rather than sys.executable,
# since the venv's python is a symlink to the system interpreter and resolve()
# would falsely equate the two.
_HERE = Path(__file__).resolve().parent
_VENV_DIR = _HERE / ".venv"
_VENV_PY = _VENV_DIR / "bin" / "python"
if _VENV_PY.exists() and Path(sys.prefix).resolve() != _VENV_DIR.resolve():
    os.execv(str(_VENV_PY), [str(_VENV_PY), __file__, *sys.argv[1:]])

import json  # noqa: E402

WORKSPACE = _HERE
sys.path.insert(0, str(WORKSPACE / "scripts"))

from bm25_lib import Index as BM25Index, SearchHit  # noqa: E402
from vector_lib import VectorIndex, rrf_fuse  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

BM25_ROOT = WORKSPACE / ".bm25-index"
VECTOR_ROOT = WORKSPACE / ".vector-index"

CORPORA = {
    "source": {
        "doc_root": WORKSPACE / "slicer-source",
        "bm25": BM25Index(name="source", index_dir=BM25_ROOT / "source",
                          doc_root=WORKSPACE / "slicer-source"),
        "vector": VectorIndex(name="source", index_dir=VECTOR_ROOT / "source",
                              doc_root=WORKSPACE / "slicer-source"),
    },
    "discourse": {
        "doc_root": WORKSPACE / "slicer-discourse",
        "bm25": BM25Index(name="discourse", index_dir=BM25_ROOT / "discourse",
                          doc_root=WORKSPACE / "slicer-discourse"),
        "vector": VectorIndex(name="discourse", index_dir=VECTOR_ROOT / "discourse",
                              doc_root=WORKSPACE / "slicer-discourse"),
    },
}

VALID_MODES = ("lexical", "vector", "hybrid")


def _resolve_mode(mode: str, corpus_key: str) -> str:
    """Map 'auto' or default to a concrete mode based on which indexes exist."""
    if mode == "auto":
        c = CORPORA[corpus_key]
        if c["vector"].index_dir.exists() and c["bm25"].index_dir.exists():
            return "hybrid"
        if c["bm25"].index_dir.exists():
            return "lexical"
        return "vector"
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES + ('auto',)}, got {mode!r}")
    return mode


def _run_search(
    corpus_key: str,
    query: str,
    top_k: int,
    mode: str,
    lexical_weight: float,
    vector_weight: float,
) -> str:
    if corpus_key not in CORPORA:
        return json.dumps({"error": f"unknown corpus {corpus_key!r}"})
    c = CORPORA[corpus_key]
    top_k = max(1, min(int(top_k), 50))
    resolved = _resolve_mode(mode, corpus_key)

    def need(idx, label: str) -> str | None:
        if not idx.index_dir.exists():
            return (f"Index for {label!r}/{corpus_key} not built. "
                    f"Run: ./setup.sh --force")
        return None

    hits: list[SearchHit]
    try:
        if resolved == "lexical":
            err = need(c["bm25"], "bm25")
            if err:
                return json.dumps({"error": err})
            hits = c["bm25"].search(query, top_k=top_k)
        elif resolved == "vector":
            err = need(c["vector"], "vector")
            if err:
                return json.dumps({"error": err})
            hits = c["vector"].search(query, top_k=top_k)
        else:  # hybrid
            err1 = need(c["bm25"], "bm25")
            err2 = need(c["vector"], "vector")
            if err1 and err2:
                return json.dumps({"error": err1})
            if err1:
                hits = c["vector"].search(query, top_k=top_k)
                resolved = "vector (bm25 missing)"
            elif err2:
                hits = c["bm25"].search(query, top_k=top_k)
                resolved = "lexical (vector missing)"
            else:
                # Pull a wider candidate pool from each side so fusion has room
                pool = max(top_k * 3, 30)
                lex_hits = c["bm25"].search(query, top_k=pool)
                vec_hits = c["vector"].search(query, top_k=pool)
                hits = rrf_fuse(
                    [lex_hits, vec_hits],
                    weights=[float(lexical_weight), float(vector_weight)],
                    top_k=top_k,
                )
    except Exception as e:  # noqa: BLE001 — surface any retriever error to the agent
        return json.dumps({"error": f"{type(e).__name__}: {e}"})

    return json.dumps({
        "corpus": corpus_key,
        "query": query,
        "mode": resolved,
        "lexical_weight": lexical_weight if resolved.startswith("hybrid") or "fuse" in resolved else None,
        "vector_weight": vector_weight if resolved.startswith("hybrid") or "fuse" in resolved else None,
        "results": [h.to_dict() for h in hits],
    }, indent=2)


SEARCH_DESCRIPTION_TEMPLATE = """Ranked search over {corpus_label}.

Three modes are available — pick by the *shape* of your query:

  mode="lexical"   BM25 over tokenized text. Fast, deterministic. Best when
                   the query is or contains a concrete identifier, symbol,
                   path, error string, or rare keyword (e.g. 'vtkMRMLScalarVolumeNode',
                   'arrayFromVolume', 'CMAKE_PREFIX_PATH').

  mode="vector"    Dense embeddings (sentence-transformers/all-MiniLM-L6-v2).
                   Best for conceptual
                   or paraphrased queries where the answer probably uses
                   different words than the question (e.g. 'how do I draw a
                   bounding box around the lesion', 'why does my volume look
                   flipped after export').

  mode="hybrid"    Reciprocal Rank Fusion of both signals; the safe default
                   when you don't know which side will fire. Honors
                   `lexical_weight` and `vector_weight` (defaults 1.0 each)
                   if you want to bias toward one signal for a given query.

  mode="auto"      Pick hybrid when both indexes exist, else fall back.
                   This is the default.

Returns top-K matches as {{path, abs_path, score, line, snippet}}. Use the
standard Read tool with abs_path to fetch full files.
"""


mcp = FastMCP("slicer-skill-search")


@mcp.tool(description=SEARCH_DESCRIPTION_TEMPLATE.format(
    corpus_label=("the local Slicer source tree (slicer-source/) — C++ "
                  "headers/sources, Python modules, CMake, and developer docs")))
def search_source(
    query: str,
    top_k: int = 10,
    mode: str = "auto",
    lexical_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> str:
    """Search slicer-source by relevance."""
    return _run_search("source", query, top_k, mode, lexical_weight, vector_weight)


@mcp.tool(description=SEARCH_DESCRIPTION_TEMPLATE.format(
    corpus_label=("the local Slicer Discourse archive (slicer-discourse/) — "
                  "~18,700 community forum threads as rendered Markdown")))
def search_discourse(
    query: str,
    top_k: int = 10,
    mode: str = "auto",
    lexical_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> str:
    """Search slicer-discourse by relevance."""
    return _run_search("discourse", query, top_k, mode, lexical_weight, vector_weight)


@mcp.tool(description=(
    "List the available BM25 and vector indexes and their freshness. Use "
    "this to check whether the local indexes have been built and when, "
    "before calling search_source or search_discourse."
))
def index_status() -> str:
    """Report which indexes are built, with manifest metadata for each."""
    out = []
    for corpus_key, c in CORPORA.items():
        for kind in ("bm25", "vector"):
            idx = c[kind]
            manifest_path = idx.index_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text())
                except (OSError, json.JSONDecodeError) as e:
                    manifest = {"error": str(e)}
                out.append({"corpus": corpus_key, "kind": kind, "built": True, **manifest})
            else:
                out.append({
                    "corpus": corpus_key,
                    "kind": kind,
                    "built": False,
                    "hint": "Run ./setup.sh --force to build all indexes",
                })
    return json.dumps(out, indent=2)


if __name__ == "__main__":
    mcp.run()
