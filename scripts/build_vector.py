#!/usr/bin/env python3
"""Build dense (vector) indexes over slicer-source and slicer-discourse for
hybrid search in the slicer-skill MCP server.

Run with the workspace's .venv:

    .venv/bin/python scripts/build_vector.py            # both indexes
    .venv/bin/python scripts/build_vector.py --target source
    .venv/bin/python scripts/build_vector.py --target discourse

Indexes are written to .vector-index/{source,discourse}/.
The model is BAAI/bge-small-en-v1.5 (auto-downloaded on first run, ~30 MB).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_bm25 import (  # noqa: E402  — reuse the same file walker
    DISCOURSE_DIR,
    SOURCE_DIR,
    iter_discourse_files,
    iter_source_files,
    load_documents,
    parse_extra,
)
from vector_lib import (  # noqa: E402
    EMBED_DIM,
    EMBED_MODEL,
    chunk_lines,
    embed_batches,
)

WORKSPACE = Path(__file__).resolve().parent.parent
INDEX_DIR = WORKSPACE / ".vector-index"


def make_searchable_chunk(path: str, chunk_text: str) -> str:
    """Prepend the path so the embedding has filename context."""
    return f"{path}\n\n{chunk_text}"


def build_index(name: str, docs: list[tuple[str, str]], out_dir: Path) -> None:
    if not docs:
        print(f"[{name}] no documents found, skipping")
        return

    # 1) chunk
    print(f"[{name}] chunking {len(docs)} documents...")
    t0 = time.time()
    chunk_records: list[tuple[str, int, int]] = []  # (path, l0, l1)
    chunk_texts: list[str] = []
    for path, text in docs:
        for l0, l1, body in chunk_lines(text):
            chunk_records.append((path, l0, l1))
            chunk_texts.append(make_searchable_chunk(path, body))
    print(f"[{name}] {len(chunk_texts)} chunks in {time.time() - t0:.1f}s")

    # 2) embed
    print(f"[{name}] loading {EMBED_MODEL}...")
    from fastembed import TextEmbedding  # local import — first use triggers a download
    model = TextEmbedding(EMBED_MODEL)

    print(f"[{name}] embedding {len(chunk_texts)} chunks...")
    t0 = time.time()
    vectors = embed_batches(chunk_texts, model, batch_size=64)
    dt = time.time() - t0
    print(f"[{name}] embedded in {dt:.1f}s ({len(chunk_texts) / dt:.0f} chunks/s), "
          f"shape={vectors.shape}, dtype={vectors.dtype}")

    # 3) save
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "vectors.npy", vectors)
    with (out_dir / "chunks.jsonl").open("w") as f:
        for path, l0, l1 in chunk_records:
            f.write(json.dumps({"path": path, "l0": l0, "l1": l1}) + "\n")

    manifest = {
        "name": name,
        "doc_count": len(docs),
        "chunk_count": len(chunk_texts),
        "embed_model": EMBED_MODEL,
        "embed_dim": EMBED_DIM,
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
    parser.add_argument(
        "--extra", action="append", default=[], metavar="NAME=PATH",
        help="Index an additional source-style corpus at PATH under index name NAME. Repeatable.",
    )
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

    for spec in args.extra:
        name, root = parse_extra(spec)
        if not root.exists():
            print(f"[{name}] not found at {root} — skipping")
            continue
        print(f"[{name}] scanning {root}...")
        paths = list(iter_source_files(root))
        print(f"[{name}] {len(paths)} candidate files")
        docs = load_documents(paths, root)
        build_index(name, docs, args.out / name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
