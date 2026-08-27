#!/usr/bin/env bash
#
# Nightly inbox processor for knowledge-cartography, run via launchd on the
# Mac Mini (see deploy/com.gustave.knowledge-cartography.plist and
# docs/MACMINI_SETUP.md). Watches NAS:/inbox/<source>/ for new exports,
# unzips them, ingests + re-clusters, then archives what was processed.
#
# Config comes from .env at the repo root (CARTOGRAPHY_* — see config.py).
# This script additionally reads CARTOGRAPHY_INBOX_DIR from that same file.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

# launchd gives a minimal PATH — make sure uv is findable regardless of how
# it was installed.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

LOG_DIR="$HOME/Library/Logs/cartography"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/inbox.log"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" | tee -a "$LOG_FILE"; }

if [ ! -f .env ]; then
    log "ERROR: no .env at $REPO_DIR — aborting"
    exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

INBOX_DIR="${CARTOGRAPHY_INBOX_DIR:-/Volumes/NAS/knowledge-cartography/inbox}"

if [ ! -d "$INBOX_DIR" ]; then
    log "WARN: inbox dir $INBOX_DIR not reachable (NAS not mounted?) — skipping run"
    exit 0
fi

PROCESSED_DIR="$INBOX_DIR/processed"
mkdir -p "$PROCESSED_DIR"

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

ARGS=()
MOVED=()

stage_zips() {
    # stage_zips <source>: unzip every new *.zip under inbox/<source>/ into a
    # shared staging folder and queue it as an ingest arg. Ingest parsers
    # rglob for known filenames, so merging multiple exports into one
    # staging dir is safe.
    local source="$1"
    local src_dir="$INBOX_DIR/$source"
    [ -d "$src_dir" ] || return 0

    local found=0
    local dest="$STAGE_DIR/$source"
    for zip in "$src_dir"/*.zip; do
        [ -e "$zip" ] || continue
        found=1
        mkdir -p "$dest"
        if ! unzip -oq "$zip" -d "$dest"; then
            log "ERROR: failed to unzip $zip — leaving in place, skipping"
            continue
        fi
        MOVED+=("$zip")
    done

    if [ "$found" = 1 ] && [ -d "$dest" ]; then
        ARGS+=("--$source" "$dest")
    fi
}

stage_zips instagram
stage_zips facebook
stage_zips google

# bookmarks: a single (unzipped) bookmarks.html, not a zip of a source dir.
BOOKMARKS_SRC="$INBOX_DIR/bookmarks"
if [ -d "$BOOKMARKS_SRC" ]; then
    BOOKMARKS_FILE=$(ls -t "$BOOKMARKS_SRC"/*.html 2>/dev/null | head -n1 || true)
    if [ -n "$BOOKMARKS_FILE" ]; then
        ARGS+=(--bookmarks "$BOOKMARKS_FILE")
        MOVED+=("$BOOKMARKS_FILE")
    fi
fi

if [ ${#ARGS[@]} -eq 0 ]; then
    log "INFO: no new exports in $INBOX_DIR — nothing to do"
    exit 0
fi

log "INFO: ingesting new exports (${ARGS[*]})"
if ! uv run cartography ingest "${ARGS[@]}" >>"$LOG_FILE" 2>&1; then
    log "ERROR: ingest failed — leaving inbox files in place for retry, not clustering"
    exit 1
fi

log "INFO: clustering + rendering map"
if ! uv run cartography cluster >>"$LOG_FILE" 2>&1; then
    log "ERROR: cluster step failed"
    exit 1
fi

TS="$(date '+%Y%m%d-%H%M%S')"
for f in "${MOVED[@]}"; do
    dest_dir="$PROCESSED_DIR/$(basename "$(dirname "$f")")"
    mkdir -p "$dest_dir"
    mv "$f" "$dest_dir/${TS}_$(basename "$f")"
done

log "INFO: run complete, archived ${#MOVED[@]} export file(s)"
