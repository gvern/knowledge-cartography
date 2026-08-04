from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schema import ItemType, KnowledgeItem, SourcePlatform
from .util import make_id

logger = logging.getLogger(__name__)


def parse(export_dir: str | Path) -> list[KnowledgeItem]:
    """Parse a Facebook "Download Your Information" (GDPR) JSON export.

    Facebook has changed this export's exact file names and key shapes across
    versions, so this looks for a few known variants and degrades gracefully
    if a file or field is missing rather than failing the whole ingest.
    """
    export_dir = Path(export_dir)
    items: list[KnowledgeItem] = []

    for path in _find(export_dir, "saved_items_and_collections.json", "saved_items.json"):
        items += _parse_saved(path)
    for path in _find(export_dir, "pages_you_follow.json", "your_followed_pages.json", "followed_pages.json"):
        items += _parse_followed(path)

    logger.info("Parsed %d Facebook items from %s", len(items), export_dir)
    return items


def _find(export_dir: Path, *names: str) -> list[Path]:
    return [path for name in names for path in export_dir.rglob(name)]


def _parse_saved(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    entries = data.get("saved_items_v2", data.get("saved_saved_item", []))
    items = []
    for entry in entries:
        url = _extract_url(entry)
        title = entry.get("title") or entry.get("name") or ""
        if not url and not title:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("facebook", "saved", url or title),
                source=SourcePlatform.FACEBOOK,
                item_type=ItemType.SAVED_POST,
                title=title,
                url=url,
                timestamp=_to_datetime(entry.get("timestamp")),
            )
        )
    return items


def _parse_followed(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    entries = next((value for value in data.values() if isinstance(value, list)), [])
    items = []
    for entry in entries:
        title = entry.get("title") or entry.get("name") or ""
        if not title:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("facebook", "followed", title),
                source=SourcePlatform.FACEBOOK,
                item_type=ItemType.FOLLOWED_PAGE,
                title=title,
                timestamp=_to_datetime(entry.get("timestamp")),
            )
        )
    return items


def _extract_url(entry: dict[str, Any]) -> str | None:
    for attachment in entry.get("attachments", []):
        for data_item in attachment.get("data", []):
            context = data_item.get("external_context", {})
            if "url" in context:
                return context["url"]
    return None


def _to_datetime(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)
