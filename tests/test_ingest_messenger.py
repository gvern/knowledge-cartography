import json

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
    assert items[0].metadata["sender"] == "A Friend"
    assert items[0].metadata["thread"] == "Some Thread"


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
