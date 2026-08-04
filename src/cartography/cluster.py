from __future__ import annotations

import logging

import hdbscan
import numpy as np
import umap

from .config import Settings
from .embed import get_collection
from .schema import ClusteredItem, ItemType, SourcePlatform

logger = logging.getLogger(__name__)


def load_embeddings(settings: Settings):
    collection = get_collection(settings)
    result = collection.get(include=["embeddings", "documents", "metadatas"])
    embeddings = np.array(result["embeddings"]) if result["embeddings"] is not None else np.empty((0, 0))
    return result["ids"], embeddings, result["documents"], result["metadatas"]


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
    for item_id, coord, label, document, metadata in zip(ids, coords, labels, documents, metadatas):
        items.append(
            ClusteredItem(
                id=item_id,
                source=SourcePlatform(metadata["source"]),
                item_type=ItemType(metadata["item_type"]),
                title=metadata.get("title", ""),
                content=document or "",
                url=metadata.get("url") or None,
                timestamp=metadata.get("timestamp") or None,
                cluster_id=int(label),
                x=float(coord[0]),
                y=float(coord[1]),
            )
        )

    n_clusters = len({item.cluster_id for item in items if item.cluster_id != -1})
    n_unclustered = sum(1 for item in items if item.cluster_id == -1)
    logger.info("Found %d clusters (%d unclustered items)", n_clusters, n_unclustered)
    return items
