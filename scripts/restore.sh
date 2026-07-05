#!/bin/bash
# scripts/restore.sh — restore a backup tarball into the data dir
#
# Usage:
#   bash /volume1/docker/diary/scripts/restore.sh /volume2/docker-backups/diary/diary-20260705-120000.tar.gz
#
# Stops the running container, replaces the data dir, then restarts.
set -euo pipefail

BACKUP_FILE="${1:?usage: restore.sh <backup-file.tar.gz>}"
DATA_DIR="${DATA_DIR:-/volume1/docker/diary/data}"
PROJECT_DIR="${PROJECT_DIR:-/volume1/docker/diary}"

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: backup file not found: $BACKUP_FILE" >&2
  exit 1
fi
if [[ ! -d "$DATA_DIR" ]]; then
  echo "ERROR: data dir not found: $DATA_DIR" >&2
  exit 1
fi

echo "About to restore:"
echo "  from: $BACKUP_FILE"
echo "  to:   $DATA_DIR"
echo "This will REPLACE all current diary data."
read -rp "Type 'yes' to continue: " confirm
if [[ "$confirm" != "yes" ]]; then
  echo "Aborted."
  exit 1
fi

echo "Stopping container..."
cd "$PROJECT_DIR"
docker compose stop diary || true

echo "Restoring..."
rm -rf "$DATA_DIR"
mkdir -p "$DATA_DIR"
tar -xzf "$BACKUP_FILE" -C "$(dirname "$DATA_DIR")"

echo "Starting container..."
docker compose start diary
sleep 2
docker compose ps
echo "Done. Container should be back up with restored data."