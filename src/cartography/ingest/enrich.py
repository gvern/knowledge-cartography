from __future__ import annotations

import logging

import trafilatura

from ..schema import KnowledgeItem

logger = logging.getLogger(__name__)


def enrich_items(items: list[KnowledgeItem], max_chars: int = 4000) -> list[KnowledgeItem]:
    """Fetch full page text for items that only carry a URL (e.g. saved posts, bookmarks)."""
    for item in items:
        if not item.url or item.content:
            continue
        try:
            downloaded = trafilatura.fetch_url(item.url)
            if not downloaded:
                continue
            extracted = trafilatura.extract(downloaded, favor_recall=True)
            if extracted:
                item.content = extracted[:max_chars]
            if not item.title:
                metadata = trafilatura.extract_metadata(downloaded)
                if metadata and metadata.title:
                    item.title = metadata.title
        except Exception:
            logger.warning("Failed to enrich %s", item.url, exc_info=True)

    enriched = sum(1 for item in items if item.content)
    logger.info("Enriched %d/%d items with full text", enriched, len(items))
    return items
