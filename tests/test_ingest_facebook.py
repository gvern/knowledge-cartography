import json

from cartography.ingest import facebook
from cartography.schema import ItemType


def test_parse_saved_items_with_url(tmp_path):
    saved = {
        "saved_items_v2": [
            {
                "title": "An article",
                "timestamp": 1700000000,
                "attachments": [{"data": [{"external_context": {"url": "https://example.com/article"}}]}],
            }
        ]
    }
    (tmp_path / "saved_items_and_collections.json").write_text(json.dumps(saved))

    items = facebook.parse(tmp_path)

    assert len(items) == 1
    assert items[0].url == "https://example.com/article"


def test_parse_followed_pages(tmp_path):
    followed = {"pages": [{"name": "A Page", "timestamp": 1700000000}]}
    (tmp_path / "pages_you_follow.json").write_text(json.dumps(followed))

    items = facebook.parse(tmp_path)

    assert len(items) == 1
    assert items[0].title == "A Page"


def test_parse_ignores_missing_files(tmp_path):
    assert facebook.parse(tmp_path) == []


def test_parse_saved_html_unwraps_redirect_and_extracts_title(tmp_path):
    html = (
        '<html><body><section class="_a6-g">'
        '<h2 class="_2ph_ _a6-h _a6-i">Gustave Vernay a enregistré un lien.</h2>'
        "<div>Topito Top 10 des choses</div>"
        '<footer><a href="https://www.facebook.com/dyi/l/?l=https%3A%2F%2Fexample.com%2Farticle&amp;s=1">'
        "<div>juil 25, 2014 12:06:36 am</div></a></footer>"
        "</section></body></html>"
    )
    (tmp_path / "your_saved_items.html").write_text(html, encoding="utf-8")

    items = facebook.parse(tmp_path)

    assert len(items) == 1
    assert items[0].url == "https://example.com/article"
    assert "enregistré un lien" in items[0].title


def test_parse_followed_html_extracts_page_names(tmp_path):
    html = (
        "<html><body>"
        '<section class="_a6-g"><h2 class="_2ph_ _a6-h _a6-i">Starbucks France</h2>'
        '<footer><div class="_a72d">mai 28, 2013 10:31:26 pm</div></footer></section>'
        "</body></html>"
    )
    (tmp_path / "pages_and_profiles_you_follow.html").write_text(html, encoding="utf-8")

    items = facebook.parse(tmp_path)

    assert len(items) == 1
    assert items[0].title == "Starbucks France"
    assert items[0].item_type == ItemType.FOLLOWED_PAGE
