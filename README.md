# knowledge-cartography

Construit une carte 3D interactive de ton graphe de connaissances personnel à
partir de tes exports de réseaux sociaux (posts sauvegardés/likés, pages
suivies) et de ton historique de navigation (Chrome, YouTube, recherches
Google, favoris). Chaque élément est transformé en embedding, projeté en 3D
(UMAP), regroupé par sujet (HDBSCAN), et étiqueté automatiquement via l'API
Claude.

## Structure

```
src/cartography/
├── cli.py          # entrypoint click (ingest/cluster/stats)
├── config.py        # chemins, provider d'embeddings, paramètres UMAP/HDBSCAN
├── schema.py         # KnowledgeItem + ClusteredItem (Pydantic)
├── embed.py           # Ollama ou Vertex AI -> ChromaDB
├── cluster.py          # UMAP + HDBSCAN
├── label.py              # nommage automatique des clusters via l'API Claude
├── viz.py                 # carte HTML interactive (Plotly)
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
# configuré — voir docs/ARCHITECTURE.md
cartography ingest --messenger ~/data/facebook

# Clustering + génération de la carte
cartography cluster
open ./output/knowledge_map.html

# Statistiques sur la base vectorielle
cartography stats
```

Les exports Instagram/Facebook s'obtiennent via "Télécharger vos
informations" (format JSON), et l'export Google via
[Google Takeout](https://takeout.google.com/) (Chrome, YouTube, recherche).
Le format de l'export Facebook varie selon les versions ; `ingest/facebook.py`
essaie plusieurs noms de fichiers connus et ignore silencieusement ceux qui
manquent.

## Architecture de déploiement

Pour faire tourner le pipeline en continu sur une infra perso (Mac Mini +
NAS + Tailscale), voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
