from __future__ import annotations

import logging

import click

from .cluster import cluster_items, load_cluster_cache, save_cluster_cache
from .config import settings
from .embed import embed_items, get_collection
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


if __name__ == "__main__":
    cli()
