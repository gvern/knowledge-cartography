from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from ..schema import ItemType, KnowledgeItem, SourcePlatform
from .util import make_id

logger = logging.getLogger(__name__)


def parse(export_dir: str | Path) -> list[KnowledgeItem]:
    """Parse Messenger threads from a Facebook GDPR export
    (your_facebook_activity/messages/{inbox,archived_threads,filtered_threads}/...).

    Facebook's "Download Your Information" lets you request JSON or HTML —
    this handles both, since which one a given export has depends on what was
    picked at request time, not the platform version.

    Sensitive by nature — private conversations, often with people who never
    consented to being indexed. One item per text message (attachments-only
    messages and, in the JSON format, unsent messages are skipped). Never
    routed through cloud labeling (see label.py) and always embedded locally
    regardless of the configured embedding provider (see embed.py) — this
    module only produces KnowledgeItems, it doesn't enforce that policy
    itself.
    """
    export_dir = Path(export_dir)
    items: list[KnowledgeItem] = []

    for path in export_dir.rglob("message_*.json"):
        items += _parse_thread_json(path)
    for path in export_dir.rglob("message_*.html"):
        items += _parse_thread_html(path)

    logger.info("Parsed %d Messenger items from %s", len(items), export_dir)
    return items


def _parse_thread_json(path: Path) -> list[KnowledgeItem]:
    data = _load_json(path)
    thread_path = data.get("thread_path") or path.parent.name
    thread_title = _fix_mojibake(data.get("title") or "")

    items = []
    for message in data.get("messages", []):
        if message.get("is_unsent"):
            continue
        content = message.get("content")
        if not content:
            continue

        sender = _fix_mojibake(message.get("sender_name") or "")
        content = _fix_mojibake(content)
        timestamp_ms = message.get("timestamp_ms")

        items.append(
            KnowledgeItem(
                id=make_id("messenger", thread_path, str(timestamp_ms), sender, content),
                source=SourcePlatform.MESSENGER,
                item_type=ItemType.MESSAGE,
                content=content,
                timestamp=_to_datetime(timestamp_ms),
                metadata={"thread": thread_title, "thread_path": thread_path, "sender": sender},
            )
        )
    return items


def _to_datetime(timestamp_ms: int | None) -> datetime | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _fix_mojibake(text: str) -> str:
    """Facebook's JSON message exports mis-encode non-ASCII text: UTF-8 bytes
    get decoded as Latin-1 and re-encoded as UTF-8 (a long-standing,
    well-documented export bug). Round-tripping through Latin-1 undoes it;
    text that was never mis-encoded just passes through unchanged (or is left
    as-is if it can't be round-tripped, rather than raising). The HTML export
    doesn't have this bug — this is only used on the JSON path."""
    try:
        return text.encode("latin1").decode("utf8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _parse_thread_html(path: Path) -> list[KnowledgeItem]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    title_tag = soup.find("h1")
    thread_title = title_tag.get_text(strip=True) if title_tag else ""
    thread_path = path.parent.name

    items = []
    sender = ""
    for index, section in enumerate(soup.find_all("section", class_="_a6-g")):
        # Facebook's export omits the sender heading on consecutive messages
        # from the same person — carry the last-seen one forward.
        heading = section.find("h2")
        if heading is not None:
            sender = heading.get_text(strip=True)

        content_div = section.find("div", class_="_a6-p")
        if content_div is None:
            continue
        reactions = content_div.find("ul", class_="_a6-q")
        if reactions is not None:
            reactions.decompose()
        # Attachment-only messages (stickers, photos) leave no text once
        # reactions are stripped — get_text() ignores <img>/<a> wrappers.
        content = content_div.get_text(" ", strip=True)
        if not content:
            continue

        items.append(
            KnowledgeItem(
                id=make_id("messenger", thread_path, str(index), sender, content),
                source=SourcePlatform.MESSENGER,
                item_type=ItemType.MESSAGE,
                content=content,
                # Facebook's HTML export renders timestamps as
                # locale-hybrid strings (French month names, English am/pm,
                # e.g. "juil 13, 2021 10:58:25 am") with no machine-readable
                # equivalent — left unset here, same as the other HTML-format
                # parsers in facebook.py.
                metadata={"thread": thread_title, "thread_path": thread_path, "sender": sender},
            )
        )
    return items
