import json

from cartography.ingest import instagram
from cartography.schema import ItemType


def test_parse_saved_and_liked_posts(tmp_path):
    saved_href = {"href": "https://instagram.com/p/abc", "timestamp": 1700000000}
    saved = {"saved_saved_media": [{"title": "Some Account", "string_map_data": {"Saved on": saved_href}}]}
    liked = {
        "likes_media_likes": [
            {
                "title": "Other Account",
                "string_list_data": [{"href": "https://instagram.com/p/def", "timestamp": 1700000100}],
            }
        ]
    }
    (tmp_path / "saved_posts.json").write_text(json.dumps(saved))
    (tmp_path / "liked_posts.json").write_text(json.dumps(liked))

    items = instagram.parse(tmp_path)

    assert len(items) == 2
    urls = {item.url for item in items}
    assert urls == {"https://instagram.com/p/abc", "https://instagram.com/p/def"}


def test_parse_skips_entries_without_href(tmp_path):
    saved = {"saved_saved_media": [{"title": "No href entry"}]}
    (tmp_path / "saved_posts.json").write_text(json.dumps(saved))

    items = instagram.parse(tmp_path)

    assert items == []


_HTML_POST_ENTRY = (
    '<table><tr><td colspan="2" class="_a6_q">URL<div>'
    '<a target="_blank" href="https://www.instagram.com/p/{slug}/">'
    "https://www.instagram.com/p/{slug}/</a></div></td></tr>"
    '<tr><td class="_a6_q">Légende</td><td class="_2piu _a6_r">{caption}</td></tr></table>'
)


def test_parse_html_saved_posts_extracts_url_and_caption(tmp_path):
    html = (
        "<html><body>" + _HTML_POST_ENTRY.format(slug="ABC123", caption="A nice caption") + "</body></html>"
    )
    (tmp_path / "saved_posts.html").write_text(html, encoding="utf-8")

    items = instagram.parse(tmp_path)

    assert len(items) == 1
    assert items[0].url == "https://www.instagram.com/p/ABC123/"
    assert items[0].content == "A nice caption"
    assert items[0].item_type == ItemType.SAVED_POST


def test_parse_html_liked_posts_dedupes_repeated_urls(tmp_path):
    entry = _HTML_POST_ENTRY.format(slug="DEF456", caption="Caption")
    html = f"<html><body>{entry}{entry}</body></html>"
    (tmp_path / "liked_posts.html").write_text(html, encoding="utf-8")

    items = instagram.parse(tmp_path)

    assert len(items) == 1
    assert items[0].item_type == ItemType.LIKED_POST


def _collection_block(name: str, *posts: str) -> str:
    header = (
        f'<td class="_a6_q">Nom</td><td class="_2piu _a6_r">{name}</td></tr>'
        '<tr><td class="_a6_q">Type</td><td class="_2piu _a6_r">Par défaut</td></tr>'
        '<tr><td class="_a6_q">Confidentialité</td><td class="_2piu _a6_r">Privées</td></tr>'
        '<tr><td class="_a6_q">Heure de mise à jour</td><td class="_2piu _a6_r">août 03, 2026</td></tr>'
    )
    return f"<table><tr>{header}{''.join(posts)}</table>"


def test_parse_collections_attaches_names_to_existing_saved_post(tmp_path):
    entry = _HTML_POST_ENTRY.format(slug="GHI789", caption="Pasta recipe")
    (tmp_path / "saved_posts.html").write_text(f"<html><body>{entry}</body></html>", encoding="utf-8")

    collections_html = "<html><body>" + _collection_block("Recipes", entry) + "</body></html>"
    (tmp_path / "saved_collections.html").write_text(collections_html, encoding="utf-8")

    items = instagram.parse(tmp_path)

    assert len(items) == 1
    assert items[0].collections == ["Recipes"]


def test_parse_collections_creates_item_for_url_only_in_a_collection(tmp_path):
    entry = _HTML_POST_ENTRY.format(slug="JKL012", caption="Only in a collection")
    collections_html = "<html><body>" + _collection_block("Travel", entry) + "</body></html>"
    (tmp_path / "saved_collections.html").write_text(collections_html, encoding="utf-8")

    items = instagram.parse(tmp_path)

    assert len(items) == 1
    assert items[0].collections == ["Travel"]
    assert items[0].content == "Only in a collection"
    assert items[0].item_type == ItemType.SAVED_POST


def test_parse_collections_accumulates_multiple_collection_names(tmp_path):
    entry = _HTML_POST_ENTRY.format(slug="MNO345", caption="Shared post")
    collections_html = (
        "<html><body>"
        + _collection_block("Recipes", entry)
        + _collection_block("Favorites", entry)
        + "</body></html>"
    )
    (tmp_path / "saved_collections.html").write_text(collections_html, encoding="utf-8")

    items = instagram.parse(tmp_path)

    assert len(items) == 1
    assert items[0].collections == ["Recipes", "Favorites"]
