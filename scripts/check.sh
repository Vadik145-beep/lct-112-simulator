#!/usr/bin/env bash
# Runs every static check and test suite. Green output = the wave may be closed.
# Usage: scripts/check.sh [--no-db]   (--no-db skips pytest when PostgreSQL is not running)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=()
SKIP_DB=false
[ "${1:-}" = "--no-db" ] && SKIP_DB=true

step() {
  local name="$1"; shift
  echo
  echo "=== $name ==="
  if "$@"; then
    echo "--- OK: $name"
  else
    echo "--- FAIL: $name"
    FAILED+=("$name")
  fi
}

cd "$ROOT/backend"
step "ruff check"  uv run ruff check .
step "ruff format" uv run ruff format --check .
if $SKIP_DB; then
  echo; echo "=== pytest === пропущен (--no-db)"
else
  step "pytest" uv run pytest -q
fi

cd "$ROOT/frontend"
step "tsc --noEmit" npm run --silent typecheck
step "eslint"       npx eslint . --max-warnings 0
step "vitest"       npx vitest run --silent
step "npm run build" npm run --silent build

# End-to-end in a real browser, only when the stand answers (docker compose up).
E2E_URL="${E2E_BASE_URL:-https://localhost}"
if curl -sk -o /dev/null --max-time 5 "$E2E_URL/health"; then
  step "playwright e2e" npx playwright test
else
  echo; echo "=== playwright e2e === пропущен: стенд $E2E_URL не отвечает"
fi

echo
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "Все проверки зелёные."
  exit 0
fi
echo "Красные проверки: ${FAILED[*]}"
exit 1
