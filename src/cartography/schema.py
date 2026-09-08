from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class SourcePlatform(str, Enum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    MESSENGER = "messenger"
    GOOGLE_SEARCH = "google_search"
    CHROME_HISTORY = "chrome_history"
    YOUTUBE = "youtube"
    BOOKMARK = "bookmark"


class ItemType(str, Enum):
    SAVED_POST = "saved_post"
    LIKED_POST = "liked_post"
    FOLLOWED_PAGE = "followed_page"
    SEARCH_QUERY = "search_query"
    PAGE_VISIT = "page_visit"
    VIDEO_WATCH = "video_watch"
    BOOKMARK = "bookmark"
    MESSAGE = "message"


class KnowledgeItem(BaseModel):
    id: str
    source: SourcePlatform
    item_type: ItemType
    title: str = ""
    content: str = ""
    url: str | None = None
    timestamp: datetime | None = None
    # Conversation context (Messenger, and any future message-like source).
    # thread_id is a stable grouping key (e.g. Messenger's thread_path);
    # thread is the human-readable label (contact/group name), which can
    # collide across distinct threads (renames, same-named group chats) —
    # group by thread_id, display thread.
    thread_id: str = ""
    thread: str = ""
    sender: str = ""
    collections: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        parts = [self.title, self.content]
        return "\n".join(p for p in parts if p).strip()

    @property
    def comparable_timestamp(self) -> datetime | None:
        """`timestamp`, coerced to timezone-aware for cross-item comparison.
        Every ingest source stamps UTC-aware timestamps except Messenger's
        HTML export format, which has no timezone in the source data and is
        stamped naive (see `ingest/messenger.py`'s `_parse_html_timestamp`)
        — comparing a naive and an aware datetime raises TypeError. Treating
        naive as UTC here is an approximation for sorting/grouping only, not
        a correction to the stored `timestamp` value."""
        ts = self.timestamp
        if ts is None:
            return None
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


class ClusteredItem(KnowledgeItem):
    cluster_id: int
    cluster_label: str = ""
    x: float
    y: float
    z: float = 0.0
