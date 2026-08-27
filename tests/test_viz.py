from cartography.config import Settings
from cartography.schema import ClusteredItem, ItemType, SourcePlatform
from cartography.viz import _hover_text, _item_detail, build_map


def test_build_map_writes_html_with_cluster_labels(tmp_path):
    settings = Settings(output_dir=tmp_path / "output")
    items = [
        ClusteredItem(
            id="a",
            source=SourcePlatform.BOOKMARK,
            item_type=ItemType.BOOKMARK,
            title="Example",
            cluster_id=0,
            cluster_label="Cooking",
            x=0.1,
            y=0.2,
        ),
        ClusteredItem(
            id="b",
            source=SourcePlatform.BOOKMARK,
            item_type=ItemType.BOOKMARK,
            title="Noise item",
            cluster_id=-1,
            x=0.3,
            y=0.4,
        ),
    ]

    path = build_map(items, settings)

    assert path.exists()
    rendered = path.read_text(encoding="utf-8")
    assert "Cooking" in rendered  # hover text, annotation, and sidebar row
    assert "unclustered" in rendered.lower()  # noise trace legend name
    assert 'id="cg-search"' in rendered  # sidebar search box present
    assert '"label": "Cooking"' in rendered  # sidebar click-to-zoom data


def test_build_map_writes_collections_tab_and_highlight_data(tmp_path):
    settings = Settings(output_dir=tmp_path / "output")
    items = [
        ClusteredItem(
            id="a",
            source=SourcePlatform.INSTAGRAM,
            item_type=ItemType.SAVED_POST,
            content="Pasta",
            cluster_id=0,
            cluster_label="Food",
            collections=["Recipes"],
            x=0.1,
            y=0.2,
        ),
        ClusteredItem(
            id="b",
            source=SourcePlatform.INSTAGRAM,
            item_type=ItemType.SAVED_POST,
            content="Pizza",
            cluster_id=-1,
            collections=["Recipes"],
            x=5.0,
            y=5.0,
        ),
    ]

    path = build_map(items, settings)

    rendered = path.read_text(encoding="utf-8")
    assert 'data-tab="collections"' in rendered
    assert 'id="cg-list-collections"' in rendered
    assert "Recipes" in rendered
    assert '"name": "Recipes", "count": 2' in rendered
    assert "collection-highlight" in rendered


def test_hover_text_falls_back_to_content_when_title_is_empty():
    # Instagram/HTML-parsed items never set title — only content (the caption).
    # Hover text must not silently show "(untitled)" for the bulk of the dataset.
    item = ClusteredItem(
        id="a",
        source=SourcePlatform.INSTAGRAM,
        item_type=ItemType.LIKED_POST,
        title="",
        content="A great recipe for weeknight pasta",
        cluster_id=0,
        cluster_label="Cooking",
        x=0.0,
        y=0.0,
    )

    text = _hover_text(item)

    assert "(untitled)" not in text
    assert "A great recipe for weeknight pasta" in text


def test_hover_text_truncates_long_content():
    item = ClusteredItem(
        id="a",
        source=SourcePlatform.INSTAGRAM,
        item_type=ItemType.LIKED_POST,
        content="x" * 500,
        cluster_id=0,
        x=0.0,
        y=0.0,
    )

    text = _hover_text(item)

    assert "x" * 500 not in text
    assert "…" in text


def test_item_detail_carries_untruncated_text_and_collections():
    item = ClusteredItem(
        id="a",
        source=SourcePlatform.INSTAGRAM,
        item_type=ItemType.SAVED_POST,
        content="x" * 500,
        url="https://instagram.com/p/abc",
        cluster_id=2,
        cluster_label="Cooking",
        collections=["Recipes", "Favorites"],
        x=0.0,
        y=0.0,
    )

    detail = _item_detail(item)

    assert detail["text"] == "x" * 500  # not truncated, unlike _hover_text
    assert detail["url"] == "https://instagram.com/p/abc"
    assert detail["cluster"] == "Cooking"
    assert detail["source"] == "Instagram"
    assert detail["type"] == "Saved post"
    assert detail["collections"] == ["Recipes", "Favorites"]


def test_build_map_writes_inspector_panel_markup(tmp_path):
    settings = Settings(output_dir=tmp_path / "output")
    items = [
        ClusteredItem(
            id="a",
            source=SourcePlatform.INSTAGRAM,
            item_type=ItemType.SAVED_POST,
            content="A great weeknight pasta recipe",
            collections=["Recipes"],
            cluster_id=0,
            cluster_label="Food",
            x=0.1,
            y=0.2,
        ),
    ]

    path = build_map(items, settings)

    rendered = path.read_text(encoding="utf-8")
    assert 'id="cg-inspector"' in rendered
    assert 'id="cg-inspector-close"' in rendered
    assert "searchIndex" in rendered
    assert "A great weeknight pasta recipe" in rendered  # full text reaches customdata
