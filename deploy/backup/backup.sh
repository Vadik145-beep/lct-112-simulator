#!/bin/sh
# Daily pg_dump at BACKUP_TIME (HH:MM, local TZ), keeps the last BACKUP_KEEP files in /backups.
# Touches /tmp/backup-alive every minute for the container healthcheck.
set -eu

BACKUP_TIME="${BACKUP_TIME:-03:00}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
mkdir -p "$BACKUP_DIR"

make_backup() {
  stamp=$(date +%Y%m%d-%H%M%S)
  file="$BACKUP_DIR/trainer-$stamp.dump"
  if pg_dump --format=custom --file="$file.part" "$PGDATABASE"; then
    mv "$file.part" "$file"
    echo "backup written: $file"
  else
    echo "backup FAILED at $stamp" >&2
    rm -f "$file.part"
  fi
  # Remove everything beyond the newest BACKUP_KEEP dumps.
  ls -1t "$BACKUP_DIR"/trainer-*.dump 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | while read -r old; do
    rm -f "$old"
    echo "backup removed: $old"
  done
}

# One-off mode for scripts and tests.
if [ "${1:-}" = "now" ]; then
  make_backup
  exit 0
fi

echo "backup service: daily at $BACKUP_TIME, keeping $BACKUP_KEEP copies in $BACKUP_DIR"
last_day=""
while true; do
  touch /tmp/backup-alive
  now=$(date +%H:%M)
  today=$(date +%Y-%m-%d)
  if [ "$now" = "$BACKUP_TIME" ] && [ "$last_day" != "$today" ]; then
    last_day="$today"
    make_backup
  fi
  sleep 60
done
