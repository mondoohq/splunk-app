#!/usr/bin/env bash
# sync.sh – Push both apps to a remote Splunk instance for dev testing.
#
# Usage:
#   SPLUNK_HOST=splunk.dev.example.com ./sync.sh
#   SPLUNK_HOST=user@host ./sync.sh                       # via SSH
#   SPLUNK_HOST=host SPLUNK_APPS_DIR=/opt/splunk/etc/apps ./sync.sh
#
# After sync, restart Splunk:
#   ssh "$SPLUNK_HOST" sudo /opt/splunk/bin/splunk restart
#
# Set DRY_RUN=1 to preview what would be transferred.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST="${SPLUNK_HOST:-}"
APPS_DIR="${SPLUNK_APPS_DIR:-/opt/splunk/etc/apps}"
DRY_RUN="${DRY_RUN:-0}"
APPS=("TA-mondoo" "mondoo_app")

if [ -z "$HOST" ]; then
    echo "ERROR: SPLUNK_HOST is not set." >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  SPLUNK_HOST=splunk.dev.example.com $0" >&2
    exit 1
fi

RSYNC_FLAGS=(-az --delete
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='.venv/'
    --exclude='tests/'
    --exclude='.git/'
    --exclude='._*'
    --exclude='.DS_Store'
    --exclude='local/'
    --exclude='local.meta'
)

if [ "$DRY_RUN" = "1" ]; then
    RSYNC_FLAGS+=(--dry-run --itemize-changes)
    echo "[dry-run] Showing what would be transferred."
fi

for APP in "${APPS[@]}"; do
    SRC="$SCRIPT_DIR/$APP/"
    DEST="$HOST:$APPS_DIR/$APP/"
    echo ""
    echo "──> rsync $SRC → $DEST"
    rsync "${RSYNC_FLAGS[@]}" "$SRC" "$DEST"
done

echo ""
echo "Done. Restart Splunk to pick up changes:"
echo "  ssh $HOST sudo /opt/splunk/bin/splunk restart"
