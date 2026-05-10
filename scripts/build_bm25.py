#!/usr/bin/env python3
"""Build BM25 indexes over slicer-source and slicer-discourse for the search MCP server.

Run with the workspace's .venv:

    .venv/bin/python scripts/build_bm25.py            # both indexes
    .venv/bin/python scripts/build_bm25.py --target source
    .venv/bin/python scripts/build_bm25.py --target discourse

Indexes are written to .bm25-index/{source,discourse}/ in the workspace.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

import bm25s

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bm25_lib import tokenize  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent.parent

SOURCE_DIR = WORKSPACE / "slicer-source"
DISCOURSE_DIR = WORKSPACE / "slicer-discourse"
INDEX_DIR = WORKSPACE / ".bm25-index"

SOURCE_EXTENSIONS = {
    ".py", ".cxx", ".cpp", ".cc", ".c", ".h", ".hxx", ".hpp",
    ".md", ".rst", ".txt", ".cmake", ".ui", ".xml", ".json", ".yml", ".yaml",
}
SOURCE_FILE_NAMES = {"CMakeLists.txt", "Doxyfile"}
SKIP_DIRS = {".git", "__pycache__", "build", "dist", "node_modules", ".venv"}

MAX_FILE_BYTES = 2_000_000  # skip files larger than 2 MB


def iter_source_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SOURCE_FILE_NAMES or path.suffix.lower() in SOURCE_EXTENSIONS:
            yield path


def iter_discourse_files(root: Path) -> Iterator[Path]:
    rendered = root / "archive" / "rendered-topics"
    if not rendered.exists():
        return
    yield from rendered.rglob("*.md")


def load_documents(paths: Iterable[Path], root: Path) -> list[tuple[str, str]]:
    """Read files into (relpath, text). Skip oversized or unreadable files."""
    docs: list[tuple[str, str]] = []
    skipped_size = 0
    skipped_io = 0
    for p in paths:
        try:
            size = p.stat().st_size
        except OSError:
            skipped_io += 1
            continue
        if size > MAX_FILE_BYTES:
            skipped_size += 1
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped_io += 1
            continue
        rel = str(p.relative_to(root))
        docs.append((rel, text))
    if skipped_size or skipped_io:
        print(f"  skipped: {skipped_size} oversize, {skipped_io} unreadable")
    return docs


def make_searchable_text(path: str, text: str) -> str:
    """Prepend the path to the text so path tokens are searchable.

    The tokenizer splits on non-word chars, so slashes/dots become separators
    and each path component becomes its own token (then CamelCase-split too).
    """
    return f"{path}\n{text}"


def build_index(name: str, docs: list[tuple[str, str]], out_dir: Path) -> None:
    if not docs:
        print(f"[{name}] no documents found, skipping")
        return

    print(f"[{name}] tokenizing {len(docs)} documents...")
    t0 = time.time()
    corpus_tokens = [tokenize(make_searchable_text(p, t)) for p, t in docs]
    n_tokens = sum(len(toks) for toks in corpus_tokens)
    print(f"[{name}] tokenized {n_tokens:,} tokens in {time.time() - t0:.1f}s")

    print(f"[{name}] building BM25 index...")
    t0 = time.time()
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens, show_progress=False)
    print(f"[{name}] indexed in {time.time() - t0:.1f}s")

    out_dir.mkdir(parents=True, exist_ok=True)
    # Save corpus as a list of dicts so each search hit can be mapped back to a path
    corpus_records = [{"path": p, "size": len(t)} for p, t in docs]
    retriever.save(str(out_dir), corpus=corpus_records)

    # Index manifest — versions and counts for sanity checking at load time
    manifest = {
        "name": name,
        "doc_count": len(docs),
        "token_count": n_tokens,
        "bm25s_version": bm25s.__version__,
        "tokenizer_version": 1,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    size_mb = sum(p.stat().st_size for p in out_dir.iterdir() if p.is_file()) / 1e6
    print(f"[{name}] wrote {size_mb:.1f} MB to {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("source", "discourse", "all"), default="all")
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--discourse-dir", type=Path, default=DISCOURSE_DIR)
    parser.add_argument("--out", type=Path, default=INDEX_DIR)
    args = parser.parse_args()

    if args.target in ("source", "all"):
        if not args.source_dir.exists():
            print(f"[source] not found at {args.source_dir} — run setup.sh first")
        else:
            print(f"[source] scanning {args.source_dir}...")
            paths = list(iter_source_files(args.source_dir))
            print(f"[source] {len(paths)} candidate files")
            docs = load_documents(paths, args.source_dir)
            build_index("source", docs, args.out / "source")

    if args.target in ("discourse", "all"):
        if not args.discourse_dir.exists():
            print(f"[discourse] not found at {args.discourse_dir} — run setup.sh first")
        else:
            print(f"[discourse] scanning {args.discourse_dir}...")
            paths = list(iter_discourse_files(args.discourse_dir))
            print(f"[discourse] {len(paths)} candidate files")
            docs = load_documents(paths, args.discourse_dir)
            build_index("discourse", docs, args.out / "discourse")

    return 0


if __name__ == "__main__":
    sys.exit(main())
