#!/bin/bash
# scripts/backup.sh — snapshot the diary data dir to /volume2
#
# Run via DSM Task Scheduler (daily) or manually:
#   bash /volume1/docker/diary/scripts/backup.sh
#
# Env vars (with defaults):
#   DATA_DIR    — source data dir on volume1   (default /volume1/docker/diary/data)
#   BACKUP_DIR  — target backup dir on volume2 (default /volume2/docker-backups/diary)
#   KEEP        — how many recent backups to keep (default 30)
set -euo pipefail

DATA_DIR="${DATA_DIR:-/volume1/docker/diary/data}"
BACKUP_DIR="${BACKUP_DIR:-/volume2/docker-backups/diary}"
KEEP="${KEEP:-30}"
DATE="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/diary-${DATE}.tar.gz"

if [[ ! -d "$DATA_DIR" ]]; then
  echo "ERROR: data dir not found: $DATA_DIR" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

# Include SQLite + WAL sidecars and any future files in the data dir.
echo "Backing up $DATA_DIR → $BACKUP_FILE"
tar -czf "$BACKUP_FILE" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"

# Rotate: keep the most recent $KEEP, delete the rest.
cd "$BACKUP_DIR"
deleted=0
if compgen -G "diary-*.tar.gz" > /dev/null; then
  while IFS= read -r old; do
    rm -f -- "$old"
    deleted=$((deleted + 1))
  done < <(ls -1t diary-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)))
fi

echo "Done. Kept last $KEEP backup(s); pruned $deleted older."
ls -lh "$BACKUP_FILE"