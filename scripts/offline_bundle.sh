#!/usr/bin/env bash
# Собирает поставку для установки без интернета (docs/INSTALL.md, раздел 6).
#
# На машине с интернетом:
#   ./scripts/offline_bundle.sh [--with-models] [--out DIR]
#
# В DIR (по умолчанию ./offline-bundle) появятся:
#   images.tar       — все образы всех профилей (ai, telephony), собранные и скачанные здесь;
#   source.tar       — исходники репозитория (git archive HEAD) без .git;
#   models.tar       — каталог моделей MODELS_DIR (только с --with-models, ~10 ГБ);
#   organizers.tar   — data/organizers, если каталог есть (материалы заказчика);
#   install.sh       — установщик, копия scripts/offline_install.sh;
#   SHA256SUMS       — контрольные суммы всего выше;
#   VERSION          — коммит и дата сборки.
#
# Дальше носитель переносится в контур, там: ./install.sh (см. offline_install.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="./offline-bundle"
WITH_MODELS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-models) WITH_MODELS=1 ;;
    --out) OUT="$2"; shift ;;
    *) echo "неизвестный параметр: $1" >&2; exit 2 ;;
  esac
  shift
done
PROFILES=(--profile ai --profile telephony)
MODELS_DIR="${MODELS_DIR:-./models}"

mkdir -p "$OUT"
echo "== образы: сборка и загрузка (${PROFILES[*]})"
docker compose "${PROFILES[@]}" build
docker compose "${PROFILES[@]}" pull --ignore-buildable
IMAGES=$(docker compose "${PROFILES[@]}" config --images | sort -u)
echo "$IMAGES" | sed 's/^/   /'
# shellcheck disable=SC2086
docker save $IMAGES -o "$OUT/images.tar"

echo "== исходники"
git archive --format=tar -o "$OUT/source.tar" HEAD
cp scripts/offline_install.sh "$OUT/install.sh"
chmod +x "$OUT/install.sh"

if [ "$WITH_MODELS" = 1 ]; then
  echo "== модели из $MODELS_DIR"
  [ -d "$MODELS_DIR" ] || { echo "нет каталога моделей: сначала ./scripts/fetch_models.sh" >&2; exit 1; }
  tar -cf "$OUT/models.tar" -C "$(dirname "$MODELS_DIR")" "$(basename "$MODELS_DIR")"
else
  echo "== модели пропущены (--with-models, чтобы включить)"
fi

if [ -d data/organizers ]; then
  echo "== материалы заказчика data/organizers"
  tar -cf "$OUT/organizers.tar" data/organizers
fi

{
  echo "commit=$(git rev-parse HEAD)"
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "images=$(echo "$IMAGES" | tr '\n' ' ')"
} > "$OUT/VERSION"

echo "== контрольные суммы"
( cd "$OUT" && sha256sum images.tar source.tar install.sh VERSION $( [ -f models.tar ] && echo models.tar ) $( [ -f organizers.tar ] && echo organizers.tar ) > SHA256SUMS )
du -sh "$OUT"/* | sed 's/^/   /'
echo "готово: $OUT"
