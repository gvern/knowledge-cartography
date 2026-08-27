from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import chromadb
from chromadb.config import Settings as ChromaSettings

from .config import Settings
from .schema import KnowledgeItem, SourcePlatform

logger = logging.getLogger(__name__)


class Embedder(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder(Embedder):
    def __init__(self, model: str, host: str):
        self.model = model
        self.host = host

    def embed(self, texts: list[str]) -> list[list[float]]:
        import ollama

        client = ollama.Client(host=self.host)
        return [client.embeddings(model=self.model, prompt=text)["embedding"] for text in texts]


class VertexAIEmbedder(Embedder):
    def __init__(self, project: str, location: str, model: str):
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(project=project, location=location)
        self._model = TextEmbeddingModel.from_pretrained(model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.get_embeddings(texts)
        return [e.values for e in embeddings]


def get_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "ollama":
        return OllamaEmbedder(settings.ollama_model, settings.ollama_host)
    if settings.embedding_provider == "vertex":
        if not settings.vertex_project:
            raise ValueError("CARTOGRAPHY_VERTEX_PROJECT must be set to use the vertex provider")
        return VertexAIEmbedder(settings.vertex_project, settings.vertex_location, settings.vertex_model)
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")


def get_collection(settings: Settings):
    client = chromadb.PersistentClient(
        path=str(settings.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection("knowledge_items")


def embed_items(
    items: list[KnowledgeItem], settings: Settings, batch_size: int = 32, skip_existing: bool = False
) -> int:
    settings.ensure_dirs()
    collection = get_collection(settings)

    items_by_id = {item.id: item for item in items if item.text}
    skipped = len(items) - len(items_by_id)
    if skipped:
        logger.info("Skipping %d items with no embeddable text", skipped)

    if skip_existing:
        already = _existing_ids(collection) & items_by_id.keys()
        if already:
            logger.info("Skipping %d items already embedded (resuming)", len(already))
            for item_id in already:
                del items_by_id[item_id]

    # Messenger content is private conversation, often with people who never
    # consented to being indexed — it never leaves the machine, regardless of
    # which embedding provider is configured for everything else.
    local_only_ids = [i for i, item in items_by_id.items() if item.source == SourcePlatform.MESSENGER]
    local_only_set = set(local_only_ids)
    other_ids = [i for i in items_by_id if i not in local_only_set]

    embedded = 0
    if other_ids:
        embedded += _embed_batch(other_ids, items_by_id, get_embedder(settings), collection, batch_size)
    if local_only_ids:
        if settings.embedding_provider == "ollama":
            local_embedder: Embedder = get_embedder(settings)
        else:
            logger.info(
                "Embedding %d Messenger item(s) locally via Ollama regardless of "
                "provider=%s (privacy policy — see docs/ARCHITECTURE.md)",
                len(local_only_ids),
                settings.embedding_provider,
            )
            local_embedder = OllamaEmbedder(settings.ollama_model, settings.ollama_host)
        embedded += _embed_batch(local_only_ids, items_by_id, local_embedder, collection, batch_size)

    return embedded


def _embed_batch(
    ids: list[str], items_by_id: dict[str, KnowledgeItem], embedder: Embedder, collection, batch_size: int
) -> int:
    embedded = 0
    for start in range(0, len(ids), batch_size):
        batch_ids = ids[start : start + batch_size]
        batch_items = [items_by_id[i] for i in batch_ids]
        batch_texts = [item.text for item in batch_items]
        vectors = embedder.embed(batch_texts)
        metadatas = [_item_metadata(item) for item in batch_items]
        collection.upsert(ids=batch_ids, embeddings=vectors, documents=batch_texts, metadatas=metadatas)
        embedded += len(batch_ids)
        logger.info("Embedded %d/%d items", embedded, len(ids))
    return embedded


def _existing_ids(collection, batch_size: int = 1000) -> set[str]:
    """Paginated id fetch — an unbounded get() on a large collection can exceed
    SQLite's bound-variable limit (same failure mode fixed in cluster.py)."""
    total = collection.count()
    ids: set[str] = set()
    for offset in range(0, total, batch_size):
        result = collection.get(limit=batch_size, offset=offset)
        ids.update(result["ids"])
    return ids


_COLLECTIONS_SEP = "||"


def _item_metadata(item: KnowledgeItem) -> dict:
    return {
        "source": item.source.value,
        "item_type": item.item_type.value,
        "title": item.title,
        "url": item.url or "",
        "timestamp": item.timestamp.isoformat() if item.timestamp else "",
        # Chroma metadata values must be scalar — collections is a list, so join it.
        "collections": _COLLECTIONS_SEP.join(item.collections),
    }
