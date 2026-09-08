# knowledge-cartography

Construit une carte 3D interactive de ton graphe de connaissances personnel à
partir de tes exports de réseaux sociaux (posts sauvegardés/likés, pages
suivies) et de ton historique de navigation (Chrome, YouTube, recherches
Google, favoris). Chaque élément est transformé en embedding, projeté en 3D
(UMAP), regroupé par sujet (HDBSCAN), et étiqueté automatiquement via l'API
Claude (label court + mots-clés). La carte HTML se navigue à la souris —
orbite, zoom — et relie les clusters proches par des lignes de constellation
pour donner une vraie lecture spatiale du graphe, pas juste un nuage de
points.

Pour le second cerveau (agent, RAG), trois façons d'exploiter la même
connaissance sans passer par un navigateur : export JSON structuré, vault
Markdown façon Obsidian, ou un serveur MCP qui expose le graphe comme des
outils interrogeables en direct dans une conversation.

## Structure

```text
src/cartography/
├── cli.py          # entrypoint click (ingest/cluster/stats)
├── config.py        # chemins, provider d'embeddings, paramètres UMAP/HDBSCAN
├── schema.py         # KnowledgeItem + ClusteredItem (Pydantic)
├── embed.py           # Ollama ou Vertex AI -> ChromaDB
├── cluster.py          # UMAP + HDBSCAN
├── label.py              # nommage des clusters : API Claude -> LLM local Ollama -> mots-clés
├── viz.py                 # carte HTML 3D interactive (Plotly Scatter3d + constellation)
├── export.py               # graphe JSON (nodes/edges/clusters) + vault Markdown
├── mcp_server.py            # serveur MCP : accès en direct au graphe pour un agent
└── ingest/
    ├── instagram.py        # posts sauvegardés/likés (export GDPR JSON)
    ├── facebook.py           # éléments sauvegardés + pages suivies
    ├── messenger.py            # messages Messenger (opt-in, 100% local)
    ├── google.py                 # historique Chrome/YouTube/recherche + favoris
    └── enrich.py                   # récupération du contenu des URLs (trafilatura)
```

## Installation

```bash
uv sync --extra local      # embeddings locaux via Ollama
# ou
uv sync --extra vertex     # embeddings via Vertex AI

ollama pull nomic-embed-text
export CARTOGRAPHY_ANTHROPIC_API_KEY="sk-ant-..."
```

## Configuration

Toutes les options se règlent via variables d'environnement préfixées
`CARTOGRAPHY_` (ou un fichier `.env`) — voir `src/cartography/config.py` pour
la liste complète. Pointe `CARTOGRAPHY_CHROMA_DIR` et `CARTOGRAPHY_OUTPUT_DIR`
vers un point de montage NAS pour que la base vectorielle et les cartes
persistent en dehors du disque local.

## Utilisation

```bash
# Ingestion de tes exports
cartography ingest \
  --instagram ~/data/instagram \
  --facebook ~/data/facebook \
  --google ~/data/takeout \
  --bookmarks ~/data/bookmarks.html

# Messenger (optionnel) : contenu privé, jamais envoyé à l'API Claude pour le
# labeling, toujours embeddé localement via Ollama quel que soit le provider
# configuré — voir docs/ARCHITECTURE.md. Le nommage des clusters Messenger
# passe par un modèle de chat Ollama LOCAL (CARTOGRAPHY_OLLAMA_CHAT_MODEL,
# ex. llama3.1:8b) plutôt qu'un simple comptage de mots-clés — bien plus
# lisible sur des messages, tout en restant 100% local.
cartography ingest --messenger ~/data/facebook

# Clustering + génération de la carte
cartography cluster
open ./output/knowledge_map.html

# Export pour un second cerveau : graphe JSON (nodes/edges/clusters) +
# vault Markdown façon Obsidian (notes/, avec wikilinks cluster <-> collection)
cartography export --format both
open ./output/notes/index.md

# Recherche sémantique en ligne de commande sur la base vectorielle
cartography search "recette de pâtes"

# Statistiques sur la base vectorielle
cartography stats
```

### Second cerveau en direct (MCP)

```bash
uv sync --extra mcp
claude mcp add cartography -- uv run --directory /chemin/vers/knowledge-cartography cartography mcp
```

Expose le graphe comme outils qu'un agent peut appeler en conversation :
`search_knowledge`, `list_clusters`, `get_cluster`, `list_collections`,
`get_collection_items`, `stats`. Comme pour le labeling, le contenu
Messenger est exclu de tout ce que ces outils renvoient — voir la
convention dans `CLAUDE.md`.

Les exports Instagram/Facebook s'obtiennent via "Télécharger vos
informations" (format JSON), et l'export Google via
[Google Takeout](https://takeout.google.com/) (Chrome, YouTube, recherche).
Le format de l'export Facebook varie selon les versions ; `ingest/facebook.py`
essaie plusieurs noms de fichiers connus et ignore silencieusement ceux qui
manquent.

## Architecture de déploiement

Pour faire tourner le pipeline en continu sur une infra perso (Mac Mini +
NAS + Tailscale), voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
