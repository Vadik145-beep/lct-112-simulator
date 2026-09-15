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
| openpyxl | MIT | разбор классификатора (xlsx) |
| pytest, pytest-asyncio, ruff | MIT | тесты и линтер |

Группа `dataset` (только подготовка данных на машине разработчика, в образ не попадает):

| Библиотека | Лицензия | Зачем |
|---|---|---|
| PyMuPDF | AGPL-3.0 (используется только в скриптах подготовки, в продукт не входит) | рендер сканов билетов, текст памятки |
| pytesseract + Tesseract OCR 5 (`rus`) | Apache 2.0 | распознавание билетов |
| Pillow, NumPy | MIT-CMU / BSD | поиск сетки таблицы на скане |
| Shapely | BSD | привязка улиц к районам по геометрии границ |

## Данные

| Источник | Лицензия | Зачем |
|---|---|---|
| OpenStreetMap (через Overpass API), © участники OpenStreetMap | ODbL 1.0 | улицы Москвы с округом и районом (`data/seed/streets.json`) |
| Датасет организаторов (классификатор, памятка, билеты) | предоставлен организаторами хакатона | справочники и ситуации для сценариев |

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
| erikvl87/languagetool | LGPL 2.1 | проверка грамотности (`GrammarProvider`) |
| postgres:16-alpine | PostgreSQL License | резервные копии (pg_dump) |
