# Библиотеки и модели

Все зависимости с лицензиями. Модели ИИ добавляются с волны 5.

## Бэкенд (Python)

| Библиотека | Лицензия | Зачем |
|---|---|---|
| FastAPI, Starlette, Uvicorn | MIT / BSD | HTTP API, WebSocket |
| Pydantic, pydantic-settings | MIT | схемы, настройки из `.env` |
| SQLAlchemy 2, asyncpg | MIT / Apache 2.0 | PostgreSQL |
| Alembic | MIT | миграции |
| argon2-cffi | MIT | хэширование паролей |
| PyJWT | MIT | access / refresh токены |
| structlog | MIT / Apache 2.0 | JSON-логи |
| prometheus-fastapi-instrumentator | ISC | `/api/metrics` |
| redis, ARQ | MIT | очередь фоновых задач |
| httpx | BSD | клиент HTTP (healthcheck, тесты) |
| pytest, pytest-asyncio, ruff | MIT | тесты и линтер |

## Фронтенд (TypeScript)

| Библиотека | Лицензия | Зачем |
|---|---|---|
| React, React DOM | MIT | интерфейс |
| React Router | MIT | маршруты |
| TanStack Query | MIT | загрузка данных |
| openapi-fetch, openapi-typescript | MIT | типизированный клиент API |
| Tailwind CSS 4 | MIT | стили |
| shadcn/ui (код компонентов в репозитории), Radix Slot, CVA, clsx, tailwind-merge | MIT | компоненты |
| lucide-react | ISC | иконки |
| @fontsource/roboto, @fontsource-variable/golos-text, @fontsource/jetbrains-mono | OFL / Apache 2.0 | шрифты в бандле, без внешних запросов |
| Vite, TypeScript, ESLint, Vitest, Testing Library | MIT | сборка, проверка, тесты |
| Playwright (Chromium) | Apache 2.0 | сквозные проверки в браузере |

## Инфраструктура (образы)

| Образ | Лицензия | Зачем |
|---|---|---|
| pgvector/pgvector:pg16 | PostgreSQL License | база данных с векторами |
| redis:7-alpine | BSD-3 (Redis 7.2) | очередь и события |
| nginx:1.27-alpine | BSD-2 | TLS, статика, прокси |
| erikvl87/languagetool | LGPL 2.1 | проверка грамотности (с волны 1) |
| postgres:16-alpine | PostgreSQL License | резервные копии (pg_dump) |
