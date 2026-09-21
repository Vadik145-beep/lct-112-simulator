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
| `backend` | FastAPI: API, вход, роли, аудит, справочники датасета (`/api/docs` в режиме разработки) |
| `worker` | фоновые задачи (ARQ) |
| `postgres` | PostgreSQL 16 + pgvector |
| `redis` | очередь и события |
| `languagetool` | проверка грамотности |
| `backup` | ежедневная копия базы в 03:00, хранится 14 копий |
| `asterisk` (профиль `telephony`) | SIP/WebRTC-телефония: звонок в софтфон браузера или настольный телефон, запись разговора |

Профиль `ai` (`docker compose --profile ai up -d`): `llm-dialog`, `llm-gen` (llama.cpp),
`stt` (faster-whisper); модели скачиваются `scripts/fetch_models.sh`.

Профиль `telephony` (`docker compose --profile ai --profile telephony up -d`): `asterisk`
(PJSIP, WebRTC, ARI). В `.env` поставьте `TELEPHONY_ENABLED=true`; на Docker Desktop
(Windows, macOS) обязателен `TELEPHONY_EXTERNAL_IP` — адрес машины в локальной сети, иначе
браузер не получит звук. Софтфон обучающегося регистрируется сам при входе
(`wss://<хост>/ws/sip`), настольный IP-телефон подключается к порту `SIP_PORT` (5060 udp/tcp)
с логином `phone-<логин>` и паролем из `GET /api/me/sip`; тест гарнитуры — номер `100` (эхо).
Звонок обучающемуся вручную, в обход формы занятия:
`uv run --project backend python scripts/issue_call.py --student student1`. Без профиля
панель вызова работает через микрофон браузера.

Профили `live`, `monitoring` объявлены и заполняются по плану.

## Данные организаторов

Справочники (классификатор происшествий, статусы, типичные ошибки, 96 ситуаций из билетов,
улицы Москвы) лежат в `data/seed/` и загружаются в базу при старте бэкенда; повторная загрузка
безопасна: `docker compose exec backend python -m app.importers.organizers`. Исходные файлы
организаторов кладутся в `data/organizers/` (в git не попадают); если они на месте, импорт
пересобирает `data/seed/classifier.json` из xlsx. Разбор датасета: [docs/DATASET.md](docs/DATASET.md).

Пересборка производных файлов на машине разработчика (нужны интернет для улиц и Tesseract с
языком `rus` для билетов):

```bash
cd backend
uv run --group dataset python -m app.importers.tickets_ocr    # data/seed/tickets.json
uv run --group dataset python -m app.importers.memo_extract   # data/seed/memo.txt
uv run --group dataset python -m app.importers.streets_osm    # data/seed/streets.json
```

## Разработка

Нужны Node.js 22, Python 3.12 и [`uv`](https://docs.astral.sh/uv/).

```bash
docker compose up -d postgres redis      # база и очередь
cd backend && uv sync && cp ../.env.example ../.env
uv run alembic upgrade head && uv run python -m app.importers.organizers && uv run python -m app.seed
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
- [docs/DATASET.md](docs/DATASET.md) — разбор датасета организаторов.
- [docs/DECISIONS.md](docs/DECISIONS.md), [docs/BLOCKERS.md](docs/BLOCKERS.md),
  [docs/LIBRARIES.md](docs/LIBRARIES.md).
