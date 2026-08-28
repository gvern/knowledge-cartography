from unittest.mock import MagicMock

import anthropic

from cartography.config import Settings
from cartography.label import _local_keyword_labels, label_clusters
from cartography.schema import ClusteredItem, ItemType, SourcePlatform


def _item(cluster_id: int, title: str, source: SourcePlatform = SourcePlatform.BOOKMARK) -> ClusteredItem:
    item_type = ItemType.MESSAGE if source == SourcePlatform.MESSENGER else ItemType.BOOKMARK
    return ClusteredItem(
        id=title,
        source=source,
        item_type=item_type,
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

    # Falls back to a local keyword label, not the bare "Cluster 3" placeholder.
    assert result[0].cluster_label == "Some / Article"


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


def test_label_one_never_sends_messenger_content_to_the_api(monkeypatch):
    settings = Settings(anthropic_api_key="fake-key")
    items = [_item(0, "Private message", source=SourcePlatform.MESSENGER)]

    client = MagicMock()
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)

    result = label_clusters(items, settings)

    client.messages.create.assert_not_called()
    # Local keyword label, not the bare "Cluster 0" placeholder — computed from
    # the Messenger text itself, which is fine: it never leaves the machine.
    assert result[0].cluster_label == "Private / Message"


def test_label_one_excludes_messenger_samples_from_a_mixed_cluster(monkeypatch):
    settings = Settings(anthropic_api_key="fake-key")
    items = [
        _item(0, "Private message", source=SourcePlatform.MESSENGER),
        _item(0, "A public article about hiking"),
    ]

    client = MagicMock()
    client.messages.create.return_value = _response(anthropic.types.TextBlock(text="Hiking", type="text"))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)

    label_clusters(items, settings)

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Private message" not in prompt
    assert "public article about hiking" in prompt


def test_label_clusters_without_api_key_uses_local_labels_for_everything(monkeypatch):
    settings = Settings(anthropic_api_key=None)
    items = [_item(0, "A great hiking trail")]

    result = label_clusters(items, settings)

    assert result[0].cluster_label == "Great / Hiking / Trail"


def test_local_keyword_labels_downweight_terms_common_to_every_cluster():
    # "chat" appears in both clusters (uninformative); "python" and "cats" each
    # appear in only one (the actual topic) — the informative term should win.
    by_cluster = {
        0: [_item(0, "let's chat about python today")],
        1: [_item(1, "let's chat about cats today")],
    }

    labels = _local_keyword_labels(by_cluster)

    assert "python" in labels[0].lower()
    assert "cats" in labels[1].lower()


def test_local_keyword_labels_skip_unclustered_and_empty_text():
    by_cluster = {
        -1: [_item(-1, "noise item")],
        0: [_item(0, "")],
    }

    labels = _local_keyword_labels(by_cluster)

    assert -1 not in labels
    assert labels[0] == "Cluster 0"
