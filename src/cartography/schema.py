from datetime import datetime
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


class ClusteredItem(KnowledgeItem):
    cluster_id: int
    cluster_label: str = ""
    x: float
    y: float
    z: float = 0.0
