from unittest.mock import MagicMock

import cartography.embed as embed_module
from cartography.config import Settings
from cartography.embed import embed_items
from cartography.schema import ItemType, KnowledgeItem, SourcePlatform


def _item(item_id: str, title: str, source: SourcePlatform = SourcePlatform.BOOKMARK) -> KnowledgeItem:
    item_type = ItemType.MESSAGE if source == SourcePlatform.MESSENGER else ItemType.BOOKMARK
    return KnowledgeItem(id=item_id, source=source, item_type=item_type, title=title)


def _fake_embedder(monkeypatch) -> MagicMock:
    embedder = MagicMock()
    embedder.embed.side_effect = lambda texts: [[0.0, 0.0] for _ in texts]
    monkeypatch.setattr(embed_module, "get_embedder", lambda settings: embedder)
    return embedder


def test_embed_items_without_resume_reembeds_everything(tmp_path, monkeypatch):
    settings = Settings(chroma_dir=tmp_path / "chroma", output_dir=tmp_path / "output")
    embedder = _fake_embedder(monkeypatch)
    items = [_item("a", "First")]

    embed_items(items, settings)
    embedder.embed.reset_mock()
    embedded = embed_items(items, settings)  # default: skip_existing=False

    assert embedded == 1
    embedder.embed.assert_called_once()


def test_embed_items_resume_skips_already_embedded(tmp_path, monkeypatch):
    settings = Settings(chroma_dir=tmp_path / "chroma", output_dir=tmp_path / "output")
    embedder = _fake_embedder(monkeypatch)
    items = [_item("a", "First"), _item("b", "Second")]
    embed_items(items, settings)
    embedder.embed.reset_mock()

    embedded = embed_items(items + [_item("c", "Third")], settings, skip_existing=True)

    assert embedded == 1
    called_texts = [call.args[0] for call in embedder.embed.call_args_list]
    assert called_texts == [["Third"]]


def test_embed_items_routes_messenger_items_through_ollama_when_provider_is_vertex(tmp_path, monkeypatch):
    settings = Settings(
        chroma_dir=tmp_path / "chroma", output_dir=tmp_path / "output", embedding_provider="vertex"
    )
    cloud_embedder = MagicMock()
    cloud_embedder.embed.side_effect = lambda texts: [[0.0, 0.0] for _ in texts]
    monkeypatch.setattr(embed_module, "get_embedder", lambda settings: cloud_embedder)

    local_embedder = MagicMock()
    local_embedder.embed.side_effect = lambda texts: [[0.0, 0.0] for _ in texts]
    monkeypatch.setattr(embed_module, "OllamaEmbedder", lambda model, host: local_embedder)

    items = [_item("a", "Public post"), _item("b", "Private message", source=SourcePlatform.MESSENGER)]

    embed_items(items, settings)

    cloud_embedder.embed.assert_called_once_with(["Public post"])
    local_embedder.embed.assert_called_once_with(["Private message"])
