#!/usr/bin/env bash
# Собирает сопроводительную документацию в один файл DOCX и PDF для сдачи.
# Pandoc запускается в контейнере, ставить его на машину не нужно.
#
#   ./scripts/build_docs.sh            # DOCX и PDF в docs/build/
#   ./scripts/build_docs.sh docx       # только DOCX
set -euo pipefail

cd "$(dirname "$0")/.."
OUT=docs/build
FORMAT=${1:-all}
IMAGE=pandoc/latex:3.1
TITLE="Тренажёр оператора ДДС-112. Сопроводительная документация"
DATE="$(date +%d.%m.%Y)"

# Порядок разделов в итоговом документе
FILES=(
  docs/ARCHITECTURE.md
  docs/METHODS.md
  docs/INSTALL.md
  docs/USER_GUIDE.md
  docs/LIMITATIONS.md
  docs/REQUIREMENTS_MATRIX.md
  docs/LIBRARIES.md
  docs/DATASET.md
  docs/PERFORMANCE.md
)

mkdir -p "$OUT"

# Титульный лист и метаданные
cat > "$OUT/metadata.yaml" <<EOF
---
title: "$TITLE"
subtitle: "Хакатон «Лидеры цифровой трансформации 2026», задача №9"
date: "$DATE"
lang: ru-RU
toc: true
toc-depth: 2
numbersections: true
geometry: margin=2cm
mainfont: "DejaVu Serif"
sansfont: "DejaVu Sans"
monofont: "DejaVu Sans Mono"
---
EOF

run_pandoc() {
  docker run --rm -v "$PWD:/data" -w /data "$IMAGE" "$@"
}

if [[ "$FORMAT" == "all" || "$FORMAT" == "docx" ]]; then
  echo "Собираю DOCX…"
  run_pandoc "$OUT/metadata.yaml" "${FILES[@]}" -o "$OUT/documentation.docx"
  echo "  $OUT/documentation.docx"
fi

if [[ "$FORMAT" == "all" || "$FORMAT" == "pdf" ]]; then
  echo "Собираю PDF…"
  run_pandoc "$OUT/metadata.yaml" "${FILES[@]}" --pdf-engine=xelatex -o "$OUT/documentation.pdf"
  echo "  $OUT/documentation.pdf"
fi

echo "Готово. Файлы в $OUT/ (каталог не коммитится)."
