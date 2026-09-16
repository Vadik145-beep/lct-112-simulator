#!/usr/bin/env bash
# Downloads the local AI models once (the only step that needs the internet, PRD section 2).
# Files, sizes and sha256 are listed in scripts/models.manifest; downloads resume and are
# verified, so the script can be re-run safely. Models land in models/ (not in git) and are
# mounted into the compose services of the `ai` profile (MODELS_DIR in .env).
#
# Usage: scripts/fetch_models.sh [--only llm|stt|tts|embeddings] [--dest DIR]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/scripts/models.manifest"
DEST="${MODELS_DIR:-$ROOT/models}"
ONLY=""
HF_BASE="${HF_ENDPOINT:-https://huggingface.co}"

while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$DEST"
failed=0
total=0
skipped=0

while IFS=$'\t' read -r repo remote local size sha; do
  [ -z "$repo" ] && continue
  case "$repo" in \#*) continue ;; esac
  if [ -n "$ONLY" ] && [ "${local#"$ONLY"/}" = "$local" ]; then
    continue
  fi
  total=$((total + 1))
  target="$DEST/$local"
  mkdir -p "$(dirname "$target")"
  url="$HF_BASE/$repo/resolve/main/$remote"

  if [ -f "$target" ] && [ "$(stat -c %s "$target")" = "$size" ]; then
    skipped=$((skipped + 1))
    continue
  fi

  echo "→ $local ($(( size / 1048576 )) МБ)"
  # -C - resumes a partial file; --fail turns HTTP errors into a non-zero exit.
  if ! curl -L --fail --retry 5 --retry-delay 5 -C - --progress-bar -o "$target" "$url"; then
    echo "   ошибка загрузки $url" >&2
    failed=$((failed + 1))
    continue
  fi
  if [ "$sha" != "-" ]; then
    actual="$(sha256sum "$target" | cut -d' ' -f1)"
    if [ "$actual" != "$sha" ]; then
      echo "   sha256 не совпал: ожидалось $sha, получено $actual — файл удалён, запустите снова" >&2
      rm -f "$target"
      failed=$((failed + 1))
    fi
  fi
done < "$MANIFEST"

echo
echo "Файлов в манифесте: $total, уже были: $skipped, ошибок: $failed. Папка: $DEST"
[ "$failed" -eq 0 ] || exit 1
