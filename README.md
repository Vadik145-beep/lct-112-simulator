# Тренажёр оператора ДДС-112

Учебное программное обеспечение для подготовки операторов дежурно-диспетчерских служб
Москвы, работающих с карточками происшествий из системы-112 (АРМ-112).
Хакатон «Лидеры цифровой трансформации 2026», задача №9.

Два режима занятий: **реагирование на карточку** (диспетчер принимает карточку в журнале и
ведёт статусы) и **приём вызова** (оператор принимает звонок заявителя, роль которого играет
ИИ, и заполняет карточку). Преподаватель ведёт занятия и получает отчёты, администратор
управляет пользователями. Система работает в закрытом контуре без доступа в интернет.

Продукт описан в [PRD.md](PRD.md), порядок разработки в [plan/](plan/README.md),
текущее состояние в [docs/PROGRESS.md](docs/PROGRESS.md).

## Запуск одной командой

Нужны Docker Desktop (Docker не меньше 6 ГБ памяти) и Git.

```bash
git clone https://github.com/Vadik145-beep/lct-112-simulator.git
cd lct-112-simulator
cp .env.example .env          # при необходимости поменяйте пароли и SECRET_KEY
docker compose up -d --build
```

Через минуту откройте **https://localhost**. Сертификат выпущен внутренним центром
сертификации, браузер один раз предупредит; чтобы предупреждения не было, импортируйте
`ca.crt` из тома `certs` (`docker compose cp nginx:/etc/nginx/certs/ca.crt .`).

Проверка состояния: `curl -sk https://localhost/health`.

### Демо-доступы

При `DEMO_MODE=true` (значение по умолчанию) на экране входа есть кнопки «Войти как
обучающийся / преподаватель / администратор». Логины и пароль:

| Роль | Логин | Пароль |
|---|---|---|
| Администратор | `admin` | `Demo12345` |
| Преподаватель | `teacher1`, `teacher2` | `Demo12345` |
| Обучающийся | `student1` … `student12` | `Demo12345` |

Пароль задаётся переменной `SEED_PASSWORD` в `.env`. При `DEMO_MODE=false` демо-кнопок нет,
а при первом входе система требует сменить пароль.

## Сервисы

| Сервис | Назначение |
|---|---|
| `nginx` | TLS, статика фронтенда, прокси `/api` и `/ws` |
| `backend` | FastAPI: API, вход, роли, аудит (`/api/docs` в режиме разработки) |
| `worker` | фоновые задачи (ARQ) |
| `postgres` | PostgreSQL 16 + pgvector |
| `redis` | очередь и события |
| `languagetool` | проверка грамотности |
| `backup` | ежедневная копия базы в 03:00, хранится 14 копий |

Профили `telephony`, `ai`, `live`, `monitoring` объявлены и заполняются по плану.

## Разработка

Нужны Node.js 22, Python 3.12 и [`uv`](https://docs.astral.sh/uv/).

```bash
docker compose up -d postgres redis      # база и очередь
cd backend && uv sync && cp ../.env.example ../.env
uv run alembic upgrade head && uv run python -m app.seed
uv run uvicorn app.main:app --reload     # API на http://localhost:8000
cd ../frontend && npm ci && npm run dev  # интерфейс на http://localhost:5173
```

Типы API из OpenAPI: `npm run gen:api` (берёт схему с работающего бэкенда или экспортирует из
кода). Все проверки разом: `scripts/check.sh` (ruff, pytest, tsc, eslint, vitest, сборка, а при
поднятом стенде и сквозные проверки в браузере `npm run e2e`; браузер один раз ставится командой
`npx playwright install chromium`).
Тесты бэкенда используют отдельную базу `trainer_test` и создают её сами.

## Документация

- [PRD.md](PRD.md) — продукт, архитектура, требования.
- [plan/README.md](plan/README.md) — план по волнам.
- [docs/PROGRESS.md](docs/PROGRESS.md) — что сделано и что дальше.
- [docs/DECISIONS.md](docs/DECISIONS.md), [docs/BLOCKERS.md](docs/BLOCKERS.md),
  [docs/LIBRARIES.md](docs/LIBRARIES.md).
