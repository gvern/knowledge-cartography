# knowledge-cartography

Builds an interactive 2D map of a personal knowledge graph from social media
exports (Instagram, Facebook) and browsing history (Google Takeout, HTML
bookmarks). Pipeline: ingest → embed (Ollama or Vertex AI) → UMAP → HDBSCAN →
label clusters via the Anthropic API → render an HTML map (Plotly).

## Commands

```bash
uv sync --extra local --group dev   # deps + ruff/pytest/mypy
uv run pytest                        # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy src/cartography
uv run cartography ingest --instagram <dir> --facebook <dir> --google <dir> --bookmarks <file>
uv run cartography cluster
uv run cartography stats
```

CI (`.github/workflows/ci.yml`) runs all four checks above on push/PR to
`main` across Python 3.10 and 3.12 — keep it green before merging.

## Config

All settings are env vars prefixed `CARTOGRAPHY_` (see `src/cartography/config.py`),
loaded from a gitignored `.env` at the repo root. Key ones: `CARTOGRAPHY_ANTHROPIC_API_KEY`
(cluster labeling), `CARTOGRAPHY_CHROMA_DIR` / `CARTOGRAPHY_OUTPUT_DIR` (point these at a
NAS mount to persist outside local disk — reachable over Tailscale in this setup).

Local embeddings require `ollama serve` running with the `nomic-embed-text` model pulled.

## Structure

```
src/cartography/
├── cli.py            # click entrypoint (ingest/cluster/stats)
├── config.py          # env-driven Settings (pydantic-settings)
├── schema.py           # KnowledgeItem / ClusteredItem (pydantic)
├── embed.py             # Ollama or Vertex AI -> ChromaDB
├── cluster.py             # UMAP + HDBSCAN
├── label.py                 # cluster naming via the Anthropic API
├── viz.py                    # Plotly HTML map
└── ingest/
    ├── instagram.py           # saved/liked posts (GDPR JSON export)
    ├── facebook.py              # saved items + followed pages (GDPR JSON, format varies by version)
    ├── google.py                  # Chrome/YouTube/search history + Netscape bookmarks.html
    └── enrich.py                    # URL content fetch (trafilatura)
tests/                                # mirrors src/, one test module per ingest source
```

## Conventions

- Ingest parsers degrade gracefully: missing/renamed export files are skipped
  (`rglob` over known filenames), never a hard failure — GDPR export formats
  drift across platform versions.
- New ingest sources: add a `parse(export_dir) -> list[KnowledgeItem]` module
  under `ingest/`, wire it into `cli.py`'s `ingest` command, add a test module
  mirroring `test_ingest_instagram.py`.
- Raw personal export data belongs in `/data` (gitignored) or a NAS mount —
  never commit it.
