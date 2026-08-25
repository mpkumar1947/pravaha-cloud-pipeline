#!/bin/bash
# Pravaha Upload Script
# Can be called manually: ./pravaha-upload.sh "/path/to/file" "category"
# Or as a post-import custom script by your media manager.
#
# This script uses rclone move to stream files directly to cloud storage.
# The rclone VFS mount handles imports natively now, so this script
# is kept as a backup/manual upload tool for exceptional cases.

set -euo pipefail

RCLONE="/usr/bin/rclone"
RCLONE_CONFIG="/root/.config/rclone/rclone.conf"
LOG="/var/log/pravaha-upload.log"

TORRENT_PATH="${1:-}"
CATEGORY="${2:-}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"
    echo "$*"
}

if [ -z "$TORRENT_PATH" ]; then
    log "ERROR: No path provided"
    exit 1
fi

TORRENT_NAME=$(basename "$TORRENT_PATH")

# Determine destination based on category
case "$CATEGORY" in
    tv)
        DEST="pravaha-union:series"
        ;;
    movies)
        DEST="pravaha-union:movies"
        ;;
    anime)
        DEST="pravaha-union:anime"
        ;;
    *)
        DEST="pravaha-union:unsorted"
        ;;
esac

log "Uploading: $TORRENT_NAME → $DEST"
log "Starting rclone move..."

# rclone move: streams local file directly to cloud — no second local copy
"$RCLONE" move "$TORRENT_PATH" "$DEST/" \
    --config "$RCLONE_CONFIG" \
    --transfers=1 \
    --checkers=4 \
    --drive-chunk-size=128M \
    --tpslimit=4 \
    --tpslimit-burst=8 \
    --disable-http2 \
    --retries=5 \
    --retries-sleep=60s \
    --low-level-retries=10 \
    --stats=60s \
    --log-file="$LOG" \
    --log-level=INFO \
    2>&1

EXIT_CODE=$?

if [ "$EXIT_CODE" -eq 0 ]; then
    log "SUCCESS: '$TORRENT_NAME' uploaded to $DEST"
    touch "$TORRENT_PATH.imported" 2>/dev/null || true
else
    log "ERROR: rclone exited with code $EXIT_CODE for '$TORRENT_NAME'"
    log "File remains at $TORRENT_PATH for manual retry."
    exit $EXIT_CODE
fi

exit 0
