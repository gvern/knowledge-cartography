from __future__ import annotations

from collections import Counter

from .cluster import load_cluster_cache
from .config import settings
from .embed import semantic_search
from .export import cluster_summaries
from .schema import ClusteredItem, SourcePlatform

# Live "second brain" access: an MCP server exposing this knowledge graph as
# tools an agent can call in conversation, instead of only static export
# files (see export.py). The `mcp` SDK is an optional dependency (`pip
# install .[mcp]`) imported lazily inside `run()` — everything else in this
# module is plain functions with no import-time dependency on it, so the
# tools themselves stay unit-testable without installing the SDK.

_cache: list[ClusteredItem] | None = None


def _get_items() -> list[ClusteredItem]:
    global _cache
    if _cache is None:
        items = load_cluster_cache(settings)
        if items is None:
            raise RuntimeError(
                "No cluster cache found. Run `cartography cluster` first to build the knowledge graph."
            )
        _cache = items
    return _cache


def _visible_items(items: list[ClusteredItem]) -> list[ClusteredItem]:
    """Items safe to hand to whatever LLM is on the other end of an MCP tool
    call — Messenger content is excluded unconditionally, the same treatment
    `label.py` already gives it for the Anthropic labeling call, extended
    here because a tool result is read by an LLM the same way (see the
    Messenger policy bullet in CLAUDE.md)."""
    return [item for item in items if item.source != SourcePlatform.MESSENGER]


def _item_summary(item: ClusteredItem) -> dict:
    return {
        "id": item.id,
        "text": item.text,
        "source": item.source.value,
        "item_type": item.item_type.value,
        "url": item.url,
        "timestamp": item.timestamp.isoformat() if item.timestamp else None,
        "cluster_id": item.cluster_id,
        "cluster_label": item.cluster_label,
        "collections": item.collections,
    }


def stats() -> dict:
    """Orient yourself: item/cluster/collection counts, source breakdown, and
    date range for this knowledge graph. A good first call before searching
    or browsing clusters."""
    items = _visible_items(_get_items())
    clusters = {item.cluster_id for item in items if item.cluster_id != -1}
    collections = {name for item in items for name in item.collections}
    timestamps = [item.comparable_timestamp for item in items if item.comparable_timestamp]
    return {
        "total_items": len(items),
        "total_clusters": len(clusters),
        "total_collections": len(collections),
        "sources": dict(Counter(item.source.value for item in items)),
        "date_range": {
            "first": min(timestamps).isoformat() if timestamps else None,
            "last": max(timestamps).isoformat() if timestamps else None,
        },
    }


def list_clusters() -> list[dict]:
    """List every topic cluster: id, label, a few keyword tags, size, source
    breakdown, and date range — the table of contents for this knowledge
    graph. Use `get_cluster` to read a cluster's actual items."""
    return cluster_summaries(_visible_items(_get_items()))


def get_cluster(cluster_id: int, limit: int = 50) -> dict:
    """Get one cluster's metadata plus up to `limit` of its items (fullest
    text first isn't guaranteed — this is source order, not relevance;
    use `search_knowledge` for relevance-ranked results)."""
    cluster_items = [item for item in _visible_items(_get_items()) if item.cluster_id == cluster_id]
    if not cluster_items:
        raise ValueError(f"No visible items in cluster {cluster_id}")
    summary = cluster_summaries(cluster_items)[0]
    summary["items"] = [_item_summary(item) for item in cluster_items[:limit]]
    return summary


def list_collections() -> list[dict]:
    """List the user's own curated collections (not algorithmic clusters) by
    name and item count."""
    counts: dict[str, int] = {}
    for item in _visible_items(_get_items()):
        for name in item.collections:
            counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda kv: -kv[1])]


def get_collection_items(name: str, limit: int = 50) -> list[dict]:
    """Get up to `limit` items from one named collection."""
    matches = [item for item in _visible_items(_get_items()) if name in item.collections]
    if not matches:
        raise ValueError(f"No visible items in collection {name!r}")
    return [_item_summary(item) for item in matches[:limit]]


def search_knowledge(query: str, limit: int = 10) -> list[dict]:
    """Semantic search over the whole knowledge graph — the main way to answer
    "what do I know about X" or "did I save anything about Y"."""
    # Over-fetch: some hits may be Messenger content filtered out below, and
    # over-fetching by 2x keeps `limit` meaningful without a second round trip.
    results = semantic_search(query, settings, limit=limit * 2)
    visible = [r for r in results if r["source"] != SourcePlatform.MESSENGER.value]
    return visible[:limit]


def run() -> None:
    """Start the MCP server over stdio — register with Claude Code/Desktop as
    a local MCP server, e.g. `claude mcp add cartography -- cartography mcp`."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("knowledge-cartography")
    for tool in (stats, list_clusters, get_cluster, list_collections, get_collection_items, search_knowledge):
        server.add_tool(tool)
    server.run()
