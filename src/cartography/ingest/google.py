from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from ..schema import ItemType, KnowledgeItem, SourcePlatform
from .util import make_id

logger = logging.getLogger(__name__)


def parse_takeout(takeout_dir: str | Path) -> list[KnowledgeItem]:
    """Parse Chrome history, YouTube watch history, and Search activity from a Google Takeout export."""
    takeout_dir = Path(takeout_dir)
    items: list[KnowledgeItem] = []

    for path in takeout_dir.rglob("History.json"):
        items += _parse_chrome_history(path)
    for path in takeout_dir.rglob("watch-history.json"):
        items += _parse_youtube_history(path)
    for path in takeout_dir.rglob("MyActivity.json"):
        items += _parse_search_history(path)

    logger.info("Parsed %d Google Takeout items from %s", len(items), takeout_dir)
    return items


def parse_bookmarks(bookmarks_path: str | Path) -> list[KnowledgeItem]:
    """Parse a Netscape-format bookmarks.html export (Chrome, Firefox, Safari)."""
    bookmarks_path = Path(bookmarks_path)
    soup = BeautifulSoup(bookmarks_path.read_text(encoding="utf-8", errors="ignore"), "html.parser")

    items = []
    for link in soup.find_all("a"):
        href = link.get("href")
        if not href or not href.startswith("http"):
            continue
        add_date = link.get("add_date")
        items.append(
            KnowledgeItem(
                id=make_id("bookmark", href),
                source=SourcePlatform.BOOKMARK,
                item_type=ItemType.BOOKMARK,
                title=link.get_text(strip=True),
                url=href,
                timestamp=_from_epoch_seconds(int(add_date)) if add_date and add_date.isdigit() else None,
            )
        )

    logger.info("Parsed %d bookmarks from %s", len(items), bookmarks_path)
    return items


def _parse_chrome_history(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    items = []
    for entry in data.get("Browser History", []):
        url = entry.get("url")
        if not url:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("chrome", url, entry.get("time_usec", "")),
                source=SourcePlatform.CHROME_HISTORY,
                item_type=ItemType.PAGE_VISIT,
                title=entry.get("title", ""),
                url=url,
                timestamp=_from_epoch_micros(entry.get("time_usec")),
            )
        )
    return items


def _parse_youtube_history(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    items = []
    for entry in data:
        url = entry.get("titleUrl")
        if not url:
            continue
        items.append(
            KnowledgeItem(
                id=make_id("youtube", url, entry.get("time", "")),
                source=SourcePlatform.YOUTUBE,
                item_type=ItemType.VIDEO_WATCH,
                title=entry.get("title", ""),
                url=url,
                timestamp=_parse_iso(entry.get("time")),
            )
        )
    return items


def _parse_search_history(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    items = []
    for entry in data:
        if entry.get("header") != "Search":
            continue
        title = entry.get("title", "")
        url = entry.get("titleUrl")
        items.append(
            KnowledgeItem(
                id=make_id("google_search", title, entry.get("time", "")),
                source=SourcePlatform.GOOGLE_SEARCH,
                item_type=ItemType.SEARCH_QUERY,
                title=title,
                url=url,
                timestamp=_parse_iso(entry.get("time")),
            )
        )
    return items


def _from_epoch_micros(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)


def _from_epoch_seconds(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)
