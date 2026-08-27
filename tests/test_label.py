from unittest.mock import MagicMock

import anthropic

from cartography.config import Settings
from cartography.label import label_clusters
from cartography.schema import ClusteredItem, ItemType, SourcePlatform


def _item(cluster_id: int, title: str) -> ClusteredItem:
    return ClusteredItem(
        id=title,
        source=SourcePlatform.BOOKMARK,
        item_type=ItemType.BOOKMARK,
        title=title,
        cluster_id=cluster_id,
        x=0.0,
        y=0.0,
    )


def _response(*blocks):
    response = MagicMock()
    response.content = list(blocks)
    return response


def test_label_one_skips_leading_thinking_block(monkeypatch):
    settings = Settings(anthropic_api_key="fake-key")
    items = [_item(0, "Some article")]

    client = MagicMock()
    client.messages.create.return_value = _response(
        anthropic.types.ThinkingBlock(thinking="pondering...", signature="sig", type="thinking"),
        anthropic.types.TextBlock(text="Cooking recipes", type="text"),
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)

    result = label_clusters(items, settings)

    assert result[0].cluster_label == "Cooking recipes"


def test_label_one_falls_back_when_no_text_block(monkeypatch):
    settings = Settings(anthropic_api_key="fake-key")
    items = [_item(3, "Some article")]

    client = MagicMock()
    client.messages.create.return_value = _response(
        anthropic.types.ThinkingBlock(thinking="pondering...", signature="sig", type="thinking"),
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)

    result = label_clusters(items, settings)

    assert result[0].cluster_label == "Cluster 3"


def test_label_one_disables_thinking(monkeypatch):
    # A 2-5 word label doesn't need reasoning, and thinking can consume the whole
    # (small) max_tokens budget before any label text is emitted — see the two
    # tests above. Explicitly disabling it is the fix, not just tolerating it.
    settings = Settings(anthropic_api_key="fake-key")
    items = [_item(0, "Some article")]

    client = MagicMock()
    client.messages.create.return_value = _response(anthropic.types.TextBlock(text="Cooking", type="text"))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)

    label_clusters(items, settings)

    assert client.messages.create.call_args.kwargs["thinking"] == {"type": "disabled"}
