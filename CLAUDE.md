# knowledge-cartography

Builds an interactive 3D map of a personal knowledge graph from social media
exports (Instagram, Facebook, Messenger) and browsing history (Google
Takeout, HTML bookmarks). Pipeline: ingest → embed (Ollama or Vertex AI) →
UMAP → HDBSCAN → label clusters via the Anthropic API → render an orbitable
3D HTML map (Plotly `Scatter3d`, with constellation lines between
topically-adjacent clusters), and/or export the same clustered knowledge as
a structured JSON graph and an Obsidian-style Markdown vault, and/or serve it
live over MCP — three ways for a second brain / agent to consume it: static
file, or a queryable tool in conversation.

## Commands

```bash
uv sync --extra local --group dev   # deps + ruff/pytest/mypy
uv run pytest                        # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy src/cartography
uv run cartography ingest --instagram <dir> --facebook <dir> --google <dir> --messenger <dir> --bookmarks <file>
uv run cartography cluster
uv run cartography export --format both   # knowledge_graph.json + notes/ Markdown vault
uv run cartography search "<query>"       # semantic search over the vector store
uv run cartography stats
uv run cartography mcp                    # MCP server over stdio (needs `uv sync --extra mcp`)
```

CI (`.github/workflows/ci.yml`) runs all four checks above on push/PR to
`main` across Python 3.10 and 3.12 — keep it green before merging.

## Config

All settings are env vars prefixed `CARTOGRAPHY_` (see `src/cartography/config.py`),
loaded from a gitignored `.env` at the repo root. Key ones: `CARTOGRAPHY_ANTHROPIC_API_KEY`
(cluster labeling), `CARTOGRAPHY_CHROMA_DIR` / `CARTOGRAPHY_OUTPUT_DIR` (point these at a
NAS mount to persist outside local disk — reachable over Tailscale in this setup).

Local embeddings require `ollama serve` running with the `nomic-embed-text` model pulled.

`CARTOGRAPHY_OLLAMA_CHAT_MODEL` (default `llama3.1:8b`, must already be pulled)
names a local chat model used to label clusters `label.py`'s Anthropic path
can't safely see — chiefly Messenger-only clusters, where raw TF-IDF keywords
("Https / Www / Soirée") were the only prior fallback and read as noise, not a
topic. Empty string disables it (straight to keyword labels). See the
Messenger policy bullet below for why this is the right place for that model,
not the Anthropic one.

## Structure

```
src/cartography/
├── cli.py            # click entrypoint (ingest/cluster/stats)
├── config.py          # env-driven Settings (pydantic-settings)
├── schema.py           # KnowledgeItem / ClusteredItem (pydantic)
├── embed.py             # Ollama or Vertex AI -> ChromaDB
├── cluster.py             # UMAP + HDBSCAN
├── label.py                 # cluster naming: Anthropic -> local Ollama chat -> keyword_tags()
├── viz.py                    # Plotly 3D HTML map (Scatter3d + constellation edges)
├── export.py                  # JSON graph (nodes/edges/clusters) + Markdown vault
├── mcp_server.py               # MCP server: live tool access to the graph for an agent
└── ingest/
    ├── instagram.py           # saved/liked posts (GDPR JSON export)
    ├── facebook.py              # saved items + followed pages (GDPR JSON, format varies by version)
    ├── messenger.py               # Messenger threads (opt-in, 100% local — never cloud-labeled)
    ├── google.py                    # Chrome/YouTube/search history + Netscape bookmarks.html
    └── enrich.py                      # URL content fetch (trafilatura)
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
- `SourcePlatform.MESSENGER` items are private-by-policy: `embed.py` always
  routes them through Ollama regardless of the configured embedding
  provider, and `label.py` never includes their text in a cluster-labeling
  API call, even in a cluster mixed with non-Messenger items. Labeling still
  wants their text, though — `label.py`'s `LocalLabeler` covers that with a
  local Ollama *chat* model (`CARTOGRAPHY_OLLAMA_CHAT_MODEL`, separate from
  the embedding model), the one path allowed to see it, before falling back
  to keyword extraction. If you add another sensitive source (e.g.
  iMessage/WhatsApp), give it the same treatment rather than a one-off — see
  docs/ARCHITECTURE.md.
- `export.py`'s JSON graph and Markdown vault stay purely local file writes
  (like the HTML map already is) — they carry the same per-item text that's
  already embedded in the map's page data, just reshaped, so no additional
  Messenger restriction applies there. If `export.py` ever grows a mode that
  calls a network API, apply the same Messenger exclusion as `label.py`.
- `mcp_server.py` is a network-facing surface the same way `label.py` is —
  its tool results are read by whatever LLM is on the other end of the MCP
  client, often a cloud model. Every tool there excludes
  `SourcePlatform.MESSENGER` items unconditionally (`_visible_items`), same
  policy, same reasoning. Give any future sensitive source the same
  treatment there too, not a one-off.
