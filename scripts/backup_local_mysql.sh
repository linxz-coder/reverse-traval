#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${MYSQL_DATABASE:-reverse_travel_archive}"
DB_HOST="${MYSQL_HOST:-127.0.0.1}"
DB_PORT="${MYSQL_PORT:-3306}"
DB_USER="${MYSQL_USER:-root}"
BACKUP_DIR="${REVERSE_TRAVEL_DB_BACKUP_DIR:-$HOME/reverse_travel_mysql_backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$BACKUP_DIR/${DB_NAME}_${STAMP}.sql"

mkdir -p "$BACKUP_DIR"

if [[ -n "${MYSQL_PASSWORD:-}" ]]; then
  export MYSQL_PWD="$MYSQL_PASSWORD"
fi

mysqldump \
  --host="$DB_HOST" \
  --port="$DB_PORT" \
  --user="$DB_USER" \
  --single-transaction \
  --routines \
  --triggers \
  --default-character-set=utf8mb4 \
  "$DB_NAME" > "$OUT_FILE"

gzip "$OUT_FILE"
echo "Backup written: ${OUT_FILE}.gz"
