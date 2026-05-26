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

# Static registry of every corpus this server knows about. doc_root is where
# the files live on disk; label feeds the tool description so the agent can
# route queries. Per-corpus tools are registered below for every entry whose
# doc_root exists (so lightweight installs don't see dead "not built" tools).
_DEPS = WORKSPACE / "slicer-dependencies"
CORPUS_REGISTRY = [
    ("source", WORKSPACE / "slicer-source",
     "the local Slicer source tree (slicer-source/) — C++ headers/sources, "
     "Python modules, CMake, and developer docs"),
    ("discourse", WORKSPACE / "slicer-discourse",
     "the local Slicer Discourse archive (slicer-discourse/) — "
     "~18,700 community forum threads as rendered Markdown"),
    ("ctk", _DEPS / "CTK",
     "the CTK (Common Toolkit) source — Qt widgets, DICOM utilities, and "
     "core abstractions used throughout Slicer's UI layer"),
    ("vtkaddon", _DEPS / "vtkAddon",
     "Slicer's vtkAddon library — VTK extensions shipped with Slicer "
     "(markups helpers, volume rendering utilities, etc.)"),
    ("slicerexecutionmodel", _DEPS / "SlicerExecutionModel",
     "SlicerExecutionModel — the framework for Slicer command-line modules "
     "(GenerateCLP, parameter XML descriptions, CLI plumbing)"),
]

CORPORA: dict[str, dict] = {}
for _name, _root, _label in CORPUS_REGISTRY:
    CORPORA[_name] = {
        "doc_root": _root,
        "label": _label,
        "bm25": BM25Index(name=_name, index_dir=BM25_ROOT / _name, doc_root=_root),
        "vector": VectorIndex(name=_name, index_dir=VECTOR_ROOT / _name, doc_root=_root),
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


def _make_search_tool(corpus_key: str, label: str):
    """Register one search_<corpus> tool. Each captures corpus_key in its closure."""

    def search(
        query: str,
        top_k: int = 10,
        mode: str = "auto",
        lexical_weight: float = 1.0,
        vector_weight: float = 1.0,
    ) -> str:
        return _run_search(corpus_key, query, top_k, mode, lexical_weight, vector_weight)

    search.__name__ = f"search_{corpus_key}"
    search.__doc__ = f"Search {corpus_key} by relevance."
    mcp.tool(
        name=f"search_{corpus_key}",
        description=SEARCH_DESCRIPTION_TEMPLATE.format(corpus_label=label),
    )(search)


# Register tools only for corpora whose doc_root actually exists, so lightweight
# installs (no slicer-dependencies/) don't see dead tools for unbuilt indexes.
for _name, _meta in CORPORA.items():
    if _meta["doc_root"].exists():
        _make_search_tool(_name, _meta["label"])


def _vector_build_status() -> dict | None:
    """If setup.sh kicked off a background vector build, report its state.

    Looks for the PID file written by setup.sh, checks whether the process
    is still alive, and tails the build log so the agent (or user) can see
    progress without leaving the chat.
    """
    pid_file = WORKSPACE / ".vector-build.pid"
    log_file = WORKSPACE / ".vector-build.log"
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        alive = False
    log_tail: list[str] = []
    if log_file.exists():
        try:
            log_tail = log_file.read_text().splitlines()[-3:]
        except OSError:
            pass
    return {"pid": pid, "alive": alive, "log_tail": log_tail}


@mcp.tool(description=(
    "List the available BM25 and vector indexes and their freshness. Use "
    "this to check whether the local indexes have been built and when, "
    "before calling search_source or search_discourse. If a vector index "
    "build was kicked off in the background by setup.sh, this also reports "
    "the build's PID, whether the process is still alive, and the last few "
    "lines of the build log."
))
def index_status() -> str:
    """Report which indexes are built, with manifest metadata for each.

    Also detects an in-progress background vector build (started by setup.sh
    with --indexes hybrid) and surfaces its PID + log tail so the caller
    knows whether vector search is "not built yet" vs "building right now,
    almost ready".
    """
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
                entry = {
                    "corpus": corpus_key,
                    "kind": kind,
                    "built": False,
                    "hint": "Run ./setup.sh --force to build all indexes",
                }
                # Vector indexes specifically may be mid-build by a background job
                if kind == "vector":
                    bs = _vector_build_status()
                    if bs is not None:
                        entry["building"] = bs["alive"]
                        entry["build_pid"] = bs["pid"]
                        entry["log_tail"] = bs["log_tail"]
                        if bs["alive"]:
                            entry["hint"] = (
                                "Background vector build in progress — "
                                "use lexical search now; vector/hybrid will "
                                "become available when the build completes."
                            )
                        else:
                            entry["hint"] = (
                                "Background vector build process is no longer "
                                "running but the index isn't on disk — check "
                                ".vector-build.log for errors."
                            )
                out.append(entry)
    return json.dumps(out, indent=2)


if __name__ == "__main__":
    mcp.run()
