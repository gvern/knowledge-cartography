from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ..schema import ItemType, KnowledgeItem, SourcePlatform
from .util import make_id

logger = logging.getLogger(__name__)


def parse(export_dir: str | Path) -> list[KnowledgeItem]:
    """Parse a Facebook "Download Your Information" (GDPR) export.

    Facebook has changed this export's exact file names, key shapes, and even
    format (JSON vs HTML, depending on what you requested) across versions, so
    this looks for a few known variants and degrades gracefully if a file or
    field is missing rather than failing the whole ingest.
    """
    export_dir = Path(export_dir)
    items: list[KnowledgeItem] = []

    for path in _find(export_dir, "saved_items_and_collections.json", "saved_items.json"):
        items += _parse_saved(path)
    for path in _find(export_dir, "pages_you_follow.json", "your_followed_pages.json", "followed_pages.json"):
        items += _parse_followed(path)
    for path in _find(export_dir, "your_saved_items.html"):
        items += _parse_saved_html(path)
    for path in _find(export_dir, "pages_and_profiles_you_follow.html"):
        items += _parse_followed_html(path)

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


def _parse_saved_html(path: Path) -> list[KnowledgeItem]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    items = []
    for section in soup.find_all("section", class_="_a6-g"):
        heading = section.find("h2")
        title = heading.get_text(strip=True) if heading else ""
        link = section.find("a", href=True)
        href = link["href"] if link else None
        url = _unwrap_redirect(href) if isinstance(href, str) else None
        content = section.get_text(" ", strip=True)
        if not title and not content:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("facebook", "saved", url or title),
                source=SourcePlatform.FACEBOOK,
                item_type=ItemType.SAVED_POST,
                title=title,
                content=content,
                url=url,
            )
        )
    return items


def _parse_followed_html(path: Path) -> list[KnowledgeItem]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    items = []
    for section in soup.find_all("section", class_="_a6-g"):
        heading = section.find("h2")
        title = heading.get_text(strip=True) if heading else ""
        if not title:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("facebook", "followed", title),
                source=SourcePlatform.FACEBOOK,
                item_type=ItemType.FOLLOWED_PAGE,
                title=title,
            )
        )
    return items


def _unwrap_redirect(href: str) -> str:
    """Facebook wraps outbound links in the export as /dyi/l/?l=<real url>&s=...; unwrap it."""
    parsed = urlparse(href)
    if parsed.path == "/dyi/l/":
        real_url = parse_qs(parsed.query).get("l")
        if real_url:
            return real_url[0]
    return href
