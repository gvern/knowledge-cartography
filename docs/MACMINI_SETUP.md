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

Il faut que le point de montage survive aux redémarrages/reconnexions, sans
intervention manuelle, puisque le job launchd tourne sans session utilisateur
active en avant-plan.

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
mkdir -p "$NAS"/chroma "$NAS"/output
```

## 4. Configurer `.env`

Créer `~/code/knowledge-cartography/.env` (gitignored) sur le Mac Mini :

```bash
CARTOGRAPHY_CHROMA_DIR=/Volumes/<partage>/knowledge-cartography/chroma
CARTOGRAPHY_OUTPUT_DIR=/Volumes/<partage>/knowledge-cartography/output
CARTOGRAPHY_INBOX_DIR=/Volumes/<partage>/knowledge-cartography/inbox
CARTOGRAPHY_ANTHROPIC_API_KEY=sk-ant-...
```

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
is not supported on macOS due to sandbox restrictions". Et les LaunchAgents
n'arrivent pas à lire le mount SMB du NAS (404, alors que la même commande
marche en shell interactif — restriction sandbox propre aux agents en
arrière-plan sur volumes réseau). Solution : un petit serveur HTTP local qui
sert un miroir local de la carte, que Tailscale proxifie.

`scripts/process_inbox.sh` resynchronise déjà `CARTOGRAPHY_OUTPUT_DIR` (NAS)
vers `~/.cartography/serve/` (disque local) après chaque `cartography
cluster`. Il reste à installer le serveur et le proxy :

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
