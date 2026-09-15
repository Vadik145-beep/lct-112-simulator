"""Prints the OpenAPI schema as JSON (used by `npm run gen:api` when the API is not running).

Run: python -m app.openapi_export > openapi.json
"""

import json
import os
import sys

# Building the schema needs settings but no database; fill in placeholders if absent.
os.environ.setdefault("SECRET_KEY", "openapi-export-placeholder-secret-key-000")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("DATABASE_ADMIN_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("LOG_LEVEL", "ERROR")

from app.main import app

if __name__ == "__main__":
    json.dump(app.openapi(), sys.stdout, ensure_ascii=False, indent=2)
