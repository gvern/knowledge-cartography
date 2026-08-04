from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..schema import ItemType, KnowledgeItem, SourcePlatform
from .util import make_id

logger = logging.getLogger(__name__)


def parse(export_dir: str | Path) -> list[KnowledgeItem]:
    """Parse an Instagram "Download Your Information" (GDPR) JSON export."""
    export_dir = Path(export_dir)
    items: list[KnowledgeItem] = []

    for path in export_dir.rglob("saved_posts.json"):
        items += _parse_saved(path)
    for path in export_dir.rglob("liked_posts.json"):
        items += _parse_liked(path)

    logger.info("Parsed %d Instagram items from %s", len(items), export_dir)
    return items


def _parse_saved(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    items = []
    for entry in data.get("saved_saved_media", []):
        href, ts = _extract_href_timestamp(entry)
        if not href:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("instagram", "saved", href),
                source=SourcePlatform.INSTAGRAM,
                item_type=ItemType.SAVED_POST,
                title=entry.get("title", ""),
                url=href,
                timestamp=_to_datetime(ts),
            )
        )
    return items


def _parse_liked(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    items = []
    for entry in data.get("likes_media_likes", []):
        href, ts = _extract_href_timestamp(entry)
        if not href:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("instagram", "liked", href),
                source=SourcePlatform.INSTAGRAM,
                item_type=ItemType.LIKED_POST,
                title=entry.get("title", ""),
                url=href,
                timestamp=_to_datetime(ts),
            )
        )
    return items


def _extract_href_timestamp(entry: dict) -> tuple[str | None, int | None]:
    if "string_map_data" in entry:
        for value in entry["string_map_data"].values():
            if "href" in value:
                return value["href"], value.get("timestamp")
    if "string_list_data" in entry:
        for value in entry["string_list_data"]:
            if "href" in value:
                return value["href"], value.get("timestamp")
    return None, None


def _to_datetime(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)
