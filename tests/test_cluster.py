from datetime import datetime, timezone

from cartography.cluster import cluster_items, load_cluster_cache, load_embeddings, save_cluster_cache
from cartography.config import Settings
from cartography.embed import get_collection
from cartography.schema import ClusteredItem, ItemType, SourcePlatform


def test_load_embeddings_paginates_beyond_batch_size(tmp_path):
    settings = Settings(chroma_dir=tmp_path / "chroma", output_dir=tmp_path / "output")
    collection = get_collection(settings)
    n = 25
    collection.add(
        ids=[f"id{i}" for i in range(n)],
        embeddings=[[float(i), float(i)] for i in range(n)],
        documents=[f"doc{i}" for i in range(n)],
        metadatas=[{"source": "bookmark"} for _ in range(n)],
    )

    ids, embeddings, documents, metadatas = load_embeddings(settings, batch_size=7)

    assert len(ids) == n
    assert len(set(ids)) == n
    assert embeddings.shape == (n, 2)
    assert len(documents) == n
    assert len(metadatas) == n


def test_cluster_items_reconstructs_collections_from_metadata(tmp_path):
    settings = Settings(chroma_dir=tmp_path / "chroma", output_dir=tmp_path / "output")
    collection = get_collection(settings)
    # UMAP needs enough points to build a manifold graph — 2 points degenerates.
    n = 12
    ids = [f"id{i}" for i in range(n)]
    metadatas = [{"source": "instagram", "item_type": "saved_post", "collections": ""} for _ in range(n)]
    metadatas[0] = {"source": "instagram", "item_type": "saved_post", "collections": "Recipes||Favorites"}
    collection.add(
        ids=ids,
        embeddings=[[float(i), float(i % 3)] for i in range(n)],
        documents=[f"doc {i}" for i in range(n)],
        metadatas=metadatas,
    )

    items = cluster_items(settings)
    by_id = {item.id: item for item in items}

    assert by_id["id0"].collections == ["Recipes", "Favorites"]
    assert by_id["id1"].collections == []


def test_cluster_cache_round_trip_preserves_fields(tmp_path):
    settings = Settings(output_dir=tmp_path / "output")
    items = [
        ClusteredItem(
            id="a",
            source=SourcePlatform.INSTAGRAM,
            item_type=ItemType.SAVED_POST,
            title="Title",
            content="Content",
            url="https://example.com/a",
            timestamp=datetime(2024, 3, 1, tzinfo=timezone.utc),
            collections=["Recipes", "Favorites"],
            cluster_id=3,
            cluster_label="Cooking",
            x=1.5,
            y=-2.5,
        ),
        ClusteredItem(
            id="b",
            source=SourcePlatform.FACEBOOK,
            item_type=ItemType.FOLLOWED_PAGE,
            cluster_id=-1,
            x=0.0,
            y=0.0,
        ),
    ]

    assert load_cluster_cache(settings) is None

    save_cluster_cache(items, settings)
    loaded = load_cluster_cache(settings)

    assert loaded == items
