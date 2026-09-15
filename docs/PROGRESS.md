# Прогресс

Ведётся агентом и командой. Новая сессия читает этот файл первым.

## Текущая волна

Волна 1 ([plan/wave-01.md](../plan/wave-01.md)), ветка `wave-01/dataset`, issue #2.
Все задачи сделаны, проверки зелёные, ждёт ручной проверки раздела «Показать» и PR.

### Сделано

- Разбор датасета: [DATASET.md](DATASET.md) — по каждому файлу структура, объём, что куда
  ложится, что не разобрано. Скриншоты АРМ-112 из памятки в `docs/reference/arm112/`
  (страницы 12, 13, 15, 16, 17, 23, 24).
- Классификатор: программный разбор трёхуровневой шапки (`app/importers/classifier.py`):
  24 группы, 1283 итоговых типа, 86 колонок служб → 64 службы и 16 признаков, неразобранных
  колонок 0. Результат в `data/seed/classifier.json` (коммитится; стенд без файлов организаторов
  загружает его).
- Билеты: OCR (`app/importers/tickets_ocr.py`, Tesseract `rus`, ячейки таблицы по отдельности)
  → `data/seed/tickets.json`, 96 ситуаций, все сверены со сканами; 32 ручные правки в
  `data/seed/tickets_corrections.json` применяются после OCR, неуверенных строк 0.
- Памятка: `data/seed/memo.txt` (`app/importers/memo_extract.py`); статусы реагирования (9),
  статусы карточки (7), причины отказа (7), типичные ошибки (11 + 3), темы заявителя (14) в
  `app/domain/reference_data.py` с ссылками на страницы памятки.
- Миграция `0002`: `services`, `incident_groups`, `incident_types`, `incident_flags`,
  `response_statuses`, `card_statuses`, `reject_reasons`, `typical_errors`, `caller_topics`,
  `tickets`, `streets`.
- Импорт `python -m app.importers.organizers` (идемпотентный, upsert по коду, печатает сводку);
  выполняется и при старте бэкенда (`entrypoint.sh`). Папка `data/` смонтирована в контейнеры
  как `/data` (`DATA_DIR`).
- `resolve_services(rules, flags)` (`app/domain/services.py`) — чистая функция; табличный тест на
  30 строках xlsx с признаками (пострадавшие, нет доступа, угроза людям, правонарушение, не на
  месте, перекрытие, тоннель, газификация…) и на всех 1283 строках без признаков сверяет
  результат с сырыми ячейками по независимой карте колонок.
- Улицы Москвы: `app/importers/streets_osm.py` (OpenStreetMap, ODbL) → `data/seed/streets.json`:
  6249 записей, 4924 названия, 12 округов, 132 района и поселения; две улицы из билетов добавлены
  вручную (`streets_manual.json`). Все улицы из билетов есть.
- API справочников (`app/routers/reference.py`, для всех вошедших): `GET /classifier/tree`,
  `/classifier/{code}/services?flags=`, `/incident-flags`, `/services`, `/response-statuses`,
  `/card-statuses`, `/reject-reasons`, `/caller-topics`, `/typical-errors?mode=`, `/tickets`,
  `/streets?q=` (по началу любого слова, без учёта регистра и «ё»). `POST /grammar/check`
  (преподаватель, администратор).
- Провайдеры: `GrammarProvider` (LanguageTool; при недоступности `method="unavailable"`,
  повторная проверка через 30 с) и `EmbeddingProvider` (e5-small из `EMBEDDING_MODEL_DIR`, иначе
  TF-IDF без модели), у обоих `method` и пороги в ответе.
- Типы фронтенда перегенерированы (`npm run gen:api`).
- Тесты: `tests/test_resolve_services.py` (37), `tests/importers/test_organizers.py` (10: сводка,
  идемпотентность, дерево, службы по признакам, статусы дословно, билеты, улица Берзарина),
  `tests/test_providers.py` (8). Всего бэкенд 83.

### Проверено

- `docker compose up -d --build` (стенд на портах 8443/8080 рядом со стендом волны 0) → все
  сервисы healthy; `docker compose exec backend python -m app.importers.organizers` печатает
  сводку: 24 группы, 1283 типа, 16 признаков, 64 службы, 9 статусов, 7 статусов карточки, 96
  ситуаций, 6251 улица.
- Через nginx: `/api/classifier/tree` (1283 типа, дерево кнопок), `/api/classifier/1.1.1.1/services?flags=injured`
  добавляет 102, 103 и ЦЭМП; `/api/streets?q=берзар` → улица Берзарина, СЗАО, Хорошёво-Мнёвники;
  `/api/grammar/check` через LanguageTool находит «завершины» → «завершены»; обучающемуся 403.
- `scripts/check.sh`: ruff, pytest (83), tsc, eslint, vitest (10), build, playwright (8) — зелёные.

### Осталось

- Ручная проверка «Показать»: открыть 5 случайных ситуаций из `data/seed/tickets.json` и сверить
  со сканом; посмотреть `docs/DATASET.md` и вывод импорта.
- Скриншот вывода импорта в `docs/screenshots/wave-01/`.

### Как проверить

```bash
docker compose up -d --build
docker compose exec backend python -m app.importers.organizers
cd backend && uv run pytest tests/importers tests/test_resolve_services.py -q
curl -sk https://localhost/api/classifier/tree | head -c 500      # нужен Bearer-токен, см. /api/docs
```

## Закрытые волны

| Волна | Закрыта | Коммит | Кто проверил |
|---|---|---|---|
| 0 | 16.09.2026 | `9453fe4` (PR #16) | Вадим |

## Открытые вопросы и блокеры

См. [BLOCKERS.md](BLOCKERS.md) и [DECISIONS.md](DECISIONS.md).
