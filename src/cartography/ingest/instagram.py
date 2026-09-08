from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from ..schema import ItemType, KnowledgeItem, SourcePlatform
from .util import make_id

logger = logging.getLogger(__name__)

# Meta's HTML export wraps each post's permalink in a fixed table row; the caption
# ("Légende") row that follows is matched separately since it's optional and may repeat.
_HTML_URL_RE = re.compile(r'>URL<div><a target="_blank" href="([^"]+)"')
_HTML_CAPTION_RE = re.compile(r'Légende</td><td class="_2piu _a6_r">(.*?)</td>', re.DOTALL)
_CAPTION_SEARCH_WINDOW = 4000

# A collection's own header row ("Nom" -> "Type" -> "Confidentialité" -> "Heure de mise
# à jour") is a fixed, distinctive sequence — unlike the same "Nom" label reused inside
# each post's nested "Propriétaire" (owner) block, which is followed by "Nom de profil"
# instead, so this pattern never matches those.
_COLLECTION_HEADER_RE = re.compile(
    r'<td class="_a6_q">Nom</td><td class="_2piu _a6_r">([^<]*)</td></tr>'
    r'<tr><td class="_a6_q">Type</td>'
)


def parse(export_dir: str | Path) -> list[KnowledgeItem]:
    """Parse an Instagram "Download Your Information" (GDPR) export.

    Meta lets you request this export as JSON or HTML; both are handled here since
    which one you get isn't under this tool's control.
    """
    export_dir = Path(export_dir)
    items: list[KnowledgeItem] = []

    for path in export_dir.rglob("saved_posts.json"):
        items += _parse_saved(path)
    for path in export_dir.rglob("liked_posts.json"):
        items += _parse_liked(path)
    for path in export_dir.rglob("saved_posts.html"):
        items += _parse_html_posts(path, ItemType.SAVED_POST, "saved")
    for path in export_dir.rglob("liked_posts.html"):
        items += _parse_html_posts(path, ItemType.LIKED_POST, "liked")
    for path in export_dir.rglob("saved_collections.html"):
        items = _merge_collections(items, path)

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


def _parse_html_posts(path: Path, item_type: ItemType, tag: str) -> list[KnowledgeItem]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    items = []
    seen_urls: set[str] = set()
    for match in _HTML_URL_RE.finditer(text):
        href = html.unescape(match.group(1))
        if href in seen_urls:
            continue
        seen_urls.add(href)

        window_end = match.end() + _CAPTION_SEARCH_WINDOW
        caption_match = _HTML_CAPTION_RE.search(text, match.end(), window_end)
        caption = html.unescape(caption_match.group(1)).strip() if caption_match else ""

        items.append(
            KnowledgeItem(
                id=make_id("instagram", tag, href),
                source=SourcePlatform.INSTAGRAM,
                item_type=item_type,
                content=caption,
                url=href,
            )
        )
    return items


def _parse_collections_html(path: Path) -> dict[str, tuple[list[str], str]]:
    """Map each post URL to (collection names it appears in, its caption)."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    headers = list(_COLLECTION_HEADER_RE.finditer(text))

    posts: dict[str, tuple[list[str], str]] = {}
    for i, header in enumerate(headers):
        name = html.unescape(header.group(1)).strip()
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[header.end() : block_end]

        for match in _HTML_URL_RE.finditer(block):
            href = html.unescape(match.group(1))
            window_end = match.end() + _CAPTION_SEARCH_WINDOW
            caption_match = _HTML_CAPTION_RE.search(block, match.end(), window_end)
            caption = html.unescape(caption_match.group(1)).strip() if caption_match else ""

            names, existing_caption = posts.get(href, ([], ""))
            if name not in names:
                names.append(name)
            posts[href] = (names, existing_caption or caption)

    logger.info("Parsed %d collections covering %d posts from %s", len(headers), len(posts), path)
    return posts


def _merge_collections(items: list[KnowledgeItem], path: Path) -> list[KnowledgeItem]:
    collection_posts = _parse_collections_html(path)
    items_by_url = {item.url: item for item in items if item.url}

    for href, (names, caption) in collection_posts.items():
        existing = items_by_url.get(href)
        if existing is not None:
            existing.collections = names
        else:
            new_item = KnowledgeItem(
                id=make_id("instagram", "saved", href),
                source=SourcePlatform.INSTAGRAM,
                item_type=ItemType.SAVED_POST,
                content=caption,
                url=href,
                collections=names,
            )
            items.append(new_item)
            items_by_url[href] = new_item

    return items
