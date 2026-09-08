# Setup du Mac Mini

Runbook pour faire tourner `cartography` en continu sur le Mac Mini, une fois
qu'il est allumé en permanence, connecté au NAS et à Tailscale. Voir
[ARCHITECTURE.md](ARCHITECTURE.md) pour le contexte général.

## 0. Prérequis réseau

- Mac Mini et NAS sur le même LAN (ou joignables via Tailscale).
- Tailscale installé et connecté sur le Mac Mini (`tailscale status` doit
  lister la machine comme `online`).
- Remote Login (SSH) activé si le déploiement se fait à distance depuis le
  poste de dev : Réglages Système → Général → Partage → "Connexion à
  distance".

## 1. Installer les outils

```bash
# uv (gestionnaire de deps Python)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Ollama (embeddings locaux)
brew install ollama
brew services start ollama   # tourne en tâche de fond en permanence
ollama pull nomic-embed-text
```

## 2. Cloner le repo

```bash
mkdir -p ~/code && cd ~/code
git clone <url-du-repo> knowledge-cartography
cd knowledge-cartography
uv sync --extra local --group dev
```

## 3. Monter le NAS de façon persistante

Le NAS sert uniquement de dépôt pour les exports bruts (`inbox/`) — **pas**
pour la base vectorielle ni pour la carte générée, voir §4. Il faut que le
point de montage survive aux redémarrages/reconnexions, sans intervention
manuelle, puisque le job launchd tourne sans session utilisateur active en
avant-plan.

Le plus simple sur macOS : Finder → Cmd+K → `smb://<nom-ou-ip-nas>/<partage>`,
cocher "Se souvenir de ce mot de passe" (stocké dans le Keychain), puis
ajouter le volume monté aux éléments de connexion (Réglages Système →
Général → Ouverture → "+" → sélectionner le volume NAS) pour qu'il se
remonte à chaque connexion de session.

Vérifier le point de montage résultant (en général `/Volumes/<partage>`) :

```bash
ls /Volumes/
```

Créer la structure attendue sur le NAS :

```bash
NAS=/Volumes/<partage>/knowledge-cartography   # adapter le chemin
mkdir -p "$NAS"/inbox/{instagram,facebook,google,bookmarks,processed}
```

## 4. Configurer `.env`

Créer `~/code/knowledge-cartography/.env` (gitignored) sur le Mac Mini :

```bash
CARTOGRAPHY_CHROMA_DIR=/Users/<user>/.cartography/chroma
CARTOGRAPHY_OUTPUT_DIR=/Users/<user>/.cartography/serve
CARTOGRAPHY_INBOX_DIR=/Volumes/<partage>/knowledge-cartography/inbox
CARTOGRAPHY_ANTHROPIC_API_KEY=sk-ant-...
```

**`CHROMA_DIR` et `OUTPUT_DIR` vivent sur le disque local, pas le NAS** —
essayé d'abord sur le mount SMB, deux problèmes rencontrés en pratique :
ChromaDB fait des écritures/verrous fréquents (WAL-style) que SMB gère mal
et a fini par planter en pleine ingestion (`Error in compaction: Error
purging logs`) ; et le mount lui-même s'est mis à bloquer au point de faire
disparaître toute la machine du tailnet pendant plusieurs minutes. `OUTPUT_DIR`
pointant directement vers `~/.cartography/serve/` a aussi l'avantage
d'éliminer l'étape de mirroring séparée qu'il fallait sinon faire pour
`com.gustave.knowledge-cartography-webserver` (voir §7).

Même en local, ChromaDB a montré des erreurs de compaction intermittentes
sous forte charge (ingestion de 245k+ items d'un coup) — pas la peine de
chercher plus loin la cause exacte pour l'instant : `scripts/process_inbox.sh`
retente automatiquement `cartography ingest --resume` (idempotent, saute les
items déjà embeddés) jusqu'à 3 fois en cas d'échec.

`CARTOGRAPHY_INBOX_DIR` n'est pas lu par `config.py` (settings de l'app) —
c'est `scripts/process_inbox.sh` qui le lit directement dans ce même fichier,
pour rester cohérent avec la convention "tout se règle via `.env`".

## 5. Tester manuellement avant d'automatiser

Déposer un export zippé dans `inbox/<source>/`, puis :

```bash
cd ~/code/knowledge-cartography
bash scripts/process_inbox.sh
tail -f ~/Library/Logs/cartography/inbox.log
```

Vérifier que la carte est bien générée dans `CARTOGRAPHY_OUTPUT_DIR` et que
le zip a été déplacé vers `inbox/processed/<source>/`.

## 6. Installer le job launchd

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs/cartography
cp deploy/com.gustave.knowledge-cartography.plist ~/Library/LaunchAgents/
```

Le plist committé pointe déjà vers `/Users/gustavevernay/code/knowledge-cartography`
(la config actuelle sur `gustaves-mac-mini`). Sur une autre machine/compte,
éditer `~/Library/LaunchAgents/com.gustave.knowledge-cartography.plist` et
adapter les chemins (`whoami`, `pwd` dans le repo cloné).

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gustave.knowledge-cartography.plist
launchctl list | grep knowledge-cartography   # doit apparaître
```

Le job tourne toutes les nuits à 3h. Pour déclencher un run immédiat sans
attendre :

```bash
launchctl kickstart -k gui/$(id -u)/com.gustave.knowledge-cartography
tail -f ~/Library/Logs/cartography/inbox.log
```

## 7. Consulter la carte depuis le tailnet (Tailscale Serve)

Le variant macOS de Tailscale (App Store, sandboxé) ne peut pas servir un
dossier directement — `tailscale serve <dossier>` échoue avec "Path serving
is not supported on macOS due to sandbox restrictions". Solution : un petit
serveur HTTP local, que Tailscale proxifie.

`CARTOGRAPHY_OUTPUT_DIR` pointant déjà vers `~/.cartography/serve/` (§4),
`cartography cluster` y écrit la carte directement — pas d'étape de
mirroring séparée à faire. Il reste à installer le serveur et le proxy :

```bash
mkdir -p ~/.cartography/serve
cp ~/code/knowledge-cartography/deploy/com.gustave.knowledge-cartography-webserver.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gustave.knowledge-cartography-webserver.plist
```

Puis, une fois (le réglage est persistant, pas besoin de le refaire à chaque
redémarrage) — nécessite que "Serve" soit activé sur le tailnet dans la
console d'admin Tailscale (un lien s'affiche si ce n'est pas encore fait) :

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8642
```

La carte est alors accessible sur tout le tailnet (téléphone inclus) à
`https://gustaves-mac-mini.tail877df4.ts.net/`. Le premier chargement peut
être lent (provisioning du certificat HTTPS), les suivants sont instantanés.

## 8. Désinstaller / mettre à jour

```bash
launchctl bootout gui/$(id -u)/com.gustave.knowledge-cartography
launchctl bootout gui/$(id -u)/com.gustave.knowledge-cartography-webserver
# puis re-copier le(s) plist modifié(s) et refaire `launchctl bootstrap`
```
