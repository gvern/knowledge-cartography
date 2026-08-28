from __future__ import annotations

import json
import logging
from pathlib import Path

import hdbscan
import numpy as np
import umap

from .config import Settings
from .embed import _COLLECTIONS_SEP, get_collection
from .schema import ClusteredItem, ItemType, SourcePlatform

logger = logging.getLogger(__name__)

_CACHE_FILENAME = ".cluster_cache.json"


def load_embeddings(settings: Settings, batch_size: int = 1000):
    """Page through the collection instead of a single unbounded get().

    ChromaDB's SQLite backend binds one query variable per cell fetched, and a
    single get() over a large collection (hundreds of thousands of items) can
    exceed SQLite's bound-variable limit and fail with an internal error.
    """
    collection = get_collection(settings)
    total = collection.count()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    embeddings: list[list[float]] = []
    for offset in range(0, total, batch_size):
        result = collection.get(
            include=["embeddings", "documents", "metadatas"], limit=batch_size, offset=offset
        )
        ids.extend(result["ids"])
        documents.extend(result["documents"])
        metadatas.extend(result["metadatas"])
        if result["embeddings"] is not None:
            embeddings.extend(result["embeddings"])

    embeddings_arr = np.array(embeddings) if embeddings else np.empty((0, 0))
    return ids, embeddings_arr, documents, metadatas


def cluster_items(settings: Settings) -> list[ClusteredItem]:
    ids, embeddings, documents, metadatas = load_embeddings(settings)
    if len(ids) == 0:
        raise RuntimeError("No embeddings found. Run `cartography ingest` first.")

    logger.info("Reducing %d embeddings to %dD with UMAP", len(ids), settings.umap_n_components)
    reducer = umap.UMAP(
        n_neighbors=min(settings.umap_n_neighbors, max(len(ids) - 1, 2)),
        min_dist=settings.umap_min_dist,
        n_components=settings.umap_n_components,
        random_state=settings.umap_random_state,
    )
    coords = reducer.fit_transform(embeddings)

    logger.info("Clustering with HDBSCAN")
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=max(settings.hdbscan_min_cluster_size, 2),
        min_samples=settings.hdbscan_min_samples,
    )
    labels = clusterer.fit_predict(coords)

    items = []
    for item_id, coord, label, document, metadata in zip(
        ids, coords, labels, documents, metadatas, strict=True
    ):
        collections_raw = metadata.get("collections") or ""
        items.append(
            ClusteredItem(
                id=item_id,
                source=SourcePlatform(metadata["source"]),
                item_type=ItemType(metadata["item_type"]),
                title=metadata.get("title", ""),
                content=document or "",
                url=metadata.get("url") or None,
                timestamp=metadata.get("timestamp") or None,
                thread_id=metadata.get("thread_id", ""),
                thread=metadata.get("thread", ""),
                sender=metadata.get("sender", ""),
                collections=collections_raw.split(_COLLECTIONS_SEP) if collections_raw else [],
                cluster_id=int(label),
                x=float(coord[0]),
                y=float(coord[1]),
                z=float(coord[2]) if len(coord) > 2 else 0.0,
            )
        )

    n_clusters = len({item.cluster_id for item in items if item.cluster_id != -1})
    n_unclustered = sum(1 for item in items if item.cluster_id == -1)
    logger.info("Found %d clusters (%d unclustered items)", n_clusters, n_unclustered)
    return items


def _cache_path(settings: Settings) -> Path:
    return settings.output_dir / _CACHE_FILENAME


def save_cluster_cache(items: list[ClusteredItem], settings: Settings) -> None:
    """Persist the UMAP/HDBSCAN/labeling result so viz-only iterations can skip
    straight to rendering instead of recomputing (UMAP alone takes minutes at
    this dataset's scale)."""
    settings.ensure_dirs()
    payload = [item.model_dump(mode="json") for item in items]
    _cache_path(settings).write_text(json.dumps(payload), encoding="utf-8")
    logger.info("Cached %d clustered items to %s", len(items), _cache_path(settings))


def load_cluster_cache(settings: Settings) -> list[ClusteredItem] | None:
    path = _cache_path(settings)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = [ClusteredItem.model_validate(entry) for entry in payload]
    logger.info("Loaded %d clustered items from cache %s", len(items), path)
    return items
