#!/bin/sh
# Applies migrations, loads the reference dataset and seeds demo data before starting the API
# (the worker skips all three).
set -e
if [ "$1" = "uvicorn" ]; then
  alembic upgrade head
  python -m app.importers.organizers
  python -m app.seed
fi
exec "$@"
