# Прогресс

Ведётся агентом и командой. Новая сессия читает этот файл первым.

## Текущая волна

Волна 7 ([plan/wave-07.md](../plan/wave-07.md)), ветка `wave-07/call-intake`, issue #8.
Начата параллельно с волной 6 (телефония) по браузерному пути; после мержа волны 6
(`656680d`) перебазирована на неё — см. «Осталось» про встраивание софтфона.

### Сделано

- Режим «Приём вызова» разрешён в настройках занятия (`sessions.py`): веса проверяются по
  составляющим приёма вызова, профиль службы не применяется, `bad_dialog_mode` остался.
  В форме занятия режим активен, при нём появляются «Как отвечает заявитель» (select /
  hybrid / generate / buttons) и «Голос», норматив по умолчанию 90 с, профиль службы отключён.
- Выдача вызовов: один активный вызов на обучающегося независимо от сложности; `sweep`
  не ставит «Не оповещено» вызовам; `evaluation_input` для приёма вызова собирает вход
  PRD 9.3 из `attempts.draft`, стенограммы и отметок панели вызова.
- `app/intake/` — карточка оператора 112: `PUT /attempts/{id}/draft` (черновик в
  `attempts.draft` с `updated_at`, событие `attempt.progress: filling_card`),
  `POST /attempts/{id}/submit` (идемпотентно по `client_submission_id`, закрывает попытку,
  оценка в запросе, `attempt.submitted` + `attempt.evaluated`, следующий вызов сразу).
  Завершение занятия закрывает открытый вызов и оценивает черновик.
- `GET /attempts/{id}` для вызова: `intake` (АОН, черновик, обязательные темы, название после
  закрытия), `reference` = `reference_card` после закрытия, `evaluation.checked_text` =
  описание со слов заявителя.
- Сид: 5 новых сценариев приёма вызова из билетов (4-1 балкон на 13-м этаже без номера дома,
  5-1 пожар с людьми на балконе, 9-1 задымление в метро «Арбатская», 15-3 ножевое ранение,
  18-2 без сознания в Красногорске — другой регион) с репликами на все темы; всего 10.
  Второе запущенное занятие «Приём вызова: тренировка операторов 112» для «Учебной-1»
  (`voice_enabled=true`, `select`, норматив 90, порог 70), очередь от лёгких к сложным.
  `incident_type_code` сценария берётся и из `reference_card`.
- Фронтенд, карточка оператора 112 (`frontend/src/intake/`, по скриншоту стр. 15):
  - `call-page.tsx` — верх: панель вызова, АОН, предоставленный (редактируется), телефон на
    месте, «Происшествие №», крупный таймер разговора (красный после норматива); слева:
    заявитель (ФИО, статус), адрес по полям с подсказкой улиц и автоподстановкой округа и
    района (`address-form.tsx`), «очистить адрес», описание со счётчиком 0/1999; справа:
    три крупные кнопки признаков + признаки выбранного типа, оранжевые «нет контакта» и
    «срыв звонка», опросная карта из `/classifier/tree` (`survey-card.tsx`: группа → ряды
    кнопок по уровням, выбранные синим, путь можно менять, «Класс.:» внизу); низ: оранжевая
    полоса «Службы» с автоподстановкой из `/classifier/{code}/services?flags=`, «+» для
    ручного добавления (удалить нельзя), «сохранить». `Ctrl+Enter` сохранить, `?` подсказка,
    `Tab` по полям. Черновик в `localStorage` и на сервере (не чаще 2 с), после перезагрузки
    берётся более свежий; действие «сохранить» повторяется с тем же `client_submission_id`.
  - `call-panel.tsx` (`CallBlock`) — блок «не подключен / готов» слева сверху; пока вызов
    звонит, идёт или только что завершён, на его месте панель софтфона волны 6
    (`src/softphone/call-panel.tsx`: ответить, завершить, «нет контакта», «срыв звонка»,
    уровень микрофона, устройство, статистика WebRTC; в браузерном режиме — микрофон
    и поле «Фраза заявителю»). Плавающая панель `StudentFrame` скрыта на маршруте карточки.
    Состояние звонка карточка берёт из `useSoftphone()` (SIP или браузер), запасной путь —
    `DialogOut.call`. Отметки «нет контакта» / «срыв звонка» идут через софтфон
    (`/no-contact`, `/call-dropped`) и попадают в оценку из колонок попытки; завершение
    вызова пишется в аудит (`call.end` с причиной).
  - `dialog-panel.tsx` — учебная панель: стенограмма (реплики, темы, 🎙 для распознанной
    речи, «▶ прослушать»), индикаторы «заявитель говорит» / «слушаю вас» / «заявитель
    думает…», поле текстового ввода (Enter), микрофон через MediaRecorder → `/utterance`,
    кнопки тем в режиме `buttons`, «Что выяснить» с обязательными темами при `hints_enabled`.
    Озвученная реплика играет сама после ответа на вызов (через fetch с токеном, как и в
    софтфоне волны 6, — `<audio src>` в `/api/media` не пускает).
  - `calls-page.tsx` — рабочее место между вызовами (`/student/sessions/{id}/calls`):
    запрашивает вызов, открывает карточку по `attempt.issued`, список принятых вызовов.
  - `attempt-page.tsx` — `/student/attempts/{id}` ветвится по режиму занятия.
  - `call-review.tsx` — разбор приёма вызова: итог, составляющие полосами, опросная карта
    (путь против эталона с подсветкой), признаки и службы против эталона (пропущенные
    зачёркнуты, лишние жёлтые), адрес по полям рядом с эталоном, обязательные вопросы
    (выяснены / нет), стенограмма с аудио реплик и записью звонка (когда появится), время,
    ошибки со ссылкой в памятку, описание с подчёркнутыми ошибками, комментарий ИИ.
  - «Мои задания» показывают все идущие занятия; для приёма вызова кнопка «Открыть АРМ
    оператора 112». Мониторинг преподавателя: плитки «входящий вызов» / «говорит с
    заявителем» / «заполняет карточку 112», норматив от момента ответа.
- Тесты: `tests/api/test_call_intake.py` (7: занятие в режиме, один вызов за раз, полный
  путь черновик → сохранение → оценка → следующий вызов, идемпотентность и 409, детектор
  региона, «срыв звонка» (вторая половина ждёт волну 6), права, sweep + завершение занятия),
  `test_sessions.py` (режим больше не 422), `test_fixtures.py` (10 сценариев вызова,
  идеальная попытка → 100). Фронт: `intake/classifier.test.ts` (ряды опросной карты, тип по
  пути, группа по коду, адрес в строку, выбор свежего черновика). E2E
  `e2e/call_intake.spec.ts` (2 сценария: полный путь с перезагрузкой посреди звонка и
  разбором; вызов из другого региона → `region_not_clarified`).

### Проверено

- `uv run pytest -q` — 366 зелёных, 4 skip (база `trainer_test_wave07` на 5436;
  `test_ws.py` отдельно — 2 зелёных); `ruff` чисто; фронт `tsc`, `eslint`, `vitest` (23) чисто.
- Стенд `docker compose -p wave-07 up -d --build` без профиля `ai` (диалог деградирует в
  `buttons`, озвучка Piper и e5 с bind-mount): `E2E_BASE_URL=https://localhost:8473
  npx playwright test e2e/call_intake.spec.ts` — 2 зелёных, скриншоты в
  `docs/screenshots/wave-07/` (01 входящий вызов, 02 заполненная карточка, 03 сохранено,
  04 разбор, 05 детектор региона). Разбор эталонного вызова 31-3: 88 баллов, «Зачтено».
  Весь набор `npx playwright test --workers=1` — 17 зелёных (волны 0, 3, 4, 7) уже с
  встроенным софтфоном; `softphone.spec.ts` на этом стенде падает ожидаемо (нужен профиль
  `telephony`, у стека волны 7 `TELEPHONY_ENABLED=false`). В 4 потока
  на этой машине (три стека рядом) сыпется на таймаутах и блокировке входа после пяти
  неверных паролей — гонять последовательно; `dds.spec.ts` берёт занятие по режиму, так как
  у student1 теперь два идущих занятия.
- Холодный старт на Docker Desktop под Windows: первая озвучка (Piper) ~40 с, первая
  оценка (e5 ONNX с bind-mount) до 150 с — поэтому в `app/warmup.py` модель эмбеддингов
  и голоса Piper (`PiperTTS.warm()`) грузятся при старте бэкенда в фоне (~2 мин на машине
  разработки; ответ на вызов озвучивает открывающую реплику, без прогрева это 40+ с), а
  `E5Embedding` читает файл в Python до создания сессии (чтение отпускает GIL и API не
  замирает). После прогрева `say` ~1 с, `submit` ~4 с (LanguageTool + e5). Прогрев Piper —
  задача волны 8 (worker).
- Аудио реплик (`GET /api/media/*`) требует bearer: панель разговора и разбор тянут файл
  через fetch с токеном и играют из blob.

### Осталось

- Руками с Asterisk (профили `ai` + `telephony`): тот же путь голосом в гарнитуру, запись
  звонка в разборе (`/recording`, кнопка «▶ запись звонка»). Решение 17.09.2026: SIP-путь
  карточки проверяется позже, вместе с доводкой (волна 11) — панель и код звонка те же,
  что проверяла волна 6. На машине разработки стек волны 6 держит SIP 5060 / ARI 8088 /
  RTP 10000–10100 — для второго стека с телефонией менять `SIP_PORT`, `ARI_PORT`,
  `TELEPHONY_RTP_START/END`.
- `e2e/softphone.spec.ts` (SIP, фейковый микрофон) после встраивания панели в карточку
  не перепроверен на стенде с Asterisk: селекторы (`call-panel`, `call-answer`,
  `call-hangup`, `call-stats`, `softphone-status`) сохранены, панель та же.
- «Показать»: полный звонок на камеру, сравнить карточку со скриншотом стр. 15.

### Как проверить

```bash
docker compose -p wave-07 up -d --build           # .env: 8473/8110/5436/6383, без профиля ai
cd backend && uv run pytest tests/api/test_call_intake.py tests/api/test_sessions.py tests/domain -q
cd ../frontend && npx vitest run && E2E_BASE_URL=https://localhost:8473 npx playwright test e2e/call_intake.spec.ts
# Руками: https://localhost:8473 → «Войти как обучающийся» → «Открыть АРМ оператора 112».
```

## Волна 6 (закрыта)

Волна 6 ([plan/wave-06.md](../plan/wave-06.md)), ветка `wave-06/telephony`, issue #7, PR #22 (`656680d`).

### Сделано

- `deploy/asterisk/`: образ на `andrius/asterisk` 22.10.1, профиль `telephony` в compose
  (`hostname: asterisk`, порты SIP 5060 udp/tcp, ARI 8088, RTP 10000–10100/udp), конфиги
  `pjsip.conf` (шаблоны `webrtc-*` с `webrtc=yes`/DTLS/ICE и `phone-*` для настольных
  телефонов), `extensions.conf` (контекст `trainer-out`: звонит софтфону и телефону вместе,
  ставит `X-Attempt-Id`; `trainer-in`: эхо-тест `100`), `ari.conf`, `http.conf`, `rtp.conf`;
  `render-config.sh` рендерит пароль ARI, диапазон RTP, транспорты и внешний адрес для ICE
  из `.env` (`TELEPHONY_EXTERNAL_IP` обязателен на Docker Desktop). nginx проксирует
  `wss://<хост>/ws/sip` → Asterisk (имя резолвится по запросу, без профиля nginx стартует).
- Миграция `0005`: `users.sip_password_enc`, `attempts.call_state/call_ended_at/
  call_end_reason/call_dropped_marked/no_contact_marked`, таблица `settings`.
- `app/telephony/`: `sip.py` (учётки `stu-<логин>`/`phone-<логин>`, пароль Fernet, генерация
  `endpoints.conf` в общий том + `res_pjsip` reload через ARI), `ari.py` (HTTP + WebSocket
  событий с переподключением; обработчики в отдельных задачах), `media.py` (RTP → PCM,
  `PortPool`, Silero VAD / детектор по громкости, `Segmenter`, `.sln16` через ffmpeg,
  ресемплинг), `calls.py` (`CallManager`: `attempt.issued` → `Local/s@trainer-out` с
  `__STU_LOGIN/__ATTEMPT_ID/__RING_TIMEOUT` и `CALLERID(all)`; после ответа — мост + запись
  `storage/recordings/<attempt>.wav`, снуп `spy=in` + ExternalMedia slin16 на UDP-порт бэкенда,
  прогон фраз VAD → STT → `dialog.say` → ответ в мост; причины завершения по cause), `service.py`
  (старт по `TELEPHONY_ENABLED`, подписка на `session-events:*` в Redis, дозвон по
  незавершённым попыткам при старте, прогрев открывающих реплик, `telephony_active()`),
  `settings.py` (раздел `telephony` в `settings`), `router.py`: `GET /me/sip`, `GET /me/call`,
  `POST /attempts/{id}/answer|hangup|no-contact|call-dropped`, `GET /attempts/{id}/recording`,
  `GET/PATCH /admin/settings`.
- `app/dialog/call.py`: переходы звонка (`call.ringing/answered/ended`), отметки, «бросил
  трубку» после второго вопроса (`DROP_AFTER_OPERATOR_TURNS`), общие для SIP и браузера.
  `dialog.service`: `ensure_opening`/`opening_audio` (реплика-открытие с озвучкой, стем
  `opening`), запрет ходов после конца звонка (409 `call_ended`), `call` в `DialogOut`,
  `call_ended` в `TurnResponse`, `audio_source_file`.
- Фронтенд `src/softphone/`: `sip-phone.ts` (JsSIP: регистрация, входящий, `X-Attempt-Id`,
  ответ с выбранным микрофоном, `<audio>` для заявителя, уровень микрофона через
  AnalyserNode, статистика `getStats`), `provider.tsx` (состояния не подключён / готов /
  входящий / разговор / завершён; SIP-режим и режим браузера через MediaRecorder +
  `/utterance`, ввод текстом при недоступном STT, опрос `/me/call` и стенограммы),
  `call-panel.tsx` (плавающая панель: ответить, завершить, «нет контакта», «срыв звонка»,
  уровень и выбор микрофона, последняя реплика, RTT/джиттер), `SoftphoneBadge` в шапке,
  `StudentFrame` в роутере (софтфон живёт и в кабинете, и в АРМ).
- `scripts/issue_call.py` — выдать попытку приёма вызова обучающемуся на стенде (до волны 7);
  `scripts/bench_call.py` — телефон бенча через ARI (`Stasis:bench-<логин>` INUSE → диалплан
  ведёт в приложение `bench`), вопросы Piper в линию A-law, замер до первого звука ответа,
  секция в `PERFORMANCE.md` + `docs/screenshots/wave-06/bench_call.json`, `--from-raw`.
- Модель Silero VAD в `scripts/models.manifest` (`vad/silero_vad.onnx`, 2,2 МБ).
- Тесты: `tests/telephony/` (29: RTP, сегментатор, SIP-учётки и файл эндпоинтов, ARI-клиент
  и `CallManager` на подменном ARI-сервере `fake_ari.py` — originate, события, открывающая
  реплика, фраза через UDP → STT → ответ, срыв заявителем, завершение), `tests/api/
  test_telephony.py` (15: `/me/sip`, `/me/call`, `answer/hangup/no-contact/call-dropped`,
  запись, настройки администратора). e2e `frontend/e2e/softphone.spec.ts`: Chromium с
  фейковым микрофоном (`e2e/fixtures/operator-question.wav`) — регистрация, входящий, ответ,
  открывающая реплика, распознанный вопрос и ответ заявителя, статистика WebRTC, завершение.

### Проверено

- `uv run pytest -q` — 396 зелёных, 3 пропущены (база `trainer_test_wave06`); `ruff` чисто;
  фронт `tsc`, `eslint`, `vitest` (18) чисто.
- Живой стенд `-p wave-06` (профили `ai` + `telephony`): `scripts/bench_call.py --rounds 3` —
  13 ответов из 15, медиана 7,1 с (холодные звонки 10–19 с, тёплый третий звонок 2,8–4,5 с),
  таблица в `docs/PERFORMANCE.md`. Первый звонок медленный из-за озвучки реплик на лету и
  кеша промпта модели; с озвучкой при утверждении (волна 8) остаётся ~STT 1 с + модель 1 с +
  VAD 0,6 с.
- e2e софтфона в Chromium прошёл: `docs/screenshots/wave-06/01-softphone-ready.png … 04-
  ended.png`, `webrtc_stats.json` (Opus, RTT 2 мс, джиттер 0 мс).
- В `storage/recordings/` после звонка лежит WAV моста (обе стороны).

### Осталось

- Ручная проверка «Показать» Вадимом: гарнитура, Chrome, `scripts/issue_call.py --student
  student1`, ответить, спросить «скажите адрес», услышать ответ; видео в
  `docs/screenshots/wave-06/`. Firefox и Яндекс.Браузер — регистрация и звонок руками.
- Настольный IP-телефон — трек K (Константин): логин `phone-<логин>`, пароль из
  `GET /api/me/sip`, порт 5060.
- Волна 7: снять `mode_unavailable` для `call_intake`, панель вызова встроить в карточку
  оператора 112 (сейчас плавает над всеми страницами обучающегося), `sweep_not_notified`
  для приёма вызова, аудит завершения вызова.
- Волна 8: озвучка реплик при утверждении в `STORAGE_DIR/tts/…` (+ `.sln16`), прогрев
  голосов Piper.
- Волна 9: экран настроек телефонии (API уже есть).

### Как проверить

```bash
scripts/fetch_models.sh                                             # + vad/silero_vad.onnx
docker compose --profile ai --profile telephony up -d --build       # .env: TELEPHONY_ENABLED=true, TELEPHONY_EXTERNAL_IP=<IP машины> на Docker Desktop
cd backend && uv run pytest tests/telephony tests/api/test_telephony.py -q
uv run --project backend python scripts/issue_call.py --student student1   # звонок в софтфон student1
uv run --project backend python scripts/bench_call.py --rounds 3 --note "…"
cd frontend && E2E_BASE_URL=https://localhost npx playwright test e2e/softphone.spec.ts
```

## Волна 5 (закрыта)

Волна 5 ([plan/wave-05.md](../plan/wave-05.md)), ветка `wave-05/dialog`, issue #6, PR #21.

### Сделано

- `scripts/fetch_models.sh` + `scripts/models.manifest`: Qwen2.5 1.5B/3B/7B (Q4_K_M),
  faster-whisper base/small, Piper denis/dmitri/irina, e5-small (ONNX); sha256 из LFS,
  докачка, проверка. Папка `models/` (не в git, `MODELS_DIR`) монтируется как `/models`.
- Профиль `ai` в compose: `llm-dialog`, `llm-gen` (llama.cpp server, `--jinja`, слоты,
  `LLM_LOAD_MODE`), `stt` (`deploy/stt/`: faster-whisper за эндпоинтом
  `/v1/audio/transcriptions`). `ffmpeg` в образе бэкенда (MP3). Настройки в `.env.example`.
- `app/providers/llm.py` — клиент llama.cpp (`/v1/chat/completions`, JSON по схеме,
  `cache_prompt`, `id_slot` на разговор, окно недоступности 15 с).
- `app/providers/dialog.py` — `DialogProvider`: `select` (номер по `enum` из id + подстраховка
  ключевыми словами), `generate` (лист фактов, 40 токенов, JSON `{reply, topics}`, защита роли
  в три слоя), `hybrid` (`select` → при `null` генерация, реплика «на утверждение»),
  `buttons` (без модели; в него деградирует любой режим при недоступном сервере), `live` →
  `select` с предупреждением (трек G). `get_dialog_provider(mode)` — по режиму занятия.
- `app/providers/tts.py` — Piper: голоса сценариев `ru_male_1`… → модели и темп (`VOICES`),
  шум по сложности, WAV+MP3, озвучка по предложениям; `NoTTS` — текст без звука.
- `app/providers/stt.py` — HTTP-клиент к `stt` с подсказкой (улицы сценария + термины);
  `NoSTT` — ввод текстом.
- `app/providers/embeddings.py` — e5 через `onnxruntime`+`tokenizers`; пороги 0.85/0.88
  по замеру (DECISIONS).
- `ALLOW_EXTERNAL_AI`: проверка всех адресов (`LLM_DIALOG_URL`, `LLM_GEN_URL`, `STT_URL`)
  на старте, плашка «Внешняя модель: не для закрытого контура» во всех кабинетах
  (`frontend/src/app/shell.tsx`, тест). `GET /api/config` отдаёт `dialog_mode`.
- `app/dialog/` (router, service, schemas) — ход диалога в попытке приёма вызова:
  `GET /attempts/{id}/dialog` (стенограмма, темы: обязательные/выясненные, режим,
  доступность STT/TTS), `POST /attempts/{id}/say` (текст), `/utterance` (аудио → STT →
  как say; без `stt` — 503 «введите текст»), `/ask-topic` (кнопка темы, без модели),
  `GET /api/media/{path}` (озвученные реплики). Первый ход добавляет реплику-открытие
  заявителя и ставит `answered_at`. Повтор с тем же `action_id` возвращает сохранённый ход.
  Каждый ход — событие `dialog.turn` через `append_event`. Озвучка кешируется в
  `STORAGE_DIR/tts/<scenario>/v<N>/r<id>.mp3`. Сгенерированные реплики `hybrid`
  добавляются в `scenario_versions.body.replies` с `approved: false, source: generated`.
- `data/seed/dialog_eval.json` — 100 вопросов оператора по 5 сценариям с допустимыми темами.
- `scripts/bench_dialog_latency.py` — серия из 20 вопросов текстом и голосом (Piper),
  все режимы и кандидаты, судья «не по теме», точность `select`, провокации →
  `docs/PERFORMANCE.md` + `docs/screenshots/wave-05/bench_dialog.json`.
- Ключевые слова тем: «дом» ищется как целое слово (не «домофон»), у `injured` добавлены
  «пострадал», «в сознании», «жив», «дышит» (`detect_topics` понимает пробел в конце
  ключевого слова как «целое слово»).
- Настройки занятия `voice_enabled`, `dialog_mode` в API преподавателя (`SessionIn`,
  `SessionPatch`, `SessionOut`; 422 `bad_dialog_mode`); в форме пока скрыты (волна 7).
  `dialog.turn` в мониторинге → плитка «говорит с заявителем».
- Тесты: `tests/providers/` (72: диалог с подменной моделью, клиент llama.cpp, STT/TTS,
  e5, `ALLOW_EXTERNAL_AI`), `tests/api/test_dialog.py` (9: say/ask-topic/utterance,
  повтор, доступ, hybrid «на утверждение», озвучка и отдача файла, настройки занятия).

### Проверено

- `uv run pytest -q` — 355 зелёных (с файлами организаторов, база `trainer_test_wave05`);
  `ruff` чисто; фронт `tsc`, `eslint`, `vitest` (18) чисто.
- Piper: тёплая озвучка фразы ~250 мс, холодная загрузка голоса ~5 с. Whisper base
  (`deploy/stt`): 4-секундная фраза за 0,8–1,9 с.
- Замеры (`docs/PERFORMANCE.md`, сырые данные `docs/screenshots/wave-05/bench_dialog.json`):
  `select` на 1.5B — медиана 0,95 с текстом (p90 1,2 с), 1,7 с голосом; точность 95% с
  подстраховкой (модель сама 85%), 3B — 86%; провокации 5/5. Судья «не по теме» — 3B
  (7B не помещается в Docker рядом с другими стеками), оговорки в файле.
- «Показать»: `scripts/demo_dialog.py` — вопрос текстом → номер реплики → MP3;
  прогон по сценарию 2-1 в `docs/screenshots/wave-05/demo/` (5 реплик голосом denis).
- Стенд `docker compose -p wave-05 --profile ai up -d llm-dialog stt` (`.env`:
  `LLM_LOAD_MODE=mlock`, порты 8081/9000); на Windows модель 1.5B грузится ~2,5 мин.

### Осталось

- Ручная проверка «Показать» Вадимом: послушать `docs/screenshots/wave-05/demo/*.mp3`,
  прогнать `scripts/demo_dialog.py` со своими вопросами.
- Замер с 7B-судьёй и whisper `small` — на стенде (`PERFORMANCE.md`, раздел «Как
  воспроизвести»; `--from-raw` перерисовывает таблицу без повторного прогона).
- Волна 7: снять `mode_unavailable` для приёма вызова, элементы `voice_enabled` /
  `dialog_mode` в форме занятия, карточка оператора 112, `sweep_not_notified` для
  попыток приёма вызова (сейчас через `norm_seconds` пометит их «Не оповещено»).
- Волна 8: прогрев голосов Piper при старте worker, озвучка при утверждении в
  `STORAGE_DIR/tts/<scenario>/v<N>/r<id>.mp3`, утверждение реплик `hybrid`.

### Как проверить

```bash
scripts/fetch_models.sh                                  # один раз, ~9 ГБ
docker compose -p wave-05 --profile ai up -d --build     # .env: LLM_LOAD_MODE=mlock на Windows
cd backend && uv run pytest tests/providers tests/api/test_dialog.py -q
uv run --project backend python scripts/demo_dialog.py "Что случилось?" "Диктуйте адрес" "Кто-нибудь пострадал?"
uv run --project backend python scripts/bench_dialog_latency.py   --candidate 1.5B=http://localhost:8081 --stt base=http://localhost:9000 --out docs/PERFORMANCE.md
```

## Волна 4 (закрыта)

Волна 4 ([plan/wave-04.md](../plan/wave-04.md)), ветка `wave-04/sessions`, issue #5, PR #20.

### Сделано

- Миграция `0004`: у занятия `cards_per_student` (0 = вся очередь) и `unfinished_seconds`
  (порог «Не завершено», по умолчанию 48 ч). Фоновая проверка теперь ставит и «Не завершено»
  карточке с первичным статусом, открытой дольше порога.
- `app/training/sessions.py` — логика преподавателя: группы (`own_group`, состав),
  проверка настроек занятия (режим «Приём вызова» → 422 до волны 7, диапазоны, справочники,
  веса, чужая группа → 403), подбор очереди (`pick_scenarios`: утверждённые сценарии по
  профилю и группам происшествий, не сложнее занятия, лёгкие раньше), `start_session`
  (фиксирует очередь, `session.started` для всех), `finish_session` (неоткрытые карточки
  снимаются, открытые закрываются и оцениваются, `session.finished`), сводка по занятиям.
- `app/training/report.py` — отчёт (`GET /sessions/{id}/report`: по обучающимся попыток,
  зачтено, средний балл, среднее время до первичного статуса и отклонение от норматива,
  неверные решения, типичные ошибки, грамотность; разворот до попыток; итоги и типичные
  ошибки группы) и снимок мониторинга (`GET /sessions/{id}/monitor`: карточки в работе,
  закрыто, последний балл, средний).
- API преподавателя (`app/training/teacher.py`, роль `teacher`): `GET /students`,
  `GET/POST /groups`, `PATCH /groups/{id}`, `GET/POST /sessions`, `GET/PATCH /sessions/{id}`
  (после старта только название и подсказки, иначе 409), `POST /sessions/{id}/start|finish`,
  `GET /sessions/{id}/monitor|report`. Чужое занятие/группа — 403. Всё пишет аудит.
- `POST /attempts/{id}/progress` (обучающийся) → событие `attempt.progress` без записи в
  попытку; карточка шлёт `viewing` / `editing_status` не чаще раза в 2 с.
- Оценка берёт норматив из занятия, а не из сценария; `GET /me/assignments` сортирует новые
  занятия выше внутри статуса.
- Фронтенд, кабинет преподавателя (`src/pages/teacher/`, хуки `src/api/teacher.ts`):
  - «Занятия» — таблица (дата, занятие, группа, режим, статус, средний балл), на телефоне
    список; «Создать занятие»;
  - форма занятия (`session-form.tsx`, создание и правка черновика): название, режим
    (приём вызова неактивен), группа, группы происшествий и профиль службы (чекбоксы из
    справочников), сложность, карточек на обучающегося, норматив, порог, «Не завершено» в
    часах, подсказки;
  - страница занятия (`session.tsx`): черновик — настройки, предпросмотр очереди, состав
    группы, «Настройки», «Начать занятие»; идёт/завершено — сводка (в работе, закончили,
    средний балл, просрочек), плитки обучающихся (служба, что делает и в какой карточке,
    таймер норматива с цветом, «ещё N в журнале», закрыто/зачтено/средний, последний балл;
    красная рамка при просрочке или «Не оповещено», жёлтая при балле ниже порога), клик
    открывает попытку, плашка «Связь потеряна, переподключаемся…», «Завершить занятие»
    с подтверждением, «Отчёт». Состояние строится из снимка и событий
    (`src/teacher/monitor.ts`, чистый редьюсер, 6 vitest-тестов);
  - отчёт (`session-report.tsx`): итоги, типичные ошибки группы, таблица по обучающимся,
    разворот до попыток (карточка, выдана, балл, время и отклонение, решение против эталона,
    ошибки, ссылка «Разбор»);
  - «Группы» (`groups.tsx`): карточки групп с составом, создание и правка состава из списка
    всех обучающихся;
  - разбор (`attempt-review.tsx`) работает и для преподавателя: ссылки в кабинет, для
    открытой карточки — просмотр (статусы, таймер, карточка) без оценки.
- Обучающийся: журнал незапущенного занятия ждёт старта и выдаёт карточки сам по
  `session.started`; «Мои задания» опрашиваются раз в 5 с, ссылка «Ждать начала в журнале».
- Нагрузка: `python -m app.load_monitor --students 20 --seconds 90 --keep` внутри
  контейнера backend (мок-обучающиеся `load01…load20`, группа «Нагрузочная (мок)»), проверка
  `e2e/monitor-load.spec.ts` с `MONITOR_LOAD=1`.
- Тесты: `tests/api/test_sessions.py` (12: группы, обучающийся не создаёт занятие,
  валидация настроек, предпросмотр очереди и 403 коллеге на всех ручках, PATCH только
  черновика, старт с событием и выдачей карточек, пустая очередь → 422, лимит карточек,
  мониторинг + progress + завершение + отчёт, «Не завершено», норматив занятия). Бэкенд
  всего 274. E2E `frontend/e2e/teacher.spec.ts` (2 сценария, два контекста браузера,
  скриншоты в `docs/screenshots/wave-04/`).

### Проверено

- `uv run pytest -q` — 274 зелёных; `ruff`, `tsc`, `eslint`, `vitest` (16), `build` — чисто.
- Стенд `docker compose -p wave-04 up -d --build` (`.env`: 8443/8080/5433/6380):
  `E2E_BASE_URL=https://localhost:8443 npx playwright test` — `teacher.spec.ts` 2 зелёных
  (карточки у обучающегося < 2 с после старта, действие на плитке < 2 с, завершение, отчёт,
  teacher2 → 403, 390 px), `dds.spec.ts` не тронут, в `wave-00.spec.ts` заголовок кабинета
  преподавателя заменён на «Занятия» (как волна 3 сделала для обучающегося); всё зелёное,
  15 сценариев. E2E преподавателя работает в своей группе (student2, student3), чтобы не
  спорить с `dds.spec.ts` за student1 при параллельном запуске.
- Нагрузка 20 мок-обучающихся × событие в секунду: страница мониторинга отвечает за
  40–110 мс, после обрыва (10 с) догоняет снимок сервера; один тик с 20 одновременными
  оценками занял до 10 с из-за LanguageTool (латентность запроса обучающегося, не
  мониторинга) — учесть в волне 11.

### Осталось

- Ручная проверка «Показать»: репетиция вдвоём (преподаватель на одном ноутбуке,
  обучающийся на другом), три карточки, одна не по профилю (17-1 → «Не принята: передано»),
  одна просрочена, отчёт; уложиться в 5 минут.
- Перед показом пересобрать стенд с чистой базой (`docker compose -p wave-04 down -v` и
  `up -d --build`), чтобы не было тестовых и нагрузочных занятий.
- Экспорт отчёта и изменение оценки — волна 9; адаптивный подбор — волна 10.
- Мелочи волны 1 (`/streets` и «ул.», `%`/`_` в LIKE, `/typical-errors?mode=bogus` → 422)
  по-прежнему не сделаны.

### Как проверить

```bash
docker compose -p wave-04 up -d --build              # .env с портами 8443/8080/5433/6380
cd backend && uv run pytest tests/api/test_sessions.py -q
cd ../frontend && E2E_BASE_URL=https://localhost:8443 npx playwright test e2e/teacher.spec.ts
# Руками: https://localhost:8443 → «Войти как преподаватель» → «Создать занятие» → «Начать»;
# во втором браузере «Войти как обучающийся» → журнал. Нагрузка: см. app/load_monitor.py.
```

## Волна 3 (закрыта)

Ветка `wave-03/dds`, issue #4, PR #19 (`2a211ac`).

### Сделано

- Миграция `0003`: `groups`, `group_members`, `scenarios`, `scenario_versions`,
  `training_sessions`, `attempts`, `evaluations`, `session_events` (модели в
  `app/models/training.py`; состояния попытки и статусы карточки — строки, константы там же).
- Сид (`python -m app.seed`, идемпотентный): группы «Учебная-1» (student1-6, teacher1) и
  «Учебная-2»; сценарии из `data/seed/scenarios/*.json` (ключ — имя файла, изменённое тело =
  новая версия; статус `approved`, если в файле не сказано иное); одно занятие `running`
  «Реагирование на карточку: тренировка ДДС управы» для «Учебной-1»: сложность 3, профиль
  `territorial_oiv`, очередь — три карточки управы (17-1 сигнализация, 2-1 задымление, 2-1
  дубль), норматив 30 с, порог 70.
- `app/training/service.py` — выдача карточек (по сложности: 1-2 → одна, 3 → три сразу;
  номер карточки из сценария, иначе `3826xxxx`), «Получена службой» при открытии, статусы
  через `app.domain.evaluation.status_machine` (недопустимый переход, закрытая карточка,
  обязательный комментарий и наряд, служба 103 — 422 с текстом), идемпотентность по
  `action_id`, статус карточки (`registered` / `not_notified` / `refused` / `finished`),
  «Завершить работу с карточкой», оценка при финальном статусе или завершении
  (`evaluate_attempt(body, attempt, weights=session.weights, pass_threshold=…)` →
  `evaluations` + `attempts.result`, состояние `evaluated`), сразу следующая карточка из
  очереди.
- `app/training/sweeper.py` — фоновая проверка каждые 5 с: карточка без первичного статуса
  дольше норматива получает «Не оповещено» (`SKIP LOCKED`, безопасно для нескольких реплик).
- События (`app/events.py`): `session_events` с плотным `seq` на занятие (advisory lock),
  публикация в Redis; WebSocket `/ws/sessions/{id}?after_seq=N` (`app/training/ws.py`):
  токен первым сообщением, догон пропущенного из таблицы, `ready`, живые события; обучающийся
  видит общие и свои, преподаватель — все. Типы: `attempt.issued`, `attempt.received`,
  `attempt.status_changed`, `card.status_changed`, `attempt.submitted`, `attempt.evaluated`.
- API (`app/training/router.py`): `GET /me/assignments`, `GET /sessions/{id}/journal`
  (выдаёт очередные карточки, постранично 10/20/50, преподаватель — `student_id=`),
  `GET /attempts/{id}` (карточка, история, допустимые переходы, причины отказа; эталон и
  оценка — после закрытия), `POST /attempts/{id}/open|status|finish`,
  `POST /sessions/{id}/restart` (только `DEMO_MODE=true`: сброс своих карточек занятия).
  `GET /reference/search?q=` — поиск по памятке (`app/domain/memo_search.py`) и
  классификатору для «Справочника».
- Фронтенд, эмулятор АРМ-112 (`frontend/src/emulator/`, палитра `.arm` в `index.css`, Roboto):
  - журнал (`journal-page.tsx`, по скриншоту стр. 12): поиск, дата и часы, оператор и АРМ,
    «автообновление» (выкл. — счётчик пропущенных обновлений на «уведомлениях»), неактивная
    «создать новую карточку», таблица с колонками скриншота, раскрытие строки (службы,
    заявитель, информация, описание), красные статусы карточки, постраничность, учебный
    таймер принятия (обычный → жёлтый с 80 % → красный со значком), строка открывается
    щелчком или Enter;
  - карточка ДДС (`card-page.tsx`, стр. 23-24): панель вызова и телефоны, «Происшествие №»,
    заявитель, адрес, описание, флаги, ЧС/ЧП, группа, признаки, «Класс.:», полоса «Службы» со
    вкладками, синяя панель истории своей службы, карандаш → строка статуса (только
    допустимые статусы, «Номер наряда», причина отказа для «Не принята», комментарий, ✓ ✕),
    горячие клавиши `Alt+A`, `Alt+R`, `Ctrl+Enter`, `Esc`, `?`; черновик строки в
    `localStorage`; при обрыве сети действие повторяется с тем же `action_id`;
  - учебная панель справа: норматив и отставание, статус службы, подсказки (выключаются),
    «Завершить работу с карточкой», после закрытия — балл, «Зачтено / Не зачтено», ссылки
    «Открыть разбор» и «Следующая карточка», состояние связи;
  - `ws.ts` — подключение с `after_seq`, переподключение через 1, 2, 4, 8, 10 с, обновление
    токена по коду 4401.
- Разбор (`pages/attempt-review.tsx`, в кабинете): итог и вердикт, составляющие полосами,
  решение и цепочка статусов против эталона (пропущенные и лишние), сработавшие ошибки с
  объяснением и ссылкой «Памятка, стр. …» в справочник, комментарии с подчёркнутыми
  находками LanguageTool, методы.
- Кабинет обучающегося: «Мои задания» (активное занятие крупно, кнопка в журнал),
  «Справочник» (поиск по памятке и классификатору, подсветка слов, `?q=` из разбора).
- Тесты: `tests/api/test_attempts.py` (16: задания, выдача по сложности, постраничность,
  открытие, полная цепочка с оценкой ≥ 90 и следующей карточкой, «Не принята» без
  комментария → 422 и с причиной → «Отказ», недопустимый и системный статус, наряд
  обязателен, идемпотентность, «Не оповещено» и поздняя «Принята», завершение без статуса →
  `no_status`, права (чужой обучающийся 404, преподаватель читает, но не ставит), плотность и
  адресность событий, поиск по справочнику, сброс в демо-режиме), `tests/api/test_ws.py` (2:
  догон + живые события через настоящий uvicorn, коды 4401/4404). Бэкенд всего 262.
- E2E `frontend/e2e/dds.spec.ts` (5 сценариев по разделу «Проверка», скриншоты в
  `docs/screenshots/wave-03/`).

### Проверено

- `uv run pytest -q` — 262 зелёных (с файлами организаторов в `data/organizers/`).
- `ruff`, `tsc`, `eslint`, `vitest` — чисто.
- Стенд `docker compose -p wave-03 up -d --build` на портах 8443/8080 (`.env`: 5433/6380):
  сид создаёт группы, 10 сценариев и занятие; `E2E_BASE_URL=https://localhost:8443
  npx playwright test e2e/dds.spec.ts` — см. «Осталось».

### Осталось

- Разбор в стиле кабинета, а не эмулятора; шкала времени статусов против эталона (P2)
  сделана списком, а не осью.
- «Не завершено» (48 ч) — сделано в волне 4 вместе с настройками занятия.

### Как проверить

```bash
docker compose -p wave-03 up -d --build              # .env с портами 8443/8080/5433/6380
cd backend && uv run pytest tests/api tests/domain -q
cd ../frontend && E2E_BASE_URL=https://localhost:8443 npx playwright test e2e/dds.spec.ts
# Руками: https://localhost:8443 → «Войти как обучающийся» → «Открыть журнал АРМ-112».
# Начать заново: POST /api/sessions/{id}/restart (DEMO_MODE=true) или тест сам сбрасывает.
```

## Волна 2 (закрыта)

Волна 2 ([plan/wave-02.md](../plan/wave-02.md)), ветка `wave-02/evaluation`, issue #3.
Все задачи сделаны, проверки зелёные, ждёт ручной проверки раздела «Показать» и PR.

### Сделано

- Пакет `backend/app/domain/evaluation/` — движки оценки обоих режимов, чистые функции без
  базы и сети:
  - `schemas.py` — Pydantic-модели тел сценариев PRD 9.2/9.3 (`CardResponseScenario`,
    `CallIntakeScenario`) и входа попытки (`CardResponseAttempt` со `status_log`,
    `CallIntakeAttempt` с карточкой и `dialog`); `parse_scenario(body)`.
  - `result.py` — результат `total`, `passed`, `components{score,max,status,items}`,
    `errors[]`, `methods{}`; веса занятия (`apply_weights`, сумма 100, вес 0 отключает
    составляющую); перенормировка при `not_checked`/`disabled`; `passed` = порог **и** нет
    критичных ошибок из `reference.critical_errors`.
  - `text.py` — нормализация (регистр, `ё→е`, пунктуация), типы улиц (`ул.`/`улица`,
    `пр-т`, `пер.`, `ш.`, `б-р`…), `street_ratio` (rapidfuzz), дом (`д. 21А` → `21а`),
    ключевые слова с допуском на словоформы, темы разговора по `caller_topics.keywords`.
  - `timing.py` — время: ≤ норматив → максимум, ≥ 2×норматив → 0, линейно между.
  - `status_machine.py` — `allowed_next`, `validate_transition` (понятная ошибка по-русски:
    недопустимый переход, закрытая карточка, обязательный комментарий/наряд, служба 103 без
    «Не принята»/«Отказ»), `check_log` — прогон журнала статусов.
  - `detectors.py` — 11 детекторов `card_response` (`no_status`, `late_primary`,
    `status_mismatch`, `competence_refusal`, `profile_refusal`, `empty_reject_comment`,
    `incomplete_comment`, `progress_missing`, `duplicate_accepted`, `wrong_accept_unfixed`,
    `wrong_final_status`) и 3 детектора `call_intake` (`address_not_asked`,
    `no_call_dropped_mark`, `region_not_clarified`); каждый — именованная функция с
    объяснением для обучающегося.
  - `card_response.py` — решение 30, время 20, цепочка статусов 20 (наибольшая общая
    подпоследовательность с эталоном), комментарии и наряд 15, типичные ошибки 10,
    грамотность 5.
  - `call_intake.py` — опросная карта 25, признаки и службы 10, адрес 15, обязательные
    вопросы 15, описание 10, время 10, типичные ошибки 5, грамотность 10.
  - `__init__.py` — `evaluate_sync(body, attempt, …)` по словарям и `evaluate_attempt(...)`
    (async: сам зовёт `GrammarProvider` и `EmbeddingProvider`) для эндпоинтов волн 3 и 7.
- `data/seed/scenarios/` — 10 эталонных сценариев из билетов (5 `card_*` + 5 `call_*`):
  2-1 задымление мусоропровода (+ дубль), 17-1 пожарная сигнализация (отказ с передачей в УК),
  31-3 свист газовой трубы (Мосгаз), 5-2 отравление (служба 103); приём вызова: 2-1, 1-3
  ребёнок в Волжском (регион), 2-2 скандал со срывом звонка, 30-2 наезд на пешехода,
  31-3 газовая труба. `expected_services` посчитаны `resolve_services`.
- Зависимость `rapidfuzz`; в pytest включён вывод русских id тестов без экранирования.
- Тесты `backend/tests/domain/evaluation/` (163, без базы — `tests/domain/conftest.py`):
  `test_text.py` (нормализация, улицы «ул. Берзарина» = «улица Берзарина», «Берзарино» ≠,
  темы), `test_status_machine.py` (переходы, 103, `check_log`), `test_detectors.py` (каждый
  детектор срабатывает и молчит на примерах памятки стр. 28-32), `test_card_response.py`
  (идеальная попытка 100, пример PRD 9.2, «Не принята» без комментария, пропущенный и лишний
  статус, недопустимый переход, 30/45/60 с → 20/10/0, LanguageTool недоступен →
  перенормировка, веса, порог, 103), `test_call_intake.py` (100, опросная карта по уровням,
  признаки и службы, адрес по частям, темы по тексту, описание, 60/90/135/180 с, веса),
  `test_fixtures.py` (10 сценариев согласованы с классификатором и памяткой, идеальная попытка
  → 100 для каждого), `test_evaluate_attempt.py` (обёртка с LanguageTool через mock),
  `test_speed.py` (оценка < 300 мс; факт — около 1 мс).

### Проверено

- `pytest backend/tests/domain/evaluation -q` — 163 зелёных; `test_speed.py` печатает худшее
  из 20 прогонов: ~1 мс на попытку.
- Полный бэкенд `uv run pytest -q` с файлами организаторов в `data/organizers/` — 246 зелёных
  (на отдельной базе `trainer_test_wave02`: общая `trainer_test` уже мигрирована чужой веткой
  до ревизии `0003`, которой в `main` нет — см. «Как проверить»).
- `ruff check` и `ruff format --check` — чисто.
- `docs/screenshots/wave-02/`: `pytest_evaluation.txt` (вывод `pytest -v`),
  `evaluation_example_card_response.json` (диспетчер опоздал, пропустил «Прибытие», без
  комментария к «Проведение работ», опечатка: 74 балла, не сдано из-за критичных
  `late_primary` и `progress_missing`), `evaluation_example_call_intake.json` (оператор не
  уточнил регион у ребёнка в Волжском, ошибся типом, не спросил телефон: 65 баллов).

### Осталось

- Ручная проверка «Показать»: посмотреть `pytest_evaluation.txt` и два JSON в
  `docs/screenshots/wave-02/`, убедиться, что объяснения по составляющим понятны.
- Смысловая близость пока TF-IDF (пороги 0.30/0.70); e5 включается в волне 5 через
  `EMBEDDING_MODEL_DIR`, движок менять не нужно.

### Как проверить

```bash
cd backend
uv sync --group dev
uv run pytest tests/domain/evaluation -q                 # без базы, ~1 с
uv run pytest tests/domain/evaluation/test_speed.py -s   # печатает время оценки
uv run ruff check . && uv run ruff format --check .
# Весь бэкенд (нужен PostgreSQL на localhost:5432; если общая trainer_test мигрирована
# чужой веткой дальше main, взять свою базу):
TEST_DATABASE_ADMIN_URL=postgresql+asyncpg://trainer:trainer-dev-password@localhost:5432/trainer_test_wave02 TEST_DATABASE_URL=postgresql+asyncpg://trainer_app:trainer-app-dev-password@localhost:5432/trainer_test_wave02 uv run pytest -q
```

## Закрытые волны

| Волна | Закрыта | Коммит | Кто проверил |
|---|---|---|---|
| 0 | 16.09.2026 | `9453fe4` (PR #16) | Вадим |
| 1 | 16.09.2026 | `f6e2b1d` (PR #17) | Вадим |
| 2 | 16.09.2026 | `0de21e3` (PR #18) | Вадим |
| 3 | 16.09.2026 | `2a211ac` (PR #19) | Вадим |
| 4 | 16.09.2026 | `d402b84` (PR #20) | Вадим |
| 5 | 16.09.2026 | `bedaa2d` (PR #21) | Вадим |
| 6 | 17.09.2026 | `656680d` (PR #22) | Вадим |

## Открытые вопросы и блокеры

См. [BLOCKERS.md](BLOCKERS.md) и [DECISIONS.md](DECISIONS.md).
