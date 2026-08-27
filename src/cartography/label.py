from __future__ import annotations

import logging
from collections import defaultdict

import anthropic

from .config import Settings
from .schema import ClusteredItem

logger = logging.getLogger(__name__)

_PROMPT = """You are labeling a cluster of personal knowledge items (saved posts, watched \
videos, search queries, bookmarks) that a semantic clustering algorithm grouped together \
because they are topically similar.

Here are up to {n} representative items from the cluster:

{samples}

Reply with ONLY a short (2-5 word) descriptive label for this cluster's overarching topic. \
No punctuation, no explanation."""


def label_clusters(
    items: list[ClusteredItem], settings: Settings, samples_per_cluster: int = 12
) -> list[ClusteredItem]:
    if not settings.anthropic_api_key:
        logger.warning("No Anthropic API key configured; leaving clusters unlabeled")
        return items

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    by_cluster: dict[int, list[ClusteredItem]] = defaultdict(list)
    for item in items:
        by_cluster[item.cluster_id].append(item)

    labels: dict[int, str] = {-1: "Unclustered"}
    for cluster_id, cluster_items in by_cluster.items():
        if cluster_id == -1:
            continue
        labels[cluster_id] = _label_one(client, settings, cluster_id, cluster_items, samples_per_cluster)

    for item in items:
        item.cluster_label = labels.get(item.cluster_id, f"Cluster {item.cluster_id}")

    return items


def _label_one(
    client: anthropic.Anthropic,
    settings: Settings,
    cluster_id: int,
    cluster_items: list[ClusteredItem],
    samples_per_cluster: int,
) -> str:
    sample_texts = [item.text for item in cluster_items[:samples_per_cluster] if item.text]
    if not sample_texts:
        return f"Cluster {cluster_id}"

    samples = "\n".join(f"- {text[:200]}" for text in sample_texts)
    prompt = _PROMPT.format(n=len(sample_texts), samples=samples)
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=30,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        text_block = next(
            (block for block in response.content if isinstance(block, anthropic.types.TextBlock)), None
        )
        if text_block is None:
            block_types = [type(block).__name__ for block in response.content]
            raise TypeError(f"No text block in response content: {block_types}")
        label = text_block.text.strip()
    except Exception:
        logger.exception("Failed to label cluster %d", cluster_id)
        return f"Cluster {cluster_id}"

    logger.info("Cluster %d (%d items): %s", cluster_id, len(cluster_items), label)
    return label
