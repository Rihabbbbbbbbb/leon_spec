"""
Azure AI Search integration for LEON Q&A + Validation Assistant.

Provides hybrid (vector + full-text) search replacing the current
TF-IDF keyword retrieval with production-grade semantic search.

Architecture:
  INDEXING (one-time):
    DOCX/PDF → extract_text → chunk → get_embedding(Azure OpenAI)
    → push to Azure AI Search Index (vector + text + metadata)

  QUERY (every request):
    Question → get_embedding(Azure OpenAI, 1 call)
    → Azure AI Search hybrid query (vector + full-text, 1 call)
    → Top-K chunks with @search.score
    → LLM synthesis

Fallback: If Azure Search is not configured or unreachable,
falls back gracefully to local keyword_retrieve().

References:
  https://learn.microsoft.com/en-us/azure/search/vector-search-overview
  https://pypi.org/project/azure-search-documents/
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from app.config import (
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
    AZURE_SEARCH_API_KEY,
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_INDEX_NAME,
)

# text-embedding-3-large produces 3072-dimensional vectors
_EMBEDDING_DIMENSIONS = 3072

# ── Index definition ────────────────────────────────────────────────
def get_index_definition(index_name: Optional[str] = None) -> SearchIndex:
    """
    Build the LEON specs search index schema.

    Fields:
      - id          : unique key (file_name + chunk_id)
      - file_name   : source document name (searchable)
      - text        : chunk content (searchable full-text)
      - section     : document section heading (filterable)
      - chunk_id    : chunk ordinal within file
      - source_type : 'reference' (template/guide) or 'uploaded' (user specs)
      - embedding   : 3072-dim vector for semantic search (HNSW indexed)
    """
    name = index_name or AZURE_SEARCH_INDEX_NAME
    return SearchIndex(
        name=name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(
                name="file_name", type=SearchFieldDataType.String,
                filterable=True, sortable=True,
            ),
            SearchableField(
                name="text", type=SearchFieldDataType.String,
                analyzer_name="fr.microsoft",  # French-aware analyzer for Stellantis specs
            ),
            SimpleField(
                name="section", type=SearchFieldDataType.String,
                filterable=True, sortable=True,
            ),
            SimpleField(
                name="chunk_id", type=SearchFieldDataType.Int32,
                filterable=True, sortable=True,
            ),
            SimpleField(
                name="source_type", type=SearchFieldDataType.String,
                filterable=True,
            ),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=_EMBEDDING_DIMENSIONS,
                vector_search_profile_name="leon-hnsw-profile",
                hidden=False,
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="leon-hnsw-algo",
                    parameters={
                        "m": 4,                     # bi-directional links per node
                        "efConstruction": 400,      # build-time search width
                        "efSearch": 500,            # query-time search width
                        "metric": "cosine",
                    },
                )
            ],
            profiles=[
                VectorSearchProfile(
                    name="leon-hnsw-profile",
                    algorithm_configuration_name="leon-hnsw-algo",
                )
            ],
        ),
    )


# ── Client factories (lazy singletons) ──────────────────────────────
_search_client: Optional[SearchClient] = None
_admin_client: Optional[SearchIndexClient] = None


def _get_credential() -> AzureKeyCredential:
    """Return AzureKeyCredential from config; raises if not configured."""
    if not AZURE_SEARCH_ENDPOINT or not AZURE_SEARCH_API_KEY:
        raise RuntimeError(
            "Azure AI Search not configured. Set AZURE_SEARCH_ENDPOINT "
            "and AZURE_SEARCH_API_KEY in .env or environment variables."
        )
    return AzureKeyCredential(AZURE_SEARCH_API_KEY)


def get_search_client(index_name: Optional[str] = None) -> SearchClient:
    """Return a SearchClient for querying the LEON index."""
    global _search_client
    idx = index_name or AZURE_SEARCH_INDEX_NAME
    if _search_client is None or _search_client._index_name != idx:
        _search_client = SearchClient(
            endpoint=AZURE_SEARCH_ENDPOINT,
            index_name=idx,
            credential=_get_credential(),
        )
    return _search_client


def get_admin_client() -> SearchIndexClient:
    """Return a SearchIndexClient for managing indexes."""
    global _admin_client
    if _admin_client is None:
        _admin_client = SearchIndexClient(
            endpoint=AZURE_SEARCH_ENDPOINT,
            credential=_get_credential(),
        )
    return _admin_client


# ── Index lifecycle ─────────────────────────────────────────────────
def create_index_if_not_exists(index_name: Optional[str] = None) -> bool:
    """
    Create the LEON search index. Returns True if created, False if
    already exists or creation failed.
    """
    name = index_name or AZURE_SEARCH_INDEX_NAME
    admin = get_admin_client()
    try:
        existing = list(admin.list_indexes())
        existing_names = [idx.name for idx in existing]
        if name in existing_names:
            logging.info(f"Index '{name}' already exists — skipping creation.")
            return False
    except Exception as exc:
        logging.warning(f"Could not list indexes: {exc}")

    definition = get_index_definition(name)
    try:
        admin.create_index(definition)
        logging.info(f"Created Azure Search index '{name}' "
                     f"({_EMBEDDING_DIMENSIONS}-dim vectors, HNSW)")
        return True
    except Exception as exc:
        logging.error(f"Failed to create index '{name}': {exc}")
        raise


def delete_index(index_name: Optional[str] = None) -> bool:
    """Delete the LEON search index. Returns True if deleted."""
    name = index_name or AZURE_SEARCH_INDEX_NAME
    admin = get_admin_client()
    try:
        admin.delete_index(name)
        logging.info(f"Deleted Azure Search index '{name}'")
        return True
    except Exception as exc:
        logging.warning(f"Could not delete index '{name}': {exc}")
        return False


def recreate_index(index_name: Optional[str] = None) -> bool:
    """Delete and recreate the index from scratch."""
    delete_index(index_name)
    return create_index_if_not_exists(index_name)


# ── Document operations ─────────────────────────────────────────────
def upload_documents(
    documents: List[dict],
    index_name: Optional[str] = None,
) -> int:
    """
    Upload a batch of documents to the search index.

    Each document dict must have:
      - id, file_name, text, section, chunk_id, source_type, embedding

    Returns the number of documents successfully indexed.
    """
    client = get_search_client(index_name)
    try:
        result = client.upload_documents(documents)
        succeeded = sum(1 for r in result if r.succeeded)
        if succeeded < len(documents):
            logging.warning(
                f"Uploaded {succeeded}/{len(documents)} documents; "
                f"some failed."
            )
        return succeeded
    except Exception as exc:
        logging.error(f"Document upload failed: {exc}")
        raise


def get_document_count(index_name: Optional[str] = None) -> int:
    """Return the total number of documents in the index."""
    client = get_search_client(index_name)
    try:
        return client.get_document_count()
    except Exception:
        return 0


# ── Hybrid search query ─────────────────────────────────────────────
def hybrid_search(
    question: str,
    query_embedding: List[float],
    top_k: int = 5,
    filter_expr: Optional[str] = None,
    index_name: Optional[str] = None,
) -> List[dict]:
    """
    Execute a hybrid (vector + full-text) search against the LEON index.

    Args:
        question:                user's natural-language question
        query_embedding:         embedding vector for the question
        top_k:                   max results to return
        filter_expr:             optional OData filter (e.g. "source_type eq 'uploaded'")
        index_name:              index name override

    Returns:
        List of result dicts with keys: file_name, text, section, chunk_id,
        source_type, @search.score, @search.reranker_score
    """
    client = get_search_client(index_name)

    vector_query = VectorizedQuery(
        vector=query_embedding,
        k_nearest_neighbors=top_k,
        fields="embedding",
        kind="vector",
    )

    kwargs = {
        "search_text": question,          # full-text keyword search (hybrid)
        "vector_queries": [vector_query],  # vector similarity search
        "top": top_k,
        "select": ["file_name", "text", "section", "chunk_id", "source_type"],
    }
    if filter_expr:
        kwargs["filter"] = filter_expr

    results = client.search(**kwargs)

    documents = []
    for result in results:
        documents.append({
            "file_name": result.get("file_name", ""),
            "text": result.get("text", ""),
            "section": result.get("section", ""),
            "chunk_id": result.get("chunk_id", 0),
            "source_type": result.get("source_type", ""),
            "@search.score": result.get("@search.score", 0.0),
            "@search.reranker_score": result.get("@search.reranker_score", 0.0),
        })

    return documents


def is_configured() -> bool:
    """Return True if Azure Search credentials are available."""
    return bool(AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY)
