from unittest.mock import MagicMock

import anthropic
import ollama

from cartography.config import Settings
from cartography.label import _local_keyword_labels, keyword_tags, label_clusters
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


def test_label_clusters_without_api_key_or_local_model_uses_keyword_labels(monkeypatch):
    settings = Settings(anthropic_api_key=None, ollama_chat_model="")
    items = [_item(0, "A great hiking trail")]

    result = label_clusters(items, settings)

    assert result[0].cluster_label == "Great / Hiking / Trail"


def _ollama_response(text: str) -> dict:
    return {"message": {"content": text}}


def test_label_one_uses_local_llm_for_messenger_only_cluster(monkeypatch):
    settings = Settings(anthropic_api_key="fake-key", ollama_chat_model="llama3.1:8b")
    items = [_item(0, "On se voit ce soir pour le film", source=SourcePlatform.MESSENGER)]

    client = MagicMock()  # Anthropic — should never be called for an all-Messenger cluster
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)
    ollama_client = MagicMock()
    ollama_client.chat.return_value = _ollama_response("Movie night plans")
    monkeypatch.setattr(ollama, "Client", lambda host: ollama_client)

    result = label_clusters(items, settings)

    client.messages.create.assert_not_called()
    assert result[0].cluster_label == "Movie night plans"
    assert ollama_client.chat.call_args.kwargs["model"] == "llama3.1:8b"
    prompt = ollama_client.chat.call_args.kwargs["messages"][0]["content"]
    assert "On se voit ce soir pour le film" in prompt  # local model — Messenger text is safe here


def test_local_llm_falls_back_to_keywords_on_failure(monkeypatch):
    settings = Settings(anthropic_api_key=None, ollama_chat_model="llama3.1:8b")
    items = [_item(0, "A great hiking trail", source=SourcePlatform.MESSENGER)]

    ollama_client = MagicMock()
    ollama_client.chat.side_effect = ConnectionError("ollama not running")
    monkeypatch.setattr(ollama, "Client", lambda host: ollama_client)

    result = label_clusters(items, settings)

    assert result[0].cluster_label == "Great / Hiking / Trail"


def test_label_clusters_uses_local_llm_for_every_cluster_without_api_key(monkeypatch):
    settings = Settings(anthropic_api_key=None, ollama_chat_model="llama3.1:8b")
    items = [_item(0, "A public article about hiking")]  # not Messenger — still routed locally

    ollama_client = MagicMock()
    ollama_client.chat.return_value = _ollama_response("Hiking trails")
    monkeypatch.setattr(ollama, "Client", lambda host: ollama_client)

    result = label_clusters(items, settings)

    assert result[0].cluster_label == "Hiking trails"


def test_label_clusters_disables_local_llm_when_chat_model_empty(monkeypatch):
    settings = Settings(anthropic_api_key=None, ollama_chat_model="")
    items = [_item(0, "A great hiking trail")]

    ollama_client = MagicMock()
    monkeypatch.setattr(ollama, "Client", lambda host: ollama_client)

    result = label_clusters(items, settings)

    ollama_client.chat.assert_not_called()
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


def test_local_keyword_labels_dedupe_accent_and_plural_variants():
    by_cluster = {
        0: [_item(0, "même meme mêmes histoire vraiment vraiment vraiment")],
    }

    labels = _local_keyword_labels(by_cluster)

    # "même"/"meme"/"mêmes" fold to the same key — only one slot spent on it,
    # leaving room for "histoire" too instead of 3 near-duplicate spellings.
    variants_present = sum(1 for w in ("même", "meme", "mêmes") if w in labels[0].lower())
    assert variants_present == 1
    assert "histoire" in labels[0].lower()


def test_local_keyword_labels_filter_elongated_chat_slang():
    by_cluster = {
        0: [_item(0, "mdrrrr ouiiii une vraie discussion sur les vacances")],
    }

    labels = _local_keyword_labels(by_cluster)

    assert "mdr" not in labels[0].lower()
    assert "ouii" not in labels[0].lower()


def test_keyword_tags_ranks_by_frequency_and_dedupes():
    items = [_item(0, "pasta pasta pasta garlic bread"), _item(0, "another pasta night")]

    tags = keyword_tags(items, top_n=2)

    assert tags[0].lower() == "pasta"
    assert len(tags) == 2


def test_keyword_tags_empty_for_no_text():
    assert keyword_tags([_item(0, "")]) == []


def test_local_keyword_labels_skip_unclustered_and_empty_text():
    by_cluster = {
        -1: [_item(-1, "noise item")],
        0: [_item(0, "")],
    }

    labels = _local_keyword_labels(by_cluster)

    assert -1 not in labels
    assert labels[0] == "Cluster 0"
