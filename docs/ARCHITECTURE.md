# Architecture de déploiement

Statut : implémenté et en usage. Ce document a démarré comme une proposition
avant tout code ; il documente maintenant l'infra perso réellement en place
(Mac Mini + NAS + Tailscale), y compris les écarts par rapport au plan
initial découverts en pratique (voir "NAS vs disque local" ci-dessous).

## Rôles de chaque machine

| Machine | Rôle |
|---|---|
| **NAS** | Stockage des exports bruts (`inbox/`) uniquement — pas la base vectorielle ni les cartes générées, voir ci-dessous. |
| **Mac Mini** | Nœud de calcul always-on : héberge Ollama (embeddings locaux), exécute le pipeline `cartography` sur un planning (launchd), stocke la base vectorielle et les cartes sur son propre disque. |
| **Tailscale** | Réseau privé qui relie NAS, Mac Mini, postes et téléphone sans exposer de port public. Utile surtout hors du LAN domestique (consulter la carte depuis l'extérieur, déclencher un run à distance). |
| **Poste de dev** | Pilotage / itération sur le code — pas dans la boucle d'exécution continue. |

## NAS vs disque local : ce qui a changé par rapport au plan initial

Le plan de départ mettait tout sur le NAS (`CARTOGRAPHY_CHROMA_DIR` /
`CARTOGRAPHY_OUTPUT_DIR` pointés dessus), le NAS étant la "source de vérité".
En pratique, sur un ingest Messenger de 245k+ items :

- ChromaDB fait des écritures/verrous internes fréquents (mécanisme proche
  d'un WAL) que le mount SMB gère mal — a fini par planter en pleine
  ingestion (`chromadb.errors.InternalError: Error in compaction: Error
  purging logs`).
- Le mount SMB lui-même s'est mis à bloquer au point de faire disparaître
  toute la machine du tailnet pendant plusieurs minutes.
- Même rapatrié sur disque local, ChromaDB a ensuite montré des erreurs de
  compaction intermittentes sous forte charge soutenue (même cause probable,
  pas encore isolée précisément) — `scripts/process_inbox.sh` et le run
  manuel retentent automatiquement avec `--resume` (idempotent) en cas
  d'échec plutôt que de tout perdre.

Résultat : `CARTOGRAPHY_CHROMA_DIR` et `CARTOGRAPHY_OUTPUT_DIR` pointent sur
le disque local du Mac Mini (`~/.cartography/{chroma,serve}`), pas le NAS.
Le NAS garde son rôle pour `inbox/` (exports bruts, écriture ponctuelle peu
fréquente — pas le même profil de charge). Voir
[docs/MACMINI_SETUP.md](MACMINI_SETUP.md) §4 pour le détail.

## Flux de données

```text
exports bruts → NAS:/inbox/<source>/
                     │
                     ▼ (launchd, Mac Mini, nocturne)
              détection + dézippage
                     │
                     ▼
         cartography ingest → cluster
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
 ~/.cartography/chroma    ~/.cartography/serve
 (vecteurs, local)        (carte HTML, local — servie directement,
                            voir "Consultation de la carte" ci-dessous)
```

**Consultation de la carte** : servie directement depuis le Mac Mini via
Tailscale Serve (`https://gustaves-mac-mini.tail877df4.ts.net/`) — voir la
case correspondante dans "Prochaines étapes" plus bas pour le détail
d'implémentation.

**Détection des nouveaux exports** : un dossier `NAS:/inbox/<source>/` où on
dépose les nouveaux exports zippés. Un job launchd sur le Mac Mini tourne par
exemple chaque nuit, dézippe ce qui est nouveau, lance `cartography ingest`,
déplace l'archive traitée vers `inbox/processed/`. Le dédoublonnage est déjà
géré nativement : les IDs sont des hash du contenu (`make_id`) et
`embed_items` fait un `upsert` — réingérer un export qui se chevauche avec le
précédent ne duplique rien.

## Intégrations — état actuel et proposées

| Source | Statut | Format réel observé | Récupération |
|---|---|---|---|
| Instagram (saved/liked) | ✅ fait | HTML (export GDPR) | Manuelle, demande périodique |
| Facebook (saved/pages suivies) | ✅ fait | HTML | Manuelle, demande périodique |
| Google Takeout (Chrome, YouTube, recherche) | Code prêt, jamais testé sur données réelles | JSON | Manuelle (Takeout) |
| Bookmarks navigateur | Code prêt | HTML Netscape | Export manuel ponctuel |
| Messages (Messenger) | ✅ fait, 100% local | `your_facebook_activity/messages/{inbox,archived_threads,filtered_threads}/*/message_*.json` | Manuelle (export GDPR), `--messenger <dir>` |
| WhatsApp / iMessage | ❌ absent | Export possible (WhatsApp : chat export .txt/.zip ; iMessage : `~/Library/Messages/chat.db` en local sur le Mac) | Optionnel, à évaluer |

Pour les messages : c'est la source la plus sensible (conversations privées,
souvent avec des tiers qui n'ont pas consenti à être indexés). À traiter
comme une intégration à part, opt-in, avec un traitement plus strict (voir
plus bas) plutôt que de la fondre dans le pipeline existant sans distinction.

## Politique d'ingestion continue, par source

Le facteur limitant : Meta n'offre pas d'API d'export automatisé pour un
usage perso (juste « Télécharger vos informations », manuel). L'ingestion
« continue » au sens strict n'est donc possible que pour ce qui vit en local
sur une machine qu'on contrôle.

| Source | Cadence réaliste | Mécanisme | Traitement |
|---|---|---|---|
| Historique Chrome (si le Mac Mini est la machine de nav principale) | Quotidien (cron local) | Lecture directe du `History` SQLite du profil Chrome — pas besoin d'attendre un Takeout | Local uniquement |
| Google Takeout (recherche, YouTube — pas dispo autrement) | Trimestriel | Manuel : dépose dans `NAS:/inbox`, le Mac Mini détecte et ingère | Standard |
| Instagram / Facebook | Trimestriel ou semestriel | Manuel (export GDPR), dépôt dans `inbox/` | Standard |
| Bookmarks | À la demande / quand ça a significativement changé | Manuel | Standard |
| Messages (Messenger/WhatsApp/iMessage) | Opt-in, cadence à part | Manuel ou lecture locale (iMessage) | Embeddings locaux uniquement (Ollama), jamais de labeling via l'API Claude sur du contenu brut de conversation |

## Point d'attention : labeling cloud vs contenu privé

Décision prise et implémentée : les messages Messenger (`ingest/messenger.py`)
sont **100% locaux**, structurellement, pas juste par convention :

- `embed.py` route tout item `source == MESSENGER` vers `OllamaEmbedder`,
  quel que soit `CARTOGRAPHY_EMBEDDING_PROVIDER` configuré globalement (utile
  si Vertex AI est activé pour le reste un jour).
- `label.py` exclut les items Messenger de l'échantillon envoyé à l'API
  Claude pour nommer un cluster — même dans un cluster mixte (messages +
  items publics), seul le texte non-Messenger part vers l'API. Un cluster
  100% Messenger n'appelle jamais l'API et retombe sur un label générique
  (`Cluster N`).

Le label des clusters passe par l'API Claude (`label.py`) — seul un
échantillon de texte par cluster est envoyé, pas les items bruts. Ce
comportement (Messenger = jamais envoyé à l'API, quel que soit le contexte)
ne doit pas changer implicitement.

## Prochaines étapes

- [x] Hardware en place : Mac Mini, switch, NAS installés et sous tension
- [x] Tailscale connecte le Mac Mini et le poste de dev (`gustaves-mac-mini` visible sur le tailnet)
- [x] Job launchd sur le Mac Mini (détection `inbox/` + ingest planifié) —
      installé et testé (`launchctl kickstart` → run OK), tourne nightly à
      3h. NAS monté sur `/Volumes/NAS-UGREEN`
      (`Projets/knowledge-cartography/inbox/`) pour les exports bruts ;
      base vectorielle et carte sur disque local, voir "NAS vs disque
      local" plus haut. Voir [docs/MACMINI_SETUP.md](MACMINI_SETUP.md)
- [x] Config Tailscale Serve/MagicDNS pour consulter la carte depuis le
      tailnet — la carte est servie sur
      `https://gustaves-mac-mini.tail877df4.ts.net/`. Le variant macOS de
      Tailscale (App Store, sandboxé) ne peut pas servir un dossier
      directement (`tailscale serve <dossier>` échoue avec "Path serving is
      not supported on macOS due to sandbox restrictions") ; on proxy donc
      vers un `python3 -m http.server` local (`com.gustave.knowledge-cartography-webserver`,
      port 8642, bind 127.0.0.1) qui sert `~/.cartography/serve/` —
      `CARTOGRAPHY_OUTPUT_DIR` pointe directement dessus (voir "NAS vs
      disque local" plus haut), donc `cartography cluster` y écrit la carte
      sans étape de mirroring séparée. Voir
      [docs/MACMINI_SETUP.md](MACMINI_SETUP.md).
- [x] Module `ingest/messenger.py` pour parser `your_facebook_activity/messages/`
      — un item par message texte (skip pièces jointes seules et messages
      supprimés), correctif du bug d'encodage mojibake de l'export Facebook.
      `cartography ingest --messenger <dir>`.
- [x] Décision explicite sur le traitement des messages : **100% local**.
      Voir "Point d'attention : labeling cloud vs contenu privé" ci-dessus.
