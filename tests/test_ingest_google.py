from cartography.ingest import google

_BOOKMARKS_HTML = """
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><A HREF="http://root-link.com" ADD_DATE="1600000000">Root Link</A>
    <DT><H3 ADD_DATE="1600000001">Recipes</H3>
    <DL><p>
        <DT><A HREF="http://pasta.com" ADD_DATE="1600000002">Pasta</A>
        <DT><A HREF="http://pizza.com" ADD_DATE="1600000003">Pizza</A>
    </DL><p>
    <DT><H3 ADD_DATE="1600000004">Travel</H3>
    <DL><p>
        <DT><A HREF="http://paris.com" ADD_DATE="1600000005">Paris guide</A>
    </DL><p>
</DL><p>
"""


def test_parse_bookmarks_assigns_folder_as_collection(tmp_path):
    path = tmp_path / "bookmarks.html"
    path.write_text(_BOOKMARKS_HTML, encoding="utf-8")

    items = google.parse_bookmarks(path)
    by_url = {item.url: item for item in items}

    assert len(items) == 4
    assert by_url["http://root-link.com"].collections == []
    assert by_url["http://pasta.com"].collections == ["Recipes"]
    assert by_url["http://pizza.com"].collections == ["Recipes"]
    assert by_url["http://paris.com"].collections == ["Travel"]


def test_parse_bookmarks_extracts_title_and_timestamp(tmp_path):
    path = tmp_path / "bookmarks.html"
    path.write_text(_BOOKMARKS_HTML, encoding="utf-8")

    items = google.parse_bookmarks(path)
    by_url = {item.url: item for item in items}

    pasta = by_url["http://pasta.com"]
    assert pasta.title == "Pasta"
    assert pasta.timestamp is not None
