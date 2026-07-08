"""
LEON Spec Indexer — Build and populate the Azure AI Search index.

Run this script ONCE to index all accessible specification files
(template, writing guide, uploaded specs) into Azure AI Search.

After running, the Azure Function will use hybrid vector+text search
instead of local TF-IDF keyword matching.

Prerequisites:
  1. Azure AI Search resource created in Azure Portal
  2. AZURE_SEARCH_ENDPOINT + AZURE_SEARCH_API_KEY in .env or environment
  3. Azure OpenAI credentials configured (for embeddings)

Usage:
  .venv\Scripts\python.exe scripts\index_specs_to_search.py
  .venv\Scripts\python.exe scripts\index_specs_to_search.py --rebuild
  .venv\Scripts\python.exe scripts\index_specs_to_search.py --stats-only
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import app.config  # noqa: E402 — loads .env, sets up paths
from app.qa.retrieval import build_index, Chunk  # noqa: E402
from app.embeddings import get_embedding  # noqa: E402
from app.qa.azure_search import (  # noqa: E402
    recreate_index,
    upload_documents,
    get_document_count,
    is_configured,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("leon-indexer")

# text-embedding-3-large max input: 8191 tokens ≈ ~32000 chars
_EMBED_MAX_CHARS = 8000


def index_all_chunks(batch_size: int = 50, target_index: str = "") -> dict:
    """
    Read all spec files, chunk them, generate embeddings, push to Azure Search.

    Args:
        batch_size:     number of chunks per Azure Search upload batch
        target_index:   index name override (empty = use config default)

    Returns:
        dict with stats: total_chunks, indexed, embedding_ms, upload_ms, errors
    """
    chunks = build_index()
    total = len(chunks)
    if total == 0:
        logger.warning("No chunks found — is data/uploads/ or data/refs/ populated?")
        return {"total_chunks": 0, "indexed": 0, "errors": 0}

    logger.info(f"Found {total} chunks across {len(set(c.file_name for c in chunks))} files")
    logger.info("Generating embeddings via Azure OpenAI text-embedding-3-large...")

    indexed = 0
    errors = 0
    embedding_total_ms = 0
    upload_total_ms = 0

    for batch_start in range(0, total, batch_size):
        batch = chunks[batch_start:batch_start + batch_size]
        documents = []

        # ── Step 1: Generate embeddings (client-side) ──────────────
        for ch in batch:
            text_for_embed = ch.text[:_EMBED_MAX_CHARS]
            t0 = time.perf_counter()
            try:
                embedding = get_embedding(text_for_embed)
            except Exception as exc:
                logger.error(
                    f"  Embedding failed for {ch.file_name}#{ch.chunk_id}: {exc}"
                )
                errors += 1
                continue
            embedding_total_ms += (time.perf_counter() - t0) * 1000

            # Determine source type for filtering
            if "template" in ch.file_name.lower():
                source = "reference"
            elif "guide" in ch.file_name.lower() or "writing" in ch.file_name.lower():
                source = "reference"
            else:
                source = "uploaded"

            # Azure Search document keys may contain only letters, digits,
            # underscore, dash, or equals — sanitize the filename-derived id.
            doc_id = re.sub(r"[^A-Za-z0-9_\-=]", "_", f"{ch.file_name}__{ch.chunk_id}")
            documents.append({
                "id": doc_id,
                "file_name": ch.file_name,
                "text": ch.text,
                "section": ch.section,
                "chunk_id": ch.chunk_id,
                "source_type": source,
                "embedding": embedding,
            })

        if not documents:
            continue

        # ── Step 2: Upload batch to Azure Search ───────────────────
        t0 = time.perf_counter()
        try:
            count = upload_documents(documents, index_name=target_index or None)
            indexed += count
            if count < len(documents):
                errors += (len(documents) - count)
        except Exception as exc:
            logger.error(f"  Upload batch failed: {exc}")
            errors += len(documents)
        upload_total_ms += (time.perf_counter() - t0) * 1000

        progress = min(batch_start + batch_size, total)
        logger.info(
            f"  [{progress}/{total}] {indexed} indexed, "
            f"~{embedding_total_ms/progress:.0f}ms/embed, "
            f"~{upload_total_ms/(batch_start/batch_size+1):.0f}ms/upload"
        )

    return {
        "total_chunks": total,
        "indexed": indexed,
        "errors": errors,
        "embedding_ms": round(embedding_total_ms),
        "upload_ms": round(upload_total_ms),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Index LEON spec files into Azure AI Search"
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Delete and recreate the search index before indexing"
    )
    parser.add_argument(
        "--stats-only", action="store_true",
        help="Show index statistics without re-indexing"
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Number of chunks per upload batch (default: 50)"
    )
    parser.add_argument(
        "--index", type=str, default="",
        help="Target index name (default: from AZURE_SEARCH_INDEX_NAME config)"
    )
    args = parser.parse_args()

    # ── Pre-flight checks ──────────────────────────────────────────
    if not is_configured():
        logger.error(
            "Azure AI Search is NOT configured.\n"
            "  Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY in .env\n"
            "  or as environment variables, then retry."
        )
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("LEON Spec Indexer — Azure AI Search")
    logger.info(f"  Endpoint: {app.config.AZURE_SEARCH_ENDPOINT}")
    logger.info(f"  Index:    {args.index or app.config.AZURE_SEARCH_INDEX_NAME}")
    logger.info(f"  Embed:    {app.config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT}")
    logger.info("=" * 60)

    # ── Stats-only mode ────────────────────────────────────────────
    if args.stats_only:
        count = get_document_count(index_name=args.index or None)
        logger.info(f"Current document count: {count}")
        return

    # ── Rebuild index if requested ─────────────────────────────────
    if args.rebuild:
        logger.info("Rebuilding index from scratch...")
        recreate_index(args.index or None)
    else:
        # Ensure index exists (idempotent)
        from app.qa.azure_search import create_index_if_not_exists
        create_index_if_not_exists(args.index or None)

    # ── Index all chunks ───────────────────────────────────────────
    t_start = time.perf_counter()
    stats = index_all_chunks(batch_size=args.batch_size, target_index=args.index)
    total_sec = time.perf_counter() - t_start

    # ── Final report ───────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("INDEXING COMPLETE")
    logger.info(f"  Total chunks:     {stats['total_chunks']}")
    logger.info(f"  Successfully indexed: {stats['indexed']}")
    logger.info(f"  Errors:           {stats['errors']}")
    logger.info(f"  Embedding time:   {stats.get('embedding_ms', 0)/1000:.1f}s")
    logger.info(f"  Upload time:      {stats.get('upload_ms', 0)/1000:.1f}s")
    logger.info(f"  Total wall time:  {total_sec:.1f}s")
    logger.info("=" * 60)

    if stats["indexed"] > 0:
        logger.info(
            "✅ Azure AI Search is ready. The Function App will now use\n"
            "   hybrid vector+text search instead of TF-IDF keywords."
        )
    if stats["errors"] > 0:
        logger.warning(
            f"⚠ {stats['errors']} chunks failed to index. Review logs above."
        )


if __name__ == "__main__":
    main()
