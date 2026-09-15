"""Container healthcheck: exit 0 when the API answers /api/health with 200."""

import sys

import httpx

from app.main import API_PREFIX

if __name__ == "__main__":
    try:
        r = httpx.get(f"http://localhost:8000{API_PREFIX}/health", timeout=4)
        sys.exit(0 if r.status_code == 200 else 1)
    except httpx.HTTPError:
        sys.exit(1)
