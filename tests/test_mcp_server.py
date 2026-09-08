import pytest

from cartography import mcp_server
from cartography.schema import ClusteredItem, ItemType, SourcePlatform


def _item(
    id_: str,
    cluster_id: int = 0,
    source: SourcePlatform = SourcePlatform.BOOKMARK,
    content: str = "",
    collections: list[str] | None = None,
) -> ClusteredItem:
    item_type = ItemType.MESSAGE if source == SourcePlatform.MESSENGER else ItemType.BOOKMARK
    return ClusteredItem(
        id=id_,
        source=source,
        item_type=item_type,
        content=content,
        cluster_id=cluster_id,
        cluster_label="Cooking" if cluster_id == 0 else "",
        collections=collections or [],
        x=0.0,
        y=0.0,
        z=0.0,
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    mcp_server._cache = None
    yield
    mcp_server._cache = None


def _seed(monkeypatch, items: list[ClusteredItem]) -> None:
    monkeypatch.setattr(mcp_server, "load_cluster_cache", lambda settings: items)


def test_get_items_raises_a_clear_error_with_no_cache(monkeypatch):
    monkeypatch.setattr(mcp_server, "load_cluster_cache", lambda settings: None)

    with pytest.raises(RuntimeError, match="cartography cluster"):
        mcp_server.stats()


def test_stats_excludes_messenger_from_every_count(monkeypatch):
    _seed(
        monkeypatch,
        [
            _item("a", content="A pasta recipe"),
            _item("b", source=SourcePlatform.MESSENGER, content="Private message"),
        ],
    )

    result = mcp_server.stats()

    assert result["total_items"] == 1
    assert result["sources"] == {"bookmark": 1}


def test_list_clusters_carries_tags_and_excludes_messenger(monkeypatch):
    _seed(
        monkeypatch,
        [
            _item("a", content="A great pasta recipe"),
            _item("b", content="Another pasta night"),
            _item("c", source=SourcePlatform.MESSENGER, content="Private pasta chat"),
        ],
    )

    clusters = mcp_server.list_clusters()

    assert len(clusters) == 1
    assert clusters[0]["size"] == 2
    assert "pasta" in {tag.lower() for tag in clusters[0]["tags"]}


def test_get_cluster_returns_items_and_excludes_messenger(monkeypatch):
    _seed(
        monkeypatch,
        [
            _item("a", content="A pasta recipe"),
            _item("b", source=SourcePlatform.MESSENGER, content="Private message"),
        ],
    )

    result = mcp_server.get_cluster(0)

    assert [item["id"] for item in result["items"]] == ["a"]


def test_get_cluster_raises_for_unknown_or_all_messenger_cluster(monkeypatch):
    _seed(monkeypatch, [_item("a", source=SourcePlatform.MESSENGER, cluster_id=1)])

    with pytest.raises(ValueError, match="No visible items"):
        mcp_server.get_cluster(1)


def test_list_and_get_collection_items_exclude_messenger(monkeypatch):
    _seed(
        monkeypatch,
        [
            _item("a", content="Pasta", collections=["Recipes"]),
            _item("b", source=SourcePlatform.MESSENGER, content="Private", collections=["Recipes"]),
        ],
    )

    collections = mcp_server.list_collections()
    assert collections == [{"name": "Recipes", "count": 1}]

    items = mcp_server.get_collection_items("Recipes")
    assert [item["id"] for item in items] == ["a"]


def test_get_collection_items_raises_for_unknown_collection(monkeypatch):
    _seed(monkeypatch, [_item("a", content="Pasta", collections=["Recipes"])])

    with pytest.raises(ValueError, match="No visible items"):
        mcp_server.get_collection_items("Nonexistent")


def test_search_knowledge_excludes_messenger_results(monkeypatch):
    fake_results = [
        {
            "id": "a",
            "distance": 0.1,
            "text": "A pasta recipe",
            "source": "bookmark",
            "item_type": "bookmark",
            "title": "",
            "url": None,
            "timestamp": None,
        },
        {
            "id": "b",
            "distance": 0.2,
            "text": "Private message",
            "source": "messenger",
            "item_type": "message",
            "title": "",
            "url": None,
            "timestamp": None,
        },
    ]
    monkeypatch.setattr(mcp_server, "semantic_search", lambda query, settings, limit: fake_results)

    results = mcp_server.search_knowledge("pasta", limit=10)

    assert [r["id"] for r in results] == ["a"]
