import json
from datetime import datetime, timezone

from cartography.config import Settings
from cartography.export import build_graph, write_graph_json, write_markdown_vault
from cartography.schema import ClusteredItem, ItemType, SourcePlatform


def _item(**kwargs) -> ClusteredItem:
    defaults = dict(
        id="id",
        source=SourcePlatform.BOOKMARK,
        item_type=ItemType.BOOKMARK,
        cluster_id=0,
        cluster_label="Cooking",
        x=0.0,
        y=0.0,
        z=0.0,
    )
    defaults.update(kwargs)
    return ClusteredItem(**defaults)


def test_build_graph_includes_nodes_and_clusters():
    items = [
        _item(id="a", title="Pasta recipe", x=0.0, y=0.0, z=0.0),
        _item(id="b", title="Pizza recipe", x=0.1, y=0.1, z=0.0),
        _item(id="c", cluster_id=-1, cluster_label="", title="Random", x=5.0, y=5.0, z=5.0),
    ]

    graph = build_graph(items, k_neighbors=1)

    assert graph["total_items"] == 3
    assert {node["id"] for node in graph["nodes"]} == {"a", "b", "c"}
    assert graph["total_clusters"] == 1
    assert graph["clusters"][0]["label"] == "Cooking"
    assert graph["clusters"][0]["size"] == 2


def test_build_graph_clusters_carry_keyword_tags():
    items = [
        _item(id="a", content="A great weeknight pasta recipe", x=0.0, y=0.0, z=0.0),
        _item(id="b", content="Another pasta recipe with garlic", x=0.1, y=0.1, z=0.0),
    ]

    graph = build_graph(items, k_neighbors=1)

    assert "pasta" in {tag.lower() for tag in graph["clusters"][0]["tags"]}


def test_semantic_edges_link_nearby_points():
    items = [_item(id=f"id{i}", x=float(i), y=0.0, z=0.0) for i in range(5)]

    graph = build_graph(items, k_neighbors=1)

    edge_pairs = {(e["source"], e["target"]) for e in graph["edges"] if e["type"] == "semantic_neighbor"}
    assert ("id0", "id1") in edge_pairs or ("id1", "id0") in edge_pairs


def test_semantic_edges_skipped_below_neighbor_count():
    items = [_item(id=f"id{i}", x=float(i), y=0.0, z=0.0) for i in range(2)]

    graph = build_graph(items, k_neighbors=6)

    assert [e for e in graph["edges"] if e["type"] == "semantic_neighbor"] == []


def test_structural_edges_chain_shared_collection_and_thread():
    items = [
        _item(id="a", collections=["Recipes"], x=0.0, y=0.0, z=0.0),
        _item(id="b", collections=["Recipes"], x=10.0, y=10.0, z=10.0),
        _item(
            id="c",
            thread_id="t1",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            x=20.0,
            y=20.0,
            z=20.0,
        ),
        _item(
            id="d",
            thread_id="t1",
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            x=21.0,
            y=20.0,
            z=20.0,
        ),
    ]

    graph = build_graph(items, k_neighbors=1)

    types = {(e["source"], e["target"], e["type"]) for e in graph["edges"]}
    assert ("a", "b", "same_collection") in types
    assert ("c", "d", "same_thread") in types


def test_write_graph_json_writes_file(tmp_path):
    settings = Settings(output_dir=tmp_path / "output")
    items = [_item(id=f"id{i}", x=float(i), y=0.0, z=0.0) for i in range(3)]

    path = write_graph_json(items, settings, k_neighbors=1)

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["total_items"] == 3


def test_write_markdown_vault_creates_notes_and_index(tmp_path):
    settings = Settings(output_dir=tmp_path / "output")
    items = [
        _item(id="a", title="Pasta", content="A pasta recipe", collections=["Recipes"], x=0.0, y=0.0, z=0.0),
        _item(id="b", title="Pizza", content="A pizza recipe", collections=["Recipes"], x=0.1, y=0.1, z=0.0),
    ]

    notes_dir = write_markdown_vault(items, settings)

    assert (notes_dir / "index.md").exists()
    index_text = (notes_dir / "index.md").read_text(encoding="utf-8")
    assert "Cooking" in index_text
    assert "Recipes" in index_text

    cluster_notes = list(notes_dir.glob("cluster-*.md"))
    assert len(cluster_notes) == 1
    cluster_text = cluster_notes[0].read_text(encoding="utf-8")
    assert "Pasta A pasta recipe" in cluster_text
    assert "[[collection-recipes|Recipes]]" in cluster_text

    collection_notes = list(notes_dir.glob("collection-*.md"))
    assert len(collection_notes) == 1
    collection_text = collection_notes[0].read_text(encoding="utf-8")
    assert "[[cluster-0-cooking|Cooking]]" in collection_text
