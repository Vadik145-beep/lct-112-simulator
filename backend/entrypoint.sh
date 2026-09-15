#!/bin/sh
# Applies migrations and seeds demo data before starting the API (the worker skips both).
set -e
if [ "$1" = "uvicorn" ]; then
  alembic upgrade head
  python -m app.seed
fi
exec "$@"
