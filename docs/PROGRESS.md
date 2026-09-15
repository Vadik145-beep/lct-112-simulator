# Прогресс

Ведётся агентом и командой. Новая сессия читает этот файл первым.

## Текущая волна

Волна 0 ([plan/wave-00.md](../plan/wave-00.md)), ветка `wave-00/main`. Все проверки зелёные, ждёт PR.

### Сделано

- Репозиторий: структура `backend/ frontend/ deploy/ scripts/ data/seed/ docs/`, `.gitattributes`,
  `.editorconfig`, `.env.example` с комментариями, `README.md`, `docs/DECISIONS.md`,
  `docs/BLOCKERS.md`, `docs/LIBRARIES.md`.
- Инфраструктура: `docker-compose.yml` (nginx, backend, worker, postgres+pgvector, redis,
  languagetool, backup; healthcheck у каждого; профили `telephony/ai/live/monitoring` объявлены),
  nginx с TLS от внутреннего CA (`deploy/certs/generate.sh`, сертификаты создаются при первом
  старте), `scripts/check.sh`.
- Бэкенд: FastAPI + pydantic-settings + SQLAlchemy async + Alembic (миграция `0001`: `users`,
  `audit_log`, права роли приложения); `/api/health`, `/api/metrics`, `/api/config`; structlog JSON;
  вход `login/refresh/logout/change-password` (argon2, JWT, refresh в httpOnly-cookie), блокировка
  на 15 минут после 5 неудач, `must_change_password`, `POST /auth/demo/{role}` при `DEMO_MODE`;
  `require_role`, `GET /me`, пустые кабинеты `GET /api/{student|teacher|admin}/cabinet`;
  `audit_log` с цепочкой SHA-256, у роли БД нет UPDATE/DELETE; `python -m app.seed`
  (admin, teacher1-2, student1-12, идемпотентно); ARQ-воркер со smoke-задачей `ping`.
- Фронтенд: Vite + React 19 + TypeScript strict, Tailwind 4, компоненты в стиле shadcn/ui,
  шрифты пакетами (Roboto, Golos Text, JetBrains Mono); светлая и тёмная темы с переключателем;
  маршруты `/login`, `/change-password`, `/student`, `/teacher`, `/admin` с каркасом (шапка: имя,
  роль, тема, выход; навигация по роли), «Нет доступа», «Страница не найдена»; экран входа
  с демо-кнопками при `DEMO_MODE`; `npm run gen:api` (типы из OpenAPI); состояния загрузки
  и ошибки; тесты Vitest (охрана маршрутов, экран входа).
- Тесты бэкенда: `tests/test_auth.py` (вход, блокировка, 403 по ролям, refresh/logout, смена
  пароля, `must_change_password`, демо-вход), `tests/test_audit.py` (порядок записей, цепочка
  хэшей, обнаружение подмены, запрет UPDATE/DELETE/TRUNCATE для роли приложения).
- Сквозные проверки Playwright (`frontend/e2e/wave-00.spec.ts`, 8 сценариев): вход тремя ролями
  с демо-кнопок, свой кабинет, чужой кабинет → «Нет доступа», выход, сессия после перезагрузки,
  вход по паролю и ошибка при неверном, тема, чистая консоль, нет внешних запросов.
  Скриншоты в `docs/screenshots/wave-00/`.

### Проверено

- `docker compose up -d --build` → все 7 сервисов healthy, `https://localhost/health` = ok.
- `scripts/check.sh`: ruff, pytest (28), tsc, eslint, vitest (10), build, playwright (8) — зелёные.
- Через nginx (curl): cookie `HttpOnly; Secure; SameSite=strict`, 403 обучающемуся на
  `/api/teacher/cabinet`, 423 после 5 неверных паролей, `/api/metrics` отдаёт метрики.

### Осталось

- Файлы организаторов положить в `data/organizers/` (не коммитятся) — нужны волне 1.

### Как проверить

```bash
cp .env.example .env
docker compose up -d --build && sleep 20 && curl -sk https://localhost/health
scripts/check.sh                       # всё разом
cd backend && uv run pytest -q         # только бэкенд (нужен PostgreSQL на localhost:5432)
```

## Закрытые волны

| Волна | Закрыта | Коммит | Кто проверил |
|---|---|---|---|

## Открытые вопросы и блокеры

См. [BLOCKERS.md](BLOCKERS.md) и [DECISIONS.md](DECISIONS.md).
