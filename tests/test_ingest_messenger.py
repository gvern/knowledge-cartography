import json
from datetime import datetime

from cartography.ingest import messenger
from cartography.schema import ItemType, SourcePlatform


def _thread(
    tmp_path, messages, title="Some Thread", thread_path="inbox/thread_abc123", filename="message_1.json"
):
    thread_dir = tmp_path / "your_facebook_activity" / "messages" / "inbox" / "thread_abc123"
    thread_dir.mkdir(parents=True)
    data = {
        "participants": [{"name": "A Friend"}],
        "title": title,
        "thread_path": thread_path,
        "messages": messages,
    }
    (thread_dir / filename).write_text(json.dumps(data), encoding="utf-8")


def test_parse_text_message(tmp_path):
    message = {"sender_name": "A Friend", "timestamp_ms": 1700000000000, "content": "Hey, check this out"}
    _thread(tmp_path, [message])

    items = messenger.parse(tmp_path)

    assert len(items) == 1
    assert items[0].source == SourcePlatform.MESSENGER
    assert items[0].item_type == ItemType.MESSAGE
    assert items[0].content == "Hey, check this out"
    assert items[0].sender == "A Friend"
    assert items[0].thread == "Some Thread"


def test_parse_skips_attachment_only_messages(tmp_path):
    message = {"sender_name": "A Friend", "timestamp_ms": 1700000000000, "photos": [{"uri": "x.jpg"}]}
    _thread(tmp_path, [message])

    items = messenger.parse(tmp_path)

    assert items == []


def test_parse_skips_unsent_messages(tmp_path):
    _thread(
        tmp_path,
        [{"sender_name": "A Friend", "timestamp_ms": 1700000000000, "content": "oops", "is_unsent": True}],
    )

    items = messenger.parse(tmp_path)

    assert items == []


def test_parse_fixes_mojibake_encoding(tmp_path):
    # "café" mis-encoded the way Facebook's export bug produces it.
    mangled = "café".encode().decode("latin1")
    _thread(tmp_path, [{"sender_name": "A Friend", "timestamp_ms": 1700000000000, "content": mangled}])

    items = messenger.parse(tmp_path)

    assert items[0].content == "café"


def test_parse_merges_multiple_message_files_in_same_thread(tmp_path):
    thread_dir = tmp_path / "your_facebook_activity" / "messages" / "inbox" / "thread_abc123"
    thread_dir.mkdir(parents=True)
    for n, text in [(1, "first batch"), (2, "second batch")]:
        data = {
            "title": "Some Thread",
            "thread_path": "inbox/thread_abc123",
            "messages": [{"sender_name": "A Friend", "timestamp_ms": 1700000000000 + n, "content": text}],
        }
        (thread_dir / f"message_{n}.json").write_text(json.dumps(data), encoding="utf-8")

    items = messenger.parse(tmp_path)

    assert {item.content for item in items} == {"first batch", "second batch"}


def test_parse_ignores_missing_files(tmp_path):
    assert messenger.parse(tmp_path) == []


def _html_thread(tmp_path, sections_html, title="Aliénor Ddr", thread_dir="alienorddr_10204790991968331"):
    thread_path = tmp_path / "your_facebook_activity" / "messages" / "inbox" / thread_dir
    thread_path.mkdir(parents=True)
    html = (
        "<html><body>"
        f'<header><div class="_a70d"><h1>{title}</h1></div></header>'
        f"<main>{sections_html}</main>"
        "</body></html>"
    )
    (thread_path / "message_1.html").write_text(html, encoding="utf-8")


def test_parse_html_text_message(tmp_path):
    _html_thread(
        tmp_path,
        '<section class="_a6-g"><h2 class="_a6-h">Gustave Vernay</h2>'
        '<div class="_2ph_ _a6-p"><div>Merci beaucoup !</div></div>'
        '<footer><div class="_a72d">juil 13, 2021 10:58:25 am</div></footer></section>',
    )

    items = messenger.parse(tmp_path)

    assert len(items) == 1
    assert items[0].content == "Merci beaucoup !"
    assert items[0].sender == "Gustave Vernay"
    assert items[0].thread == "Aliénor Ddr"
    assert items[0].timestamp == datetime(2021, 7, 13, 10, 58, 25)


def test_parse_html_timestamp_handles_pm_and_unrecognized_month(tmp_path):
    _html_thread(
        tmp_path,
        '<section class="_a6-g"><h2 class="_a6-h">Gustave Vernay</h2>'
        '<div class="_2ph_ _a6-p"><div>soir</div></div>'
        '<footer><div class="_a72d">août 29, 2019 9:28:24 pm</div></footer></section>'
        '<section class="_a6-g"><h2 class="_a6-h">Gustave Vernay</h2>'
        '<div class="_2ph_ _a6-p"><div>inconnu</div></div>'
        '<footer><div class="_a72d">wat 1, 2019 1:00:00 am</div></footer></section>',
    )

    items = messenger.parse(tmp_path)

    assert items[0].timestamp == datetime(2019, 8, 29, 21, 28, 24)
    assert items[1].timestamp is None


def test_parse_html_carries_sender_forward_when_heading_omitted(tmp_path):
    _html_thread(
        tmp_path,
        '<section class="_a6-g"><h2 class="_a6-h">Aliénor Ddr</h2>'
        '<div class="_2ph_ _a6-p"><div>Premier message</div></div>'
        '<footer><div class="_a72d">t1</div></footer></section>'
        '<section class="_a6-g">'
        '<div class="_2ph_ _a6-p"><div>Deuxième message</div></div>'
        '<footer><div class="_a72d">t2</div></footer></section>',
    )

    items = messenger.parse(tmp_path)

    assert [item.sender for item in items] == ["Aliénor Ddr", "Aliénor Ddr"]


def test_parse_html_skips_attachment_only_messages(tmp_path):
    _html_thread(
        tmp_path,
        '<section class="_a6-g"><h2 class="_a6-h">Gustave Vernay</h2>'
        '<div class="_2ph_ _a6-p"><div><div></div><div></div><div></div><div></div>'
        '<div><div><a href="x.png"><img src="x.png" /></a></div></div></div></div>'
        '<footer><div class="_a72d">t1</div></footer></section>',
    )

    items = messenger.parse(tmp_path)

    assert items == []


def test_parse_html_strips_reactions_from_content(tmp_path):
    _html_thread(
        tmp_path,
        '<section class="_a6-g"><h2 class="_a6-h">Gustave Vernay</h2>'
        '<div class="_2ph_ _a6-p"><div><div></div><div>peut être oui</div><div></div><div></div>'
        '<div><ul class="_a6-q"><li><span>\U0001f44dAliénor Ddr</span></li></ul></div></div></div>'
        '<footer><div class="_a72d">t1</div></footer></section>',
    )

    items = messenger.parse(tmp_path)

    assert len(items) == 1
    assert items[0].content == "peut être oui"
