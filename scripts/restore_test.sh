#!/usr/bin/env bash
# Restores the newest backup into a separate database and checks that the trainer can log in
# there (PRD 14: «бэкап восстанавливается скриптом»). Needs the compose stack up.
#
#   scripts/restore_test.sh [-p <compose project>] [--keep]
#
# What it does: asks the backup service for a fresh dump (`backup.sh now`), restores it with
# pg_restore into `trainer_restore_test`, runs the application's login check against that
# database and drops it (unless --keep).
set -euo pipefail

PROJECT_ARGS=()
KEEP=false
while [ $# -gt 0 ]; do
  case "$1" in
    -p) PROJECT_ARGS=(-p "$2"); shift 2 ;;
    --keep) KEEP=true; shift ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Passwords of the stack for the login check (the compose services read the same file).
if [ -f .env ]; then set -a; . ./.env; set +a; fi
DC=(docker compose "${PROJECT_ARGS[@]}")
RESTORE_DB="trainer_restore_test"

step() { echo; echo "=== $*"; }

step "Свежая копия через службу backup"
DUMP=$("${DC[@]}" exec -T backup /usr/local/bin/backup.sh now -restore-test | tail -n 1 | tr -d '\r')
[ -n "$DUMP" ] || { echo "Служба backup не вернула имя файла" >&2; exit 1; }
echo "Файл: $DUMP"

step "Восстановление в отдельную базу $RESTORE_DB"
"${DC[@]}" exec -T backup sh -c "
  set -e
  psql -v ON_ERROR_STOP=1 -d postgres -c 'DROP DATABASE IF EXISTS $RESTORE_DB'
  psql -v ON_ERROR_STOP=1 -d postgres -c 'CREATE DATABASE $RESTORE_DB'
  pg_restore --no-owner --role=\"\$PGUSER\" -d $RESTORE_DB /backups/$DUMP
  psql -v ON_ERROR_STOP=1 -d $RESTORE_DB -c 'GRANT CONNECT ON DATABASE $RESTORE_DB TO trainer_app'
  psql -v ON_ERROR_STOP=1 -d $RESTORE_DB -c 'GRANT USAGE ON SCHEMA public TO trainer_app'
  psql -v ON_ERROR_STOP=1 -d $RESTORE_DB -c 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trainer_app'
  psql -v ON_ERROR_STOP=1 -d $RESTORE_DB -c 'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM trainer_app'
  psql -v ON_ERROR_STOP=1 -d $RESTORE_DB -c 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO trainer_app'
  psql -v ON_ERROR_STOP=1 -d $RESTORE_DB -At -c 'SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM attempts) AS attempts, (SELECT count(*) FROM audit_log) AS audit'
"

step "Вход в восстановленную базу через код приложения"
"${DC[@]}" exec -T \
  -e DATABASE_URL="postgresql+asyncpg://trainer_app:${APP_DB_PASSWORD:-trainer-app-dev-password}@postgres:5432/$RESTORE_DB" \
  -e DATABASE_ADMIN_URL="postgresql+asyncpg://trainer:${POSTGRES_PASSWORD:-trainer-dev-password}@postgres:5432/$RESTORE_DB" \
  backend python -m app.admin.restore_check --login admin

if ! $KEEP; then
  step "Удаление $RESTORE_DB"
  "${DC[@]}" exec -T backup psql -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE $RESTORE_DB"
fi
echo
echo "Восстановление проверено: копия $DUMP разворачивается, вход работает."
