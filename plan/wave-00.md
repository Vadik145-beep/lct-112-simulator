# Волна 0. Каркас

**Срок:** 15-16.09. **PRD:** разделы 6, 7, 16 (фаза 0), 17.

## Цель

Система поднимается одной командой, в неё можно войти тремя ролями, у каждой роли свой пустой
кабинет, чужие кабинеты закрыты. Дальше все волны только добавляют содержимое.

## Вход

- Репозиторий с `PRD.md`, `plan/`, `.claude/settings.json`.
- Файлы организаторов в `data/organizers/` (не коммитятся).

## Задачи

### Репозиторий
- [ ] Структура папок по PRD раздел 7: `backend/`, `frontend/`, `deploy/`, `scripts/`, `data/seed/`, `docs/`.
- [ ] `.gitattributes` (`* text=auto eol=lf`), `.editorconfig`, `.env.example` с безопасными значениями и комментариями к каждой переменной.
- [ ] `CLAUDE.md` уже в репозитории, не переписывать.
- [ ] `README.md`: что это, запуск одной командой, демо-доступы, ссылки на PRD и план.
- [ ] `docs/PROGRESS.md`, `docs/DECISIONS.md`, `docs/BLOCKERS.md` заведены.

### Инфраструктура
- [ ] `docker-compose.yml`: `nginx`, `backend`, `worker`, `postgres` (pgvector), `redis`, `languagetool`, `backup`; healthcheck у каждого; профили `telephony`, `ai`, `live`, `monitoring` объявлены пустыми.
- [ ] nginx с TLS от внутреннего CA: `deploy/certs/generate.sh`, статика фронтенда, прокси `/api` и `/ws`.
- [ ] `scripts/check.sh`: ruff, pytest, `tsc --noEmit`, eslint, vitest, `npm run build`.

### Бэкенд
- [ ] FastAPI, настройки через pydantic-settings, SQLAlchemy async, Alembic с первой миграцией (`users`, `audit_log`).
- [ ] `/health`, `/metrics`, structlog в JSON.
- [ ] Вход: `POST /auth/login`, `refresh` (httpOnly-cookie), `logout`, `change-password`; argon2; блокировка на 15 минут после 5 неудач; `must_change_password`.
- [ ] Роли и зависимости `require_role(...)`; `GET /me`.
- [ ] `POST /auth/demo/{role}` при `DEMO_MODE=true`.
- [ ] `audit_log` с цепочкой хэшей; у роли БД приложения нет UPDATE и DELETE на таблицу; запись входа и выхода.
- [ ] `app.seed` создаёт `admin`, `teacher1`, `teacher2`, `student1`…`student12` (идемпотентно).

### Фронтенд
- [ ] Vite + React + TypeScript strict, Tailwind 4, shadcn/ui, шрифты пакетами (Roboto, Golos Text, JetBrains Mono).
- [ ] Токены темы, светлая и тёмная, переключатель.
- [ ] React Router: `/login`, `/student`, `/teacher`, `/admin`; каркас с шапкой (имя, роль, выход) и навигацией по роли; страница «Нет доступа».
- [ ] Экран входа с демо-кнопками при `DEMO_MODE`.
- [ ] `npm run gen:api` генерирует типы из OpenAPI бэкенда.
- [ ] Состояния загрузки и ошибки в каркасе.

## Проверка

```bash
docker compose up -d --build && sleep 20 && curl -sk https://localhost/health
scripts/check.sh
pytest backend/tests/test_auth.py -q     # вход, блокировка попыток, 403 по ролям, аудит
```

- [ ] `https://localhost` открывается без ошибок в консоли.
- [ ] Вход каждой ролью, выход, повторный вход.
- [ ] `student1` по адресу `/teacher` видит «Нет доступа»; API отвечает 403.
- [ ] 5 неверных паролей блокируют вход на 15 минут (тест).
- [ ] `git log -1 --format=%B` не содержит `Claude`.

## Показать

Три входа подряд с демо-кнопок, у каждой роли свой кабинет. Скриншот в `docs/screenshots/wave-00/`.

## Если не получается

TLS и nginx мешают: временно поднять `http://localhost:5173` через Vite dev, но TLS вернуть до
закрытия волны. Docker Desktop не даёт памяти: 6 ГБ достаточно для волны 0.
