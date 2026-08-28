from cartography.schema import ItemType, KnowledgeItem, SourcePlatform


def test_knowledge_item_text_joins_title_and_content():
    item = KnowledgeItem(
        id="abc",
        source=SourcePlatform.BOOKMARK,
        item_type=ItemType.BOOKMARK,
        title="Title",
        content="Content",
    )
    assert item.text == "Title\nContent"


def test_knowledge_item_text_skips_empty_parts():
    item = KnowledgeItem(
        id="abc",
        source=SourcePlatform.BOOKMARK,
        item_type=ItemType.BOOKMARK,
        title="Only title",
    )
    assert item.text == "Only title"
