# Прогресс

Ведётся агентом и командой. Новая сессия читает этот файл первым.

## Текущая волна

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

## Открытые вопросы и блокеры

См. [BLOCKERS.md](BLOCKERS.md) и [DECISIONS.md](DECISIONS.md).
