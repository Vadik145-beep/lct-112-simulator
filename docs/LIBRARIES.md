# Библиотеки и модели

Все зависимости с лицензиями, модели ИИ — в разделе «Модели».

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
| rapidfuzz | MIT | нечёткое сравнение улиц и ключевых слов в оценке |
| websockets | BSD-3 | события ARI Asterisk (`app/telephony/ari.py`), клиент WebSocket в тестах |
| cryptography | Apache 2.0 / BSD | шифрование SIP-паролей обучающихся (Fernet, ключ из SECRET_KEY) |
| NumPy | BSD | звук звонка: RTP ↔ PCM, VAD, ресемплинг (`app/telephony/media.py`) |
| piper-tts (+ onnxruntime, numpy, espeak-ng в составе) | GPL-3.0 (piper 1.x, из-за espeak-ng) | озвучка реплик заявителя (`TTSProvider`) |
| onnxruntime | MIT | запуск модели эмбеддингов e5 (`EmbeddingProvider`) |
| tokenizers | Apache 2.0 | токенизация текста для e5 |
| pytest, pytest-asyncio, ruff | MIT | тесты и линтер |

Группа `dataset` (только подготовка данных на машине разработчика, в образ не попадает):

| Библиотека | Лицензия | Зачем |
|---|---|---|
| PyMuPDF | AGPL-3.0 (используется только в скриптах подготовки, в продукт не входит) | рендер сканов билетов, текст памятки |
| pytesseract + Tesseract OCR 5 (`rus`) | Apache 2.0 | распознавание билетов |
| Pillow, NumPy | MIT-CMU / BSD | поиск сетки таблицы на скане |
| Shapely | BSD | привязка улиц к районам по геометрии границ |

Сервис `asterisk` (`deploy/asterisk`, образ `andrius/asterisk` 22.10.1 на Debian):

| Компонент | Лицензия | Зачем |
|---|---|---|
| Asterisk 22 (PJSIP, res_ari, res_srtp, codec_opus) | GPL-2.0 (отдельный процесс, связь по HTTP/WebSocket ARI) | SIP-регистрация софтфона и настольных телефонов, WebRTC (DTLS-SRTP, ICE), запись, снуп канала, ExternalMedia |

Фронтенд:

| Библиотека | Лицензия | Зачем |
|---|---|---|
| JsSIP | MIT | софтфон в браузере: регистрация по WebSocket, приём вызова по WebRTC (`src/softphone/`) |

Сервис `stt` (`deploy/stt`, отдельный образ):

| Библиотека | Лицензия | Зачем |
|---|---|---|
| faster-whisper (+ CTranslate2, PyAV) | MIT (CTranslate2 MIT, PyAV BSD, FFmpeg LGPL) | распознавание речи оператора (`STTProvider`) |
| FastAPI, Uvicorn, python-multipart | MIT / BSD | HTTP-обёртка, совместимая с эндпоинтом транскрипции OpenAI |

## Модели

Скачиваются один раз `scripts/fetch_models.sh` в `models/` (не в git). Файлы, размеры и sha256
в `scripts/models.manifest`; скрипт проверяет хэш после загрузки.

| Модель | Файл | Лицензия | Зачем |
|---|---|---|---|
| Qwen2.5-1.5B-Instruct, GGUF Q4_K_M | `llm/qwen2.5-1.5b-instruct-q4_k_m.gguf` | Apache 2.0 | диалог с заявителем (`llm-dialog`, кандидат) |
| Qwen2.5-3B-Instruct, GGUF Q4_K_M | `llm/qwen2.5-3b-instruct-q4_k_m.gguf` | Qwen Research License (некоммерческая) | диалог, кандидат для сравнения; на стенде только если выиграет замер, для продажи не годится |
| Qwen2.5-7B-Instruct, GGUF Q4_K_M (2 части) | `llm/qwen2.5-7b-instruct-q4_k_m-0000N-of-00002.gguf` | Apache 2.0 | генерация сценариев (`llm-gen`), судья в замерах |
| faster-whisper base / small (Systran, CTranslate2 int8) | `stt/faster-whisper-{base,small}/` | MIT (веса Whisper OpenAI — MIT) | распознавание речи оператора |
| Piper ru_RU denis, dmitri (medium) | `tts/ru_RU-{denis,dmitri}-medium.onnx` | датасет CC0, модель MIT | мужские голоса заявителя |
| Piper ru_RU irina (medium) | `tts/ru_RU-irina-medium.onnx` | датасет RHVoice, лицензия не указана автором голоса; модель MIT | женский голос; перед продажей продукта заменить или уточнить лицензию |
| intfloat/multilingual-e5-small (ONNX) | `embeddings/multilingual-e5-small/` | MIT | смысловая близость описаний (`EmbeddingProvider`) |
| Silero VAD v5 (ONNX, onnx-community/silero-vad) | `vad/silero_vad.onnx` | MIT | границы фраз оператора в звонке (`app/telephony/media.py`); без файла — детектор по громкости |

Голос `ruslan` (CC BY-NC-SA) не используется.

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
| ghcr.io/ggml-org/llama.cpp:server | MIT | сервер моделей диалога и генерации (профиль `ai`) |
| postgres:16-alpine | PostgreSQL License | резервные копии (pg_dump) |
