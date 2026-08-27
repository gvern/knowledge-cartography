# Architecture de déploiement

Statut : proposition, rien n'est implémenté. Ce document capture la conception
discutée pour faire tourner `cartography` en continu sur une infra perso
(Mac Mini + NAS + Tailscale), avant de coder quoi que ce soit.

## Rôles de chaque machine

| Machine | Rôle |
|---|---|
| **NAS** | Stockage durable : exports bruts (`inbox/`), base vectorielle ChromaDB, cartes HTML générées. Source de vérité — rien d'important ne doit vivre uniquement sur le Mac Mini ou un poste de dev. |
| **Mac Mini** | Nœud de calcul always-on : héberge Ollama (embeddings locaux), exécute le pipeline `cartography` sur un planning (launchd), monte le NAS en lecture/écriture. |
| **Tailscale** | Réseau privé qui relie NAS, Mac Mini, postes et téléphone sans exposer de port public. Utile surtout hors du LAN domestique (consulter la carte depuis l'extérieur, déclencher un run à distance). |
| **Poste de dev** | Pilotage / itération sur le code — pas dans la boucle d'exécution continue. |

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
NAS:/chroma (vecteurs)   NAS:/output (carte HTML)
```

Correspond à ce que `config.py` anticipe déjà (`CARTOGRAPHY_CHROMA_DIR` /
`CARTOGRAPHY_OUTPUT_DIR` pointables vers un mount NAS) — pas de changement de
code nécessaire pour ça, juste de la config d'environnement sur le Mac Mini.

**Consultation de la carte** : soit monter le NAS en SMB sur les autres
devices et ouvrir le HTML localement, soit (préférable) servir `output/` via
un petit serveur web sur le NAS et y accéder par le nom Tailscale
(MagicDNS) — évite de dépendre du montage réseau juste pour regarder la carte
depuis le téléphone.

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
| Messages (Messenger) | ❌ absent | Le zip Facebook contient déjà `your_facebook_activity/messages/...` (threads, photos, gifs) | À construire |
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

Le label des clusters passe par l'API Claude (`label.py`) — seul un
échantillon de texte par cluster est envoyé, pas les items bruts. Mais si des
messages privés entrent un jour dans le pipeline, il faut décider
explicitement s'ils participent au labeling cloud ou restent 100% local
(embeddings Ollama uniquement, labeling désactivé ou fait localement pour ces
clusters). Ce comportement ne doit pas changer implicitement.

## Prochaines étapes

- [x] Hardware en place : Mac Mini, switch, NAS installés et sous tension
- [x] Tailscale connecte le Mac Mini et le poste de dev (`gustaves-mac-mini` visible sur le tailnet)
- [x] Job launchd sur le Mac Mini (détection `inbox/` + ingest planifié) —
      installé et testé (`launchctl kickstart` → run OK), tourne nightly à
      3h. NAS monté sur `/Volumes/NAS-UGREEN`, données sous
      `Projets/knowledge-cartography/{inbox,chroma,output}`. Voir
      [docs/MACMINI_SETUP.md](MACMINI_SETUP.md)
- [x] Config Tailscale Serve/MagicDNS pour consulter la carte depuis le
      tailnet — la carte est servie sur
      `https://gustaves-mac-mini.tail877df4.ts.net/`. Le variant macOS de
      Tailscale (App Store, sandboxé) ne peut pas servir un dossier
      directement (`tailscale serve <dossier>` échoue avec "Path serving is
      not supported on macOS due to sandbox restrictions") ; on proxy donc
      vers un `python3 -m http.server` local (`com.gustave.knowledge-cartography-webserver`,
      port 8642, bind 127.0.0.1) qui sert un miroir local
      (`~/.cartography/serve/`) plutôt que le mount NAS directement — les
      LaunchAgents obtiennent un 404 en lisant le mount SMB (alors que la
      même commande marche en shell interactif, restriction sandbox macOS
      propre aux agents en arrière-plan sur volumes réseau).
      `scripts/process_inbox.sh` resynchronise ce miroir après chaque
      `cartography cluster`. Voir [docs/MACMINI_SETUP.md](MACMINI_SETUP.md).
- [ ] Module `ingest/messenger.py` pour parser `your_facebook_activity/messages/`
- [ ] Décision explicite sur le traitement des messages (labeling cloud ou non)
