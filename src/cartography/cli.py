from __future__ import annotations

import logging

import click

from .cluster import cluster_items, load_cluster_cache, save_cluster_cache
from .config import settings
from .embed import embed_items, get_collection, get_embedder
from .export import write_graph_json, write_markdown_vault
from .ingest import facebook, google, instagram, messenger
from .ingest.enrich import enrich_items
from .label import label_clusters
from .viz import build_map

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    """Map your personal knowledge graph from social media exports and browsing history."""


@cli.command()
@click.option(
    "--instagram",
    "instagram_dir",
    type=click.Path(exists=True, file_okay=False),
    help="Instagram GDPR export directory",
)
@click.option(
    "--facebook",
    "facebook_dir",
    type=click.Path(exists=True, file_okay=False),
    help="Facebook GDPR export directory",
)
@click.option(
    "--google",
    "google_dir",
    type=click.Path(exists=True, file_okay=False),
    help="Google Takeout export directory",
)
@click.option(
    "--messenger",
    "messenger_dir",
    type=click.Path(exists=True, file_okay=False),
    help="Facebook export directory containing Messenger threads (opt-in; always embedded "
    "locally via Ollama and never sent to the Claude API for labeling, see docs/ARCHITECTURE.md)",
)
@click.option(
    "--bookmarks",
    "bookmarks_path",
    type=click.Path(exists=True, dir_okay=False),
    help="Browser bookmarks.html export",
)
@click.option(
    "--enrich/--no-enrich",
    default=False,
    help="Fetch and extract full text for items that only have a URL",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Skip items already embedded from a previous run (for resuming an interrupted ingest)",
)
def ingest(instagram_dir, facebook_dir, google_dir, messenger_dir, bookmarks_path, enrich, resume) -> None:
    """Parse exports into knowledge items and embed them into the vector store."""
    items = []
    if instagram_dir:
        items += instagram.parse(instagram_dir)
    if facebook_dir:
        items += facebook.parse(facebook_dir)
    if google_dir:
        items += google.parse_takeout(google_dir)
    if messenger_dir:
        items += messenger.parse(messenger_dir)
    if bookmarks_path:
        items += google.parse_bookmarks(bookmarks_path)

    if not items:
        raise click.UsageError(
            "Provide at least one of --instagram, --facebook, --google, --messenger, --bookmarks"
        )

    click.echo(f"Parsed {len(items)} items")

    if enrich:
        items = enrich_items(items)

    embedded = embed_items(items, settings, skip_existing=resume)
    click.echo(f"Embedded {embedded} items into {settings.chroma_dir}")


@cli.command()
@click.option("--no-label", is_flag=True, help="Skip cluster auto-labeling via the Claude API")
@click.option("--output", "output_name", default="knowledge_map.html", help="Output HTML filename")
@click.option(
    "--from-cache",
    is_flag=True,
    help="Skip UMAP/HDBSCAN/labeling and re-render from the previous run's cached result "
    "(fast — for iterating on the map's HTML/JS only)",
)
def cluster(no_label, output_name, from_cache) -> None:
    """Reduce embeddings to 2D, cluster them, and render an interactive map."""
    items = load_cluster_cache(settings) if from_cache else None
    if items is None:
        if from_cache:
            click.echo("No cache found, computing from scratch")
        items = cluster_items(settings)
        if not no_label:
            items = label_clusters(items, settings)
        save_cluster_cache(items, settings)
    path = build_map(items, settings, output_name)
    click.echo(f"Map written to {path}")


@cli.command()
def stats() -> None:
    """Show basic stats about the current vector store."""
    collection = get_collection(settings)
    click.echo(f"{collection.count()} items in {settings.chroma_dir}")


@cli.command(name="export")
@click.option(
    "--format",
    "export_format",
    type=click.Choice(["json", "markdown", "both"]),
    default="both",
    help="Structured JSON graph (json), an Obsidian-style Markdown vault (markdown), or both",
)
@click.option("--output", "json_output_name", default="knowledge_graph.json", help="JSON graph filename")
@click.option(
    "--notes-dir", default="notes", help="Markdown vault directory name (under CARTOGRAPHY_OUTPUT_DIR)"
)
@click.option(
    "--neighbors", "k_neighbors", default=6, help="Semantic nearest-neighbor edges per node in the JSON graph"
)
@click.option(
    "--from-cache/--recompute",
    default=True,
    help="Reuse the previous cluster/label run's cache instead of recomputing UMAP/HDBSCAN/labels",
)
def export_cmd(export_format, json_output_name, notes_dir, k_neighbors, from_cache) -> None:
    """Export the clustered knowledge graph for other tools: a structured JSON graph
    (nodes/edges/clusters) and/or an Obsidian-compatible Markdown vault — the same
    knowledge the HTML map shows a human, reshaped for an agent or a RAG pipeline."""
    items = load_cluster_cache(settings) if from_cache else None
    if items is None:
        if from_cache:
            click.echo("No cache found, computing from scratch")
        items = cluster_items(settings)
        items = label_clusters(items, settings)
        save_cluster_cache(items, settings)

    if export_format in ("json", "both"):
        path = write_graph_json(items, settings, json_output_name, k_neighbors=k_neighbors)
        click.echo(f"Graph exported to {path}")
    if export_format in ("markdown", "both"):
        path = write_markdown_vault(items, settings, notes_dir)
        click.echo(f"Markdown vault written to {path}")


@cli.command()
@click.argument("query")
@click.option("--limit", default=10, help="Number of results to return")
def search(query, limit) -> None:
    """Semantic search over the local vector store — a quick way for the user or
    an agent to query the second brain without opening the map."""
    vector = get_embedder(settings).embed([query])[0]
    results = get_collection(settings).query(query_embeddings=[vector], n_results=limit)

    ids = results["ids"][0]
    if not ids:
        click.echo("No results.")
        return

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    for i, (document, metadata, distance) in enumerate(
        zip(documents, metadatas, distances, strict=True), start=1
    ):
        title = metadata.get("title") or (document or "")[:80]
        source = metadata.get("source", "")
        click.echo(f"{i}. [{distance:.3f}] ({source}) {title}")
        if metadata.get("url"):
            click.echo(f"   {metadata['url']}")


if __name__ == "__main__":
    cli()
