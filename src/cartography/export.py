from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.neighbors import NearestNeighbors

from .config import Settings
from .schema import ClusteredItem

logger = logging.getLogger(__name__)

_GRAPH_FILENAME = "knowledge_graph.json"
_NOTES_DIRNAME = "notes"
_MIN_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)


def build_graph(items: list[ClusteredItem], k_neighbors: int = 6) -> dict:
    """A structured knowledge graph — nodes, clusters, and edges — meant to be
    loaded by another tool (an agent, a RAG pipeline, a script) rather than a
    browser. The HTML map already carries this same data for a human; this is
    the same underlying knowledge, reshaped for machine consumption."""
    clusters = _cluster_summaries(items)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_items": len(items),
        "total_clusters": len(clusters),
        "clusters": clusters,
        "nodes": [_node(item) for item in items],
        "edges": _semantic_edges(items, k_neighbors) + _structural_edges(items),
    }


def write_graph_json(
    items: list[ClusteredItem], settings: Settings, output_name: str = _GRAPH_FILENAME, k_neighbors: int = 6
) -> Path:
    settings.ensure_dirs()
    graph = build_graph(items, k_neighbors=k_neighbors)
    output_path = settings.output_dir / output_name
    output_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Wrote knowledge graph (%d nodes, %d edges, %d clusters) to %s",
        len(graph["nodes"]),
        len(graph["edges"]),
        len(graph["clusters"]),
        output_path,
    )
    return output_path


def _node(item: ClusteredItem) -> dict:
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
        "thread_id": item.thread_id or None,
        "thread": item.thread or None,
        "sender": item.sender or None,
        "coords": {"x": item.x, "y": item.y, "z": item.z},
    }


def _cluster_summaries(items: list[ClusteredItem]) -> list[dict]:
    by_cluster: dict[int, list[ClusteredItem]] = defaultdict(list)
    for item in items:
        if item.cluster_id != -1:
            by_cluster[item.cluster_id].append(item)

    clusters = []
    for cluster_id, cluster_items in by_cluster.items():
        timestamps = [item.timestamp for item in cluster_items if item.timestamp]
        n = len(cluster_items)
        clusters.append(
            {
                "id": cluster_id,
                "label": cluster_items[0].cluster_label,
                "size": n,
                "centroid": {
                    "x": sum(i.x for i in cluster_items) / n,
                    "y": sum(i.y for i in cluster_items) / n,
                    "z": sum(i.z for i in cluster_items) / n,
                },
                "sources": dict(Counter(item.source.value for item in cluster_items)),
                "date_range": {
                    "first": min(timestamps).isoformat() if timestamps else None,
                    "last": max(timestamps).isoformat() if timestamps else None,
                },
            }
        )
    return sorted(clusters, key=lambda c: -c["size"])


def _semantic_edges(items: list[ClusteredItem], k: int) -> list[dict]:
    """Nearest-neighbor edges in UMAP coordinate space, as a fast proxy for
    embedding-space similarity — UMAP is built to preserve local neighborhood
    structure, and re-fetching every raw high-dimensional embedding from
    Chroma just to run k-NN again would be redundant with the projection
    already computed for the map."""
    if k < 1 or len(items) <= k:
        return []

    coords = np.array([[item.x, item.y, item.z] for item in items])
    n_neighbors = min(k + 1, len(items))  # +1: a point is its own nearest neighbor
    _, indices = NearestNeighbors(n_neighbors=n_neighbors).fit(coords).kneighbors(coords)

    edges = []
    seen: set[tuple[str, str]] = set()
    for i, neighbor_idx in enumerate(indices):
        for j in neighbor_idx:
            if i == j:
                continue
            a, b = sorted((items[i].id, items[int(j)].id))
            pair = (a, b)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({"source": pair[0], "target": pair[1], "type": "semantic_neighbor"})
    return edges


def _structural_edges(items: list[ClusteredItem]) -> list[dict]:
    """Edges from groupings that already exist in the source data — shared
    collections (the user's own curation) and shared conversation threads —
    independent of embedding geometry."""
    ordered = sorted(items, key=lambda item: item.timestamp or _MIN_TIMESTAMP)

    by_collection: dict[str, list[str]] = defaultdict(list)
    by_thread: dict[str, list[str]] = defaultdict(list)
    for item in ordered:
        for name in item.collections:
            by_collection[name].append(item.id)
        if item.thread_id:
            by_thread[item.thread_id].append(item.id)

    return _chain_edges(by_collection, "same_collection") + _chain_edges(by_thread, "same_thread")


def _chain_edges(groups: dict[str, list[str]], edge_type: str) -> list[dict]:
    """One edge per consecutive pair within each group — a chain, not a full
    clique, so a large shared thread or collection contributes edges linear
    in its size rather than quadratic."""
    edges = []
    for name, ids in groups.items():
        for a, b in zip(ids, ids[1:], strict=False):
            edges.append({"source": a, "target": b, "type": edge_type, "label": name})
    return edges


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return _SLUG_RE.sub("-", folded).strip("-") or "untitled"


def write_markdown_vault(
    items: list[ClusteredItem], settings: Settings, dir_name: str = _NOTES_DIRNAME
) -> Path:
    """An Obsidian-compatible Markdown vault: one note per cluster and one per
    collection, cross-linked with wikilinks — a literal second-brain artifact
    that's browsable, greppable, and RAG-able without any tool beyond a text
    editor, independent of the HTML map."""
    settings.ensure_dirs()
    notes_dir = settings.output_dir / dir_name
    notes_dir.mkdir(parents=True, exist_ok=True)

    by_cluster: dict[int, list[ClusteredItem]] = defaultdict(list)
    by_collection: dict[str, list[ClusteredItem]] = defaultdict(list)
    for item in items:
        if item.cluster_id != -1:
            by_cluster[item.cluster_id].append(item)
        for name in item.collections:
            by_collection[name].append(item)

    cluster_labels = {cid: citems[0].cluster_label or f"Cluster {cid}" for cid, citems in by_cluster.items()}
    cluster_slugs = {cid: _slug(f"cluster-{cid}-{label}") for cid, label in cluster_labels.items()}
    collection_slugs = {name: _slug(f"collection-{name}") for name in by_collection}

    for cluster_id, cluster_items in by_cluster.items():
        _write_cluster_note(
            notes_dir,
            cluster_slugs[cluster_id],
            cluster_id,
            cluster_labels[cluster_id],
            cluster_items,
            collection_slugs,
        )
    for name, coll_items in by_collection.items():
        _write_collection_note(
            notes_dir, collection_slugs[name], name, coll_items, cluster_slugs, cluster_labels
        )
    _write_index(notes_dir, by_cluster, by_collection, cluster_slugs, collection_slugs, cluster_labels)

    logger.info(
        "Wrote %d cluster notes and %d collection notes to %s", len(by_cluster), len(by_collection), notes_dir
    )
    return notes_dir


def _item_line(item: ClusteredItem) -> str:
    when = item.timestamp.strftime("%Y-%m-%d") if item.timestamp else "?"
    who = f"**{item.sender}**: " if item.sender else ""
    text = " ".join(item.text.split()) if item.text else "(no text)"
    if len(text) > 300:
        text = text[:299].rstrip() + "…"
    link = f" ([source]({item.url}))" if item.url else ""
    return f"- `{when}` {who}{text}{link}"


def _write_cluster_note(
    notes_dir: Path,
    slug: str,
    cluster_id: int,
    label: str,
    cluster_items: list[ClusteredItem],
    collection_slugs: dict[str, str],
) -> None:
    timestamps = [i.timestamp for i in cluster_items if i.timestamp]
    related = sorted({name for item in cluster_items for name in item.collections})

    lines = [
        "---",
        f"cluster_id: {cluster_id}",
        f"size: {len(cluster_items)}",
        f"first: {min(timestamps).isoformat() if timestamps else ''}",
        f"last: {max(timestamps).isoformat() if timestamps else ''}",
        "---",
        "",
        f"# {label}",
        "",
    ]
    if related:
        lines.append("## Collections")
        lines += [f"- [[{collection_slugs[name]}|{name}]]" for name in related]
        lines.append("")
    lines.append("## Items")
    lines += [_item_line(item) for item in sorted(cluster_items, key=lambda i: i.timestamp or _MIN_TIMESTAMP)]

    (notes_dir / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")


def _write_collection_note(
    notes_dir: Path,
    slug: str,
    name: str,
    coll_items: list[ClusteredItem],
    cluster_slugs: dict[int, str],
    cluster_labels: dict[int, str],
) -> None:
    related = sorted({item.cluster_id for item in coll_items if item.cluster_id != -1})

    lines = ["---", f"size: {len(coll_items)}", "---", "", f"# {name}", ""]
    if related:
        lines.append("## Related clusters")
        lines += [f"- [[{cluster_slugs[cid]}|{cluster_labels[cid]}]]" for cid in related]
        lines.append("")
    lines.append("## Items")
    lines += [_item_line(item) for item in sorted(coll_items, key=lambda i: i.timestamp or _MIN_TIMESTAMP)]

    (notes_dir / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")


def _write_index(
    notes_dir: Path,
    by_cluster: dict[int, list[ClusteredItem]],
    by_collection: dict[str, list[ClusteredItem]],
    cluster_slugs: dict[int, str],
    collection_slugs: dict[str, str],
    cluster_labels: dict[int, str],
) -> None:
    total = sum(len(v) for v in by_cluster.values())
    lines = [
        "# Knowledge Cartography — Index",
        "",
        f"{total} clustered items across {len(by_cluster)} clusters and {len(by_collection)} collections.",
        "",
        "## Clusters (by size)",
    ]
    for cluster_id, cluster_items in sorted(by_cluster.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- [[{cluster_slugs[cluster_id]}|{cluster_labels[cluster_id]}]] ({len(cluster_items)})")

    lines += ["", "## Collections (by size)"]
    for name, coll_items in sorted(by_collection.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- [[{collection_slugs[name]}|{name}]] ({len(coll_items)})")

    (notes_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")
