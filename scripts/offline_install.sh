#!/usr/bin/env bash
# Установка из поставки без интернета (docs/INSTALL.md, раздел 6). Запускается из каталога
# поставки, собранной scripts/offline_bundle.sh:
#
#   ./install.sh [--dir /opt/lct/app] [--profile ai] [--profile telephony]
#
# Что делает: проверяет контрольные суммы, загружает образы в Docker, распаковывает исходники,
# модели и материалы заказчика, создаёт .env из .env.example (если его ещё нет) и поднимает
# систему с запретом на скачивание чего-либо (--pull never, без сборки). Ничего из сети не нужно.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="/opt/lct/app"
PROFILES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) TARGET="$2"; shift ;;
    --profile) PROFILES+=(--profile "$2"); shift ;;
    *) echo "неизвестный параметр: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v docker >/dev/null || { echo "нужен Docker (docker compose v2)" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "нужен docker compose v2" >&2; exit 1; }

echo "== проверка поставки"
( cd "$HERE" && sha256sum -c SHA256SUMS )
cat "$HERE/VERSION"

echo "== образы"
docker load -i "$HERE/images.tar"

echo "== исходники → $TARGET"
mkdir -p "$TARGET"
tar -xf "$HERE/source.tar" -C "$TARGET"
if [ -f "$HERE/organizers.tar" ]; then
  tar -xf "$HERE/organizers.tar" -C "$TARGET"
fi
if [ -f "$HERE/models.tar" ]; then
  echo "== модели"
  tar -xf "$HERE/models.tar" -C "$TARGET"
fi
cd "$TARGET"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "   создан .env из .env.example — поменяйте SECRET_KEY и пароли (см. INSTALL.md, раздел 2)"
fi

echo "== запуск (${PROFILES[*]:-без профилей})"
docker compose "${PROFILES[@]}" up -d --no-build --pull never

echo "== ожидание готовности"
for _ in $(seq 1 60); do
  if docker compose ps --format '{{.Name}} {{.Health}}' | grep -q 'backend-1 healthy'; then
    break
  fi
  sleep 5
done
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
HTTPS_PORT=$(grep -E '^HTTPS_PORT=' .env | cut -d= -f2)
echo "готово: https://localhost:${HTTPS_PORT:-443}"
