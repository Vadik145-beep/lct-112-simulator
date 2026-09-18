#!/bin/sh
# Backup service of the trainer.
#  * Daily pg_dump at BACKUP_TIME (HH:MM, local TZ), keeps the last BACKUP_KEEP dumps in /backups.
#  * Manual copies: the API drops <BACKUP_DIR>/requests/<id>.request; this loop answers with
#    <id>.done (the file name) or <id>.failed (the error) within POLL_SECONDS.
#  * The administrator's schedule (<BACKUP_DIR>/.schedule with BACKUP_TIME / BACKUP_KEEP) is
#    read on every loop, so a change in the cabinet applies without a restart.
#  * Touches /tmp/backup-alive (container healthcheck) and <BACKUP_DIR>/.alive (the API's tile).
set -eu

BACKUP_TIME="${BACKUP_TIME:-03:00}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
POLL_SECONDS="${POLL_SECONDS:-5}"
REQUESTS_DIR="$BACKUP_DIR/requests"
mkdir -p "$BACKUP_DIR" "$REQUESTS_DIR"

# Prints the dump file name on success, exits non-zero on failure.
make_backup() {
  suffix="${1:-}"
  stamp=$(date +%Y%m%d-%H%M%S)
  file="$BACKUP_DIR/trainer-$stamp$suffix.dump"
  if pg_dump --format=custom --file="$file.part" "$PGDATABASE" 2>"$file.err"; then
    mv "$file.part" "$file"
    rm -f "$file.err"
    echo "backup written: $file" >&2
    rotate
    basename "$file"
    return 0
  fi
  echo "backup FAILED at $stamp: $(cat "$file.err")" >&2
  rm -f "$file.part"
  return 1
}

# Removes everything beyond the newest BACKUP_KEEP dumps.
rotate() {
  ls -1t "$BACKUP_DIR"/trainer-*.dump 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | while read -r old; do
    rm -f "$old"
    echo "backup removed: $old" >&2
  done
}

read_schedule() {
  if [ -f "$BACKUP_DIR/.schedule" ]; then
    # Only the two known keys are taken from the file.
    t=$(sed -n 's/^BACKUP_TIME=\([0-2][0-9]:[0-5][0-9]\)$/\1/p' "$BACKUP_DIR/.schedule" | head -n 1)
    k=$(sed -n 's/^BACKUP_KEEP=\([0-9]\{1,3\}\)$/\1/p' "$BACKUP_DIR/.schedule" | head -n 1)
    [ -n "$t" ] && BACKUP_TIME="$t"
    [ -n "$k" ] && BACKUP_KEEP="$k"
  fi
}

serve_requests() {
  for request in "$REQUESTS_DIR"/*.request; do
    [ -f "$request" ] || continue
    id=$(basename "$request" .request)
    mv "$request" "$REQUESTS_DIR/$id.running"
    if name=$(make_backup "-manual"); then
      echo "$name" >"$REQUESTS_DIR/$id.done"
    else
      err_file=$(ls -1t "$BACKUP_DIR"/trainer-*.err 2>/dev/null | head -n 1)
      { [ -n "$err_file" ] && cat "$err_file"; } >"$REQUESTS_DIR/$id.failed" 2>/dev/null || echo "pg_dump failed" >"$REQUESTS_DIR/$id.failed"
      rm -f "$BACKUP_DIR"/trainer-*.err
    fi
    rm -f "$REQUESTS_DIR/$id.running"
  done
}

# One-off mode for scripts and tests.
if [ "${1:-}" = "now" ]; then
  read_schedule
  make_backup "${2:-}"
  exit 0
fi

read_schedule
echo "backup service: daily at $BACKUP_TIME, keeping $BACKUP_KEEP copies in $BACKUP_DIR"
last_day=""
tick=0
while true; do
  touch /tmp/backup-alive "$BACKUP_DIR/.alive"
  read_schedule
  serve_requests
  now=$(date +%H:%M)
  today=$(date +%Y-%m-%d)
  if [ "$now" = "$BACKUP_TIME" ] && [ "$last_day" != "$today" ]; then
    last_day="$today"
    make_backup >/dev/null || true
  fi
  tick=$((tick + 1))
  sleep "$POLL_SECONDS"
done
