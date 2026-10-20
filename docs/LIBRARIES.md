# Библиотеки и модели

Все зависимости с лицензиями. Модели ИИ — в разделе «Модели», облачные сервисы, которые
подключаются только явной настройкой, — в разделе «Облачные сервисы». Версии библиотек
зафиксированы в `backend/uv.lock`, `frontend/package-lock.json` и `deploy/stt/requirements.txt`.

## Бэкенд (Python 3.12)

| Библиотека | Лицензия | Зачем |
|---|---|---|
| FastAPI, Starlette, Uvicorn | MIT / BSD-3 | HTTP API, WebSocket событий занятия |
| python-multipart | Apache 2.0 | приём записанных фраз и звуковых файлов реплик |
| Pydantic, pydantic-settings | MIT | схемы, строгая схема сценария, настройки из `.env` |
| SQLAlchemy 2, asyncpg | MIT / Apache 2.0 | PostgreSQL |
| Alembic | MIT | миграции |
| argon2-cffi | MIT | хеширование паролей |
| PyJWT | MIT | токены доступа и продления |
| cryptography | Apache 2.0 / BSD | шифрование SIP-паролей обучающихся (Fernet) |
| structlog | MIT / Apache 2.0 | журналы в JSON |
| prometheus-fastapi-instrumentator | ISC | метрики `/api/metrics` |
| redis | MIT | рассылка событий занятия, состояние задач генерации, блокировки звонков |
| ARQ | MIT | сервис `worker`, зарезервирован под фоновые задачи |
| httpx | BSD-3 | клиент моделей (llama.cpp, распознавание, облачная модель), LanguageTool, облачный голос |
| websockets | BSD-3 | события ARI Asterisk |
| tzdata | Apache 2.0 | часовой пояс Москвы в контейнере |
| openpyxl | MIT | разбор классификатора (xlsx), выгрузка отчёта в Excel |
| ReportLab | BSD | отчёт о занятии в PDF; шрифт DejaVu Sans (лицензия Bitstream Vera) встроен, кириллица без системных шрифтов |
| rapidfuzz | MIT | нечёткое сравнение улиц, адресов и ключевых слов в оценке и в разговоре |
| NumPy | BSD-3 | звук звонка (RTP ↔ PCM, детектор речи, передискретизация), модель прогноза готовности, виртуальная когорта |
| onnxruntime | MIT | модель смысловой близости e5 и детектор речи Silero VAD |
| tokenizers | Apache 2.0 | токенизация текста для e5 |
| PyTorch (сборка для процессора) | BSD-3 | запуск модели синтеза Silero |
| num2words | LGPL-2.1 (библиотека без изменений) | числа словами перед синтезом Silero («11 лет» → «одиннадцать лет», номера по цифрам) |
| piper-tts (с espeak-ng) | GPL-3.0 | запасной движок синтеза, если нет модели Silero |
| FFmpeg (пакет Debian, отдельный процесс) | LGPL-2.1+ / GPL-2.0+ | перевод озвучки в MP3 и в формат Asterisk |

Разработка и тесты: pytest, pytest-asyncio, ruff (MIT); pypdf (BSD-3) для проверки выгрузки PDF.
Сборка образа: uv (MIT / Apache 2.0), базовый образ `python:3.12-slim` (лицензия PSF и
лицензии пакетов Debian).

Группа `dataset` (только подготовка данных на машине разработчика, в образ не попадает):

| Библиотека | Лицензия | Зачем |
|---|---|---|
| PyMuPDF | AGPL-3.0 (только в скриптах подготовки, в продукт не входит) | рендер сканов билетов |
| pytesseract + Tesseract OCR 5 (`rus`) | Apache 2.0 | распознавание билетов |
| Pillow, NumPy | MIT-CMU / BSD-3 | поиск сетки таблицы на скане |
| Shapely | BSD-3 | привязка улиц к районам по геометрии границ |

Подготовка студийной озвучки (`scripts/voice_replies.py`, только машина разработчика с
интернетом, в продукт не входит): httpx и API ElevenLabs. В поставку входят только готовые
звуковые файлы.

Сборка документации (`scripts/build_docs.py`, `scripts/guide_screenshots.py`; только машина, где
собирают PDF, в продукт не входит):

| Библиотека | Лицензия | Зачем |
|---|---|---|
| python-docx | MIT | общий комплект документации в DOCX |
| playwright (Python) + Chromium | Apache 2.0 / BSD | печать руководств в PDF, снимки экрана |
| pypdf | BSD-3 | номера страниц для оглавления PDF |
| Шрифты PT Sans, PT Mono (`docs/fonts`) | SIL Open Font License 1.1 | кириллица в PDF без системных шрифтов |

## Распознавание речи (сервис `stt`)

| Библиотека | Лицензия | Зачем |
|---|---|---|
| faster-whisper 1.2 (CTranslate2, PyAV) | MIT (CTranslate2 MIT, PyAV BSD, FFmpeg в составе PyAV LGPL) | распознавание речи обучающегося на процессоре |
| FastAPI, Uvicorn, python-multipart | MIT / BSD-3 / Apache 2.0 | HTTP-обёртка, совместимая с интерфейсом транскрипции OpenAI |

## Телефония (сервисы `asterisk`, `asterisk-cloud`)

| Компонент | Лицензия | Зачем |
|---|---|---|
| Asterisk 22 (образ `andrius/asterisk:22.10.1_debian-trixie`: PJSIP, ARI, SRTP, Opus) | GPL-2.0 (отдельный процесс, связь по HTTP и WebSocket ARI) | регистрация программного телефона по WebRTC и настольных телефонов по SIP, мост звонка, запись, передача звука на сервер (ExternalMedia), SIP-транк оператора связи; в профиле `cloud` — SIP-транк в облачный голос |

## Фронтенд (TypeScript)

| Библиотека | Лицензия | Зачем |
|---|---|---|
| React 19, React DOM | MIT | интерфейс |
| React Router | MIT | маршруты |
| TanStack Query | MIT | загрузка данных |
| openapi-fetch, openapi-typescript | MIT | типизированный клиент API |
| JsSIP | MIT | программный телефон в браузере: регистрация по WebSocket, звонок по WebRTC |
| @vapi-ai/web (с @daily-co/daily-js) | MIT (daily-js — BSD-2) | облачный голос из браузера; используется только при включённом облачном голосе |
| Apache ECharts | Apache 2.0 | тепловая карта, динамика, калибровочный график и рейтинги в аналитике |
| Tailwind CSS 4 | MIT | стили |
| shadcn/ui (код компонентов в репозитории), Radix Slot, CVA, clsx, tailwind-merge | MIT | компоненты |
| lucide-react | ISC | иконки |
| @fontsource/roboto, @fontsource-variable/golos-text, @fontsource/jetbrains-mono | OFL 1.1 / Apache 2.0 | шрифты в сборке, без внешних запросов |
| Vite, TypeScript, ESLint, Vitest, Testing Library, jsdom | MIT | сборка, проверка, тесты |
| Playwright (Chromium) | Apache 2.0 | сквозные проверки в браузере |

## Инфраструктура (образы)

| Образ | Лицензия | Зачем |
|---|---|---|
| `nginx:1.27-alpine` | BSD-2 | TLS, интерфейс, прокси; собирается с интерфейсом внутри |
| `node:22-alpine` | MIT | только этап сборки интерфейса, в итоговый образ не входит |
| `pgvector/pgvector:pg16` | PostgreSQL License | база данных |
| `redis:7-alpine` | BSD-3 (Redis 7.2) | события занятия |
| `erikvl87/languagetool:6.5` | LGPL-2.1 (LanguageTool) | проверка грамотности |
| `ghcr.io/ggml-org/llama.cpp:server` | MIT | сервер языковых моделей (`llm-dialog`, `llm-gen`) |
| `postgres:16-alpine` | PostgreSQL License | резервные копии (`pg_dump`, `pg_restore`) |
| `andrius/asterisk:22.10.1_debian-trixie` | GPL-2.0 (Asterisk) | телефония |
| `python:3.12-slim` | PSF и лицензии пакетов Debian | основа образов `backend`, `worker`, `stt` |
| `alpine:3.20` | MIT (BusyBox — GPL-2.0) | заготовка профилей `live` и `monitoring` |

## Модели

Скачиваются один раз `scripts/fetch_models.sh` в папку `models/` (не в git) или входят в
поставку без интернета. Файлы, размеры и sha256 — в `scripts/models.manifest`; скрипт проверяет
хеш после загрузки.

| Модель | Файл | Лицензия | Зачем |
|---|---|---|---|
| Qwen2.5-3B-Instruct, GGUF Q4_K_M | `llm/qwen2.5-3b-instruct-q4_k_m.gguf` | **Qwen Research License (некоммерческая)** | в поставке и на стенде — собеседники и написание карточек (одна модель на обе задачи); для коммерческого использования заменить на 1.5B или 7B |
| Qwen2.5-1.5B-Instruct, GGUF Q4_K_M | `llm/qwen2.5-1.5b-instruct-q4_k_m.gguf` | Apache 2.0 | собеседники по умолчанию в `docker-compose.yml`, для слабого сервера |
| Qwen2.5-7B-Instruct, GGUF Q4_K_M (2 части) | `llm/qwen2.5-7b-instruct-q4_k_m-0000N-of-00002.gguf` | Apache 2.0 | написание сценариев и карточек в сервисе `llm-gen` по умолчанию `docker-compose.yml`; в поставке не запускается |
| faster-whisper base / small (Systran, CTranslate2 int8) | `stt/faster-whisper-{base,small}/` | MIT (веса Whisper OpenAI — MIT) | распознавание речи; в поставке `small` |
| Silero TTS v4 ru (голоса aidar, baya, kseniya, xenia, eugene) | `tts/silero_v4_ru.pt` | **CC BY-NC-SA 4.0 (некоммерческая)** | основной синтез речи на сервере; для коммерческого использования нужна коммерческая лицензия Silero или другой движок (Piper) |
| Piper ru_RU denis, dmitri (medium) | `tts/ru_RU-{denis,dmitri}-medium.onnx` | датасет CC0, модель MIT | запасной синтез, мужские голоса |
| Piper ru_RU irina (medium) | `tts/ru_RU-irina-medium.onnx` | датасет RHVoice, лицензия голоса автором не указана; модель MIT | запасной синтез, женский голос; перед коммерческим использованием заменить или уточнить лицензию |
| intfloat/multilingual-e5-small (ONNX) | `embeddings/multilingual-e5-small/` | MIT | смысловая близость комментариев и описаний, поиск по памятке и билетам при генерации |
| Silero VAD v5 (ONNX, onnx-community/silero-vad) | `vad/silero_vad.onnx` | MIT | границы фраз обучающегося в звонке через Asterisk; без файла — определение по громкости |

Модель Silero TTS скачивается с models.silero.ai, остальные — с Hugging Face. Голос Piper
`ruslan` (CC BY-NC-SA) не используется.

**Студийные записи.** Реплики эталонных сценариев и постоянные фразы собеседников ДДС записаны
заранее сервисом ElevenLabs и лежат в `data/seed/audio`. Это готовые звуковые файлы, при работе
тренажёра ElevenLabs не вызывается. Использование записей регулируется условиями ElevenLabs для
аккаунта, на котором они сделаны.

## Облачные сервисы (только при `ALLOW_EXTERNAL_AI=true`)

В закрытом контуре не используются. Без явного разрешения система с этими настройками не
запускается. Это коммерческие сервисы, они работают по условиям поставщика и по ключам
заказчика.

| Сервис | Настройка | Зачем |
|---|---|---|
| Облачная языковая модель с OpenAI-совместимым интерфейсом | `LLM_GEN_URL`, `LLM_GEN_API_KEY`, `LLM_GEN_API_MODEL` | быстрое написание карточек и сценариев |
| Vapi — облачный голос: языковая модель OpenAI GPT-4.1, голос ElevenLabs (eleven_flash_v2_5), распознавание Soniox | `CLOUD_VOICE_ENABLED`, `VAPI_*`, `CLOUD_VOICE_*` | режим «Свободная генерация речи (с интернетом)» |

Отдельно от ИИ к Asterisk можно подключить SIP-линию оператора связи (`TELEPHONY_TRUNK_*`, в
облачном профиле — МультиФон, `MULTIFON_*`), чтобы звонки занятия шли на обычный телефон
обучающегося. Это услуга связи по договору с оператором; по умолчанию линия выключена.

## Данные

| Источник | Лицензия | Зачем |
|---|---|---|
| OpenStreetMap (через Overpass API), © участники OpenStreetMap | ODbL 1.0 | улицы Москвы с округом и районом (`data/seed/streets.json`) |
| Датасет организаторов (классификатор, памятка, билеты, скриншоты АРМ-112) | предоставлен организаторами хакатона | справочники, учебные ситуации, вид рабочего места |
