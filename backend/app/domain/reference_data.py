"""Reference data taken from the organizers' memo «Работа на АРМ-112» (data/seed/memo.txt).

Titles are quoted verbatim from the memo; page numbers point to it. Codes are ours and are
used by the evaluation engines, so they never change once released.
"""

from __future__ import annotations

# Response statuses of a service (memo, pages 21-23). Order = position in the drop-down.
RESPONSE_STATUSES: list[dict] = [
    {
        "code": "added",
        "title": "Добавлена",
        "order": 1,
        "is_system": True,
        "is_primary": False,
        "is_final": False,
        "requires_comment": False,
        "requires_order_number": False,
        "allowed_next": ["received", "accepted", "rejected"],
        "description": "Технический статус. Проставляется системой автоматически при сохранении "
        "карточки с оповещением службы. С этого момента идёт отсчёт 30 секунд.",
        "memo_page": 21,
    },
    {
        "code": "received",
        "title": "Получена службой",
        "order": 2,
        "is_system": True,
        "is_primary": False,
        "is_final": False,
        "requires_comment": False,
        "requires_order_number": False,
        "allowed_next": ["accepted", "rejected"],
        "description": "Технический статус. Проставляется системой автоматически при открытии "
        "карточки диспетчером на АРМ-112.",
        "memo_page": 21,
    },
    {
        "code": "accepted",
        "title": "Принята",
        "order": 3,
        "is_system": False,
        "is_primary": True,
        "is_final": False,
        "requires_comment": False,
        "requires_order_number": False,
        # The memo (p. 25) lists all five follow-up statuses after «Принята»; the live АРМ-112
        # (screenshots of 17.09.2026) offers them one step at a time. We follow the live system.
        "allowed_next": ["response_started", "works_done", "works_refused"],
        "description": "Диспетчер подтверждает факт приёма информации. Реагирование будет "
        "осуществляться. Проставляется в течение 30 секунд после направления карточки в службу.",
        "memo_page": 21,
    },
    {
        "code": "rejected",
        "title": "Не принята",
        "order": 4,
        "is_system": False,
        "is_primary": True,
        "is_final": False,
        "requires_comment": True,
        "requires_order_number": False,
        "allowed_next": ["accepted"],
        "description": "Реагирование не будет осуществляться. Обязателен комментарий с причиной "
        "отказа и данными о передаче информации в другие службы. Далее доступна только «Принята».",
        "memo_page": 21,
    },
    {
        "code": "response_started",
        "title": "Начало реагирования",
        "order": 5,
        "is_system": False,
        "is_primary": False,
        "is_final": False,
        "requires_comment": False,
        "requires_order_number": True,
        "allowed_next": ["arrived", "works_done", "works_refused"],
        "description": "Выезд сил и средств реагирования на место происшествия. Указывается номер "
        "наряда.",
        "memo_page": 22,
    },
    {
        "code": "arrived",
        "title": "Прибытие",
        "order": 6,
        "is_system": False,
        "is_primary": False,
        "is_final": False,
        "requires_comment": False,
        "requires_order_number": False,
        "allowed_next": ["works_started", "works_done", "works_refused"],
        "description": "Прибытие сил и средств реагирования на место происшествия.",
        "memo_page": 22,
    },
    {
        "code": "works_started",
        "title": "Проведение работ",
        "order": 7,
        "is_system": False,
        "is_primary": False,
        "is_final": False,
        "requires_comment": False,
        "requires_order_number": False,
        "allowed_next": ["works_done", "works_refused"],
        "description": "Проведение аварийно-восстановительных работ на месте происшествия.",
        "memo_page": 22,
    },
    {
        "code": "works_done",
        "title": "Работы завершены",
        "order": 8,
        "is_system": False,
        "is_primary": False,
        "is_final": True,
        "requires_comment": True,
        "requires_order_number": False,
        "allowed_next": [],
        "description": "Завершение работ. Перед сохранением результаты реагирования отражаются "
        "в комментарии: сохранение статуса закрывает карточку для редактирования.",
        "memo_page": 22,
    },
    {
        "code": "works_refused",
        "title": "Отказ от выполнения работ",
        "order": 9,
        "is_system": False,
        "is_primary": False,
        "is_final": True,
        "requires_comment": True,
        "requires_order_number": False,
        "allowed_next": [],
        "description": "Реагирование начато, но работы на месте не проводились. Обязателен "
        "комментарий с причиной отказа и данными о передаче информации. Закрывает карточку.",
        "memo_page": 22,
    },
]

# Card statuses (memo, pages 27-28). is_alert = red indication in the journal.
CARD_STATUSES: list[dict] = [
    {"code": "registered", "title": "Зарегистрирована", "is_alert": False, "order": 1},
    {"code": "processed", "title": "Отработана", "is_alert": False, "order": 2},
    {"code": "verified", "title": "Проверена", "is_alert": False, "order": 3},
    {"code": "not_notified", "title": "Не оповещено", "is_alert": True, "order": 4},
    {"code": "refused", "title": "Отказ", "is_alert": True, "order": 5},
    {"code": "not_finished", "title": "Не завершено", "is_alert": True, "order": 6},
    {"code": "finished", "title": "Завершена", "is_alert": False, "order": 7},
]

# Reasons for «Не принята» / «Отказ от выполнения работ» as they are phrased in the memo
# examples (pages 28-30) and in the definition of «Не принята» (page 21).
REJECT_REASONS: list[dict] = [
    {"code": "not_our_territory", "title": "Не обслуживаем территорию", "order": 1},
    {"code": "not_our_object", "title": "Не обслуживаем объект", "order": 2},
    {"code": "not_in_competence", "title": "Не в компетенции службы", "order": 3},
    {"code": "duplicate", "title": "Дубль", "order": 4},
    {"code": "other_card", "title": "Реагирование по другой карточке", "order": 5},
    {"code": "transferred", "title": "Информация передана в другую службу", "order": 6},
    {"code": "no_contract", "title": "Нет договора с обслуживающей организацией", "order": 7},
]

# Typical violations (memo, pages 26 and 28-31; PRD section 9.1). Each code is a detector in
# the evaluation engine; penalty is subtracted from the 10-point «typical errors» component.
TYPICAL_ERRORS: list[dict] = [
    {
        "code": "no_status",
        "title": "Отсутствует статус реагирования",
        "description": "Карточка направлена в службу, а статус «Принята» или «Не принята» так и не "
        "проставлен. Карточка переходит в статус «Не оповещено».",
        "mode": "card_response",
        "penalty": 10,
        "memo_ref": "стр. 28, пример 1",
        "example": "Отсутствует статус реагирования.",
    },
    {
        "code": "late_primary",
        "title": "Первичный статус проставлен позже норматива",
        "description": "«Принята» или «Не принята» проставлены позже 30 секунд после направления "
        "карточки в службу.",
        "mode": "card_response",
        "penalty": 4,
        "memo_ref": "стр. 21, 26 (своевременность)",
        "example": "Диспетчер должен подтвердить получение сообщения через 30 секунд после его "
        "направления в службу.",
    },
    {
        "code": "status_mismatch",
        "title": "Статус не соответствует фактическому реагированию",
        "description": "«Принята» при отсутствии реагирования или «Не принята», когда реагирование "
        "ведётся. Некорректный статус вводит в заблуждение другие службы.",
        "mode": "card_response",
        "penalty": 6,
        "memo_ref": "стр. 26, 28-29, пример 2",
        "example": "Повреждение дорожного покрытия. ДДС района проставлен статус «Принята: не "
        "обслуживаем территорию». Следовало проставить статус «Не принята: не обслуживаем "
        "территорию».",
    },
    {
        "code": "competence_refusal",
        "title": "Отказ из-за реагирования другой службы",
        "description": "Служба отказывается от обработки информации только потому, что на "
        "происшествие уже реагирует другая служба.",
        "mode": "card_response",
        "penalty": 6,
        "memo_ref": "стр. 26, 29, пример 3",
        "example": "Вскрыт чердак жилого дома. ДДС района проставлен статус «Не принята: в "
        "компетенции 102». После звонка отдела контроля и разъяснений информация принята.",
    },
    {
        "code": "profile_refusal",
        "title": "Отказ от профильного происшествия",
        "description": "«Не принята» по происшествию, которое входит в зону ответственности "
        "службы.",
        "mode": "card_response",
        "penalty": 8,
        "memo_ref": "стр. 29, пример 3",
        "example": "Посторонние граждане в подвале жилого дома. ДДС района проставлен статус «Не "
        "принята» без комментариев. После звонка отдела контроля информация принята.",
    },
    {
        "code": "empty_reject_comment",
        "title": "Нет комментария к «Не принята» или «Отказ от выполнения работ»",
        "description": "Отказ без комментария: не указана причина и кому передана информация.",
        "mode": "card_response",
        "penalty": 6,
        "memo_ref": "стр. 30, пример 4",
        "example": "Сработала пожарная сигнализация в жилом доме. ДДС района проставлен статус «Не "
        "принята» без комментариев. Дом обслуживает УК «ПИК», информация передана в их "
        "диспетчерскую — это следовало указать в комментарии.",
    },
    {
        "code": "incomplete_comment",
        "title": "Неполный комментарий к отказу",
        "description": "В комментарии есть причина отказа, но нет данных о передаче информации "
        "(куда передано, что сделано).",
        "mode": "card_response",
        "penalty": 3,
        "memo_ref": "стр. 30, пример 5",
        "example": "Застревание в лифте. Службой «Мослифт» и ДДС района проставлены статусы «Не "
        "принята: не обслуживаем». Информация передана в диспетчерскую «Практика» — это "
        "следовало указать в комментарии.",
    },
    {
        "code": "progress_missing",
        "title": "Нет статусов хода работ или комментариев к ним",
        "description": "Пропущены «Начало реагирования», «Прибытие», «Проведение работ» либо они "
        "проставлены без комментариев.",
        "mode": "card_response",
        "penalty": 4,
        "memo_ref": "стр. 31, пример 6",
        "example": "Прорыв трубы с горячей водой в жилом доме. В первой карточке «Принята», в "
        "последующих «Не принята: дубль». Работы ведутся, но статусы хода работ не вносились.",
    },
    {
        "code": "duplicate_accepted",
        "title": "«Принята» вместо «Не принята: дубль»",
        "description": "Повторная карточка того же происшествия принята, хотя реагирование идёт "
        "по другой карточке.",
        "mode": "card_response",
        "penalty": 4,
        "memo_ref": "стр. 30, пример 4; стр. 31, пример 6",
        "example": "Задымление в подъезде жилого дома. В системе есть карточка с таким же "
        "адресом и "
        "типом, созданная на 2 минуты раньше Службой 101. Следовало проставить «Не принята: "
        "дубль» или «Не принята: реагирование по КП (номер карточки)».",
    },
    {
        "code": "wrong_accept_unfixed",
        "title": "Ошибочная «Принята» не исправлена",
        "description": "«Принята» проставлена ошибочно, реагирования не будет, а «Отказ от "
        "выполнения работ» с комментарием так и не проставлен.",
        "mode": "card_response",
        "penalty": 6,
        "memo_ref": "стр. 32 («Что делать если…»)",
        "example": "Ошибочно проставили статус «Принята», реагирование не будет осуществляться: "
        "проставить статус «Отказ от выполнения работ» с комментарием.",
    },
    {
        "code": "wrong_final_status",
        "title": "«Работы завершены» вместо «Отказ от выполнения работ»",
        "description": "Работы не проводились, но карточка закрыта статусом «Работы завершены».",
        "mode": "card_response",
        "penalty": 4,
        "memo_ref": "стр. 29, пример 2",
        "example": "Осиное гнездо на фасаде. Проставлен статус «Работы завершены: в ДДС нет "
        "договора "
        "с организацией, которая удаляет осиные гнёзда». Следовало проставить «Отказ от "
        "выполнения работ» с тем же комментарием.",
    },
    {
        "code": "error_missed",
        "title": "Ошибка в данных карточки не замечена",
        "description": "Оператор 112 ошибся в карточке (адрес, тип, пострадавшие, службы), а "
        "диспетчер принял данные как есть и не отметил ошибку.",
        "mode": "card_response",
        "penalty": 6,
        "memo_ref": "ответ заказчика 20.09.2026 (диспетчер проверяет карточку)",
        "example": "В карточке дом 44, а по описанию заявитель называет дом 42. Наряд поедет "
        "не туда — ошибку следовало отметить и исправить.",
    },
    {
        "code": "false_alarm",
        "title": "Верное поле отмечено как ошибочное",
        "description": "Диспетчер отметил ошибку в поле, которое заполнено правильно.",
        "mode": "card_response",
        "penalty": 3,
        "memo_ref": "ответ заказчика 20.09.2026 (диспетчер проверяет карточку)",
        "example": "Адрес в карточке совпадает с описанием, но диспетчер отметил дом как "
        "неверный: лишняя правка задерживает реагирование.",
    },
    {
        "code": "service_not_informed",
        "title": "Служба не оповещена по телефону",
        "description": "Карточка принята, а дежурному своей службы диспетчер так и не "
        "позвонил: информация о происшествии до бригады не дошла.",
        "mode": "card_response",
        "penalty": 6,
        "memo_ref": "ответ заказчика 20.09.2026 («диспетчер сам звонит руководителям служб»)",
        "example": "Нет отопления в доме, карточка принята, наряд проставлен, но дежурному "
        "тепловых сетей никто не позвонил — бригада не выехала.",
    },
    {
        "code": "status_before_report",
        "title": "Статус хода работ проставлен до доклада бригады",
        "description": "«Начало реагирования», «Прибытие», «Проведение работ» или «Работы "
        "завершены» проставлены раньше, чем старший наряда доложил об этом по телефону: "
        "статус не отражает фактическое реагирование.",
        "mode": "card_response",
        "penalty": 2,
        "memo_ref": "стр. 22, 26 (по факту получения информации); ответ заказчика 21.09.2026",
        "example": "Карточка принята и сразу проставлены «Прибытие» и «Работы завершены», "
        "хотя бригада ещё не выехала.",
    },
    {
        "code": "report_not_reflected",
        "title": "Доклад бригады не отражён статусом",
        "description": "Старший наряда доложил о выезде, прибытии, работах или их завершении, "
        "а соответствующий статус в карточке так и не проставлен (или проставлен позже "
        "норматива на отражение доклада).",
        "mode": "card_response",
        "penalty": 2,
        "memo_ref": "стр. 22, 31 (пример 6); ответ заказчика 21.09.2026",
        "example": "Бригада доложила «на месте», диспетчер продолжил работу без «Прибытия» — "
        "другие службы и заявитель не видят, что реагирование идёт.",
    },
    {
        "code": "report_not_taken",
        "title": "Доклад бригады не принят",
        "description": "Старший наряда звонил диспетчеру с доклада о ходе реагирования, "
        "а трубку никто не взял: информация о выезде, прибытии или работах в службу "
        "не поступила.",
        "mode": "card_response",
        "penalty": 3,
        "memo_ref": "стр. 31, нарушение 7 (не обеспечена оперативная связь с АРМ-112)",
        "example": "Бригада доложила о прибытии, диспетчер не ответил на звонок — ход "
        "реагирования в карточке не отражён, отдел контроля дозвониться не может.",
    },
    {
        "code": "address_not_asked",
        "title": "Адрес не уточнён до отбоя",
        "description": "Разговор завершён, а точный адрес происшествия не выяснен.",
        "mode": "call_intake",
        "penalty": 3,
        "memo_ref": "стр. 18-19",
        "example": "Заявитель назвал ориентир «в доме через дорогу», точный адрес не уточнён.",
    },
    {
        "code": "no_call_dropped_mark",
        "title": "Нет отметки «срыв звонка»",
        "description": "Заявитель бросил трубку, но в карточке нет отметки о срыве звонка.",
        "mode": "call_intake",
        "penalty": 2,
        "memo_ref": "билеты, ситуация 2-2",
        "example": "Поругался с продавцом «Мегафон», бросил трубку.",
    },
    {
        "code": "questions_not_asked",
        "title": "Обязательные вопросы не заданы",
        "description": "Обучающийся сам задал меньше половины обязательных вопросов заявителю "
        "(порог задаётся в сценарии). Критическая ошибка: незачёт при любом балле.",
        "mode": "call_intake",
        "penalty": 3,
        "memo_ref": "стр. 18-19",
        "example": "Оператор сказал «алло, ждите» и оформил карточку по тому, что заявитель "
        "рассказал сам.",
    },
    {
        "code": "card_empty",
        "title": "Карточка не заполнена",
        "description": "Ни тип происшествия, ни адрес, ни описание не внесены. Критическая "
        "ошибка: незачёт при любом балле; описание, грамотность, признаки и службы, время "
        "оцениваются в 0.",
        "mode": "call_intake",
        "penalty": 5,
        "memo_ref": "стр. 18-19",
        "example": "Разговор проведён, а карточка сохранена с одним словом «пропуск».",
    },
    {
        "code": "region_not_clarified",
        "title": "Не уточнён регион происшествия",
        "description": "Происшествие вне Москвы, а регион не уточнён и карточка оформлена как "
        "московская.",
        "mode": "call_intake",
        "penalty": 2,
        "memo_ref": "билеты, ситуации 1-3, 4-2, 7-2",
        "example": "Волгоградская обл., г. Волжский, ул. Карла Маркса.",
    },
]

# Facts the dispatcher passes to a service officer on the phone (issue #36, «звено Б → В»),
# with the keywords that show a fact in the dispatcher's phrase; ``address`` also needs the
# street and the house of the card named. The officer's replies use the same codes as topics,
# plus «greeting», «confirm», «repeat» and «unknown».
SERVICE_CALL_FACTS: list[dict] = [
    {
        "code": "address",
        "title": "Адрес",
        "keywords": ["адрес", "улиц", "проспект", "переул", "шоссе", "бульвар", "дом ", "д "],
        "order": 1,
    },
    {
        "code": "incident_type",
        "title": "Тип происшествия",
        "keywords": [
            "происшеств",
            "случил",
            "тип",
            "течь",
            "прорыв",
            "затопл",
            "нет отоплен",
            "нет воды",
            "нет света",
            "нет электр",
            "запах газа",
            "утечк",
            "пожар",
            "задымлен",
            "горит",
            "дтп",
            "провал",
            "обрыв",
            "авари",
            "сработал",
            "сигнализац",
            "застрял",
            "лифт",
            "канализац",
            "отключен",
        ],
        "order": 2,
    },
    {
        "code": "injured",
        "title": "Пострадавшие",
        "keywords": ["пострадавш", "пострадал", "ранен", "жертв", "травм", "без пострадавших"],
        "order": 3,
    },
    {
        "code": "order_number",
        "title": "Номер наряда",
        "keywords": ["наряд", "номер наряда", "заявк", "номер заявки"],
        "order": 4,
    },
    {
        "code": "access",
        "title": "Доступ на объект",
        "keywords": [
            "доступ",
            "домофон",
            "код подъезда",
            "код ",
            "ключ",
            "встрет",
            "открыт",
            "пропуск",
            "калитк",
            "шлагбаум",
        ],
        "order": 5,
    },
]

# The dispatcher asks the officer how the response goes («где бригада?», «выехали?»); the
# officer answers from the squad's current state (app.domain.scenarios.officers).
OFFICER_PROGRESS_KEYWORDS: list[str] = [
    "как дела",
    "как обстановка",
    "где бригада",
    "где наряд",
    "выехал",
    "выезжа",
    "прибыл",
    "на месте",
    "доехал",
    "закончил",
    "ход работ",
    "как работы",
    "что там",
    "долго ещё",
    "долго еще",
    "когда будете",
    "сколько ещё",
    "сколько еще",
    "когда закончите",
    "кто на месте",
    "сколько людей",
    "нужна помощь",
    "помощь нужна",
    "нужны службы",
    "справляетесь",
    "что по людям",
    "что с людьми",
    "перекрыт",
    "обстановка на месте",
]

OFFICER_TOPICS: list[dict] = [
    {"code": "greeting", "title": "Приветствие", "order": 0},
    *[{"code": f["code"], "title": f["title"], "order": f["order"]} for f in SERVICE_CALL_FACTS],
    {"code": "progress", "title": "Ход работ", "order": 6},
    {"code": "report", "title": "Доклад бригады", "order": 7},
    {"code": "confirm", "title": "Подтверждение приёма", "order": 8},
    {"code": "repeat", "title": "Просьба повторить", "order": 9},
    {"code": "unknown", "title": "Вне темы", "order": 10},
]

# Topics a dispatcher must clarify with the caller; keywords help the dialog engine to map a
# question to a topic without a model.
CALLER_TOPICS: list[dict] = [
    {
        "code": "what_happened",
        "title": "Что случилось",
        "keywords": ["что случилось", "что произошло", "что горит", "что у вас", "расскажите"],
        "order": 1,
    },
    {
        "code": "address",
        "title": "Адрес происшествия",
        # Без голого «где»: оно есть почти в любой фразе («где депо», «где-то там») и
        # закрывало тему адреса словами заявителя (замечание пользователя 22.09.2026).
        "keywords": [
            "адрес",
            "улица",
            # Whole-word forms of «дом»: the bare stem would also match «домофон».
            "дом ",
            "дома",
            "дому",
            "доме",
            "какой район",
            "где вы",
            "где это",
            "где находит",
            "где произош",
            "где случил",
            "где горит",
            "куда ехать",
            "куда направ",
            "ориентир",
        ],
        "order": 2,
    },
    {
        "code": "entrance_floor_code",
        "title": "Подъезд, этаж, код домофона",
        "keywords": ["подъезд", "этаж", "код", "домофон", "квартира"],
        "order": 3,
    },
    {
        "code": "region",
        "title": "Регион (Москва или другой)",
        "keywords": ["регион", "область", "город", "москва", "московская область", "населённый"],
        "order": 4,
    },
    {
        "code": "injured",
        "title": "Пострадавшие",
        "keywords": [
            "пострадавш",
            "пострадал",
            "ранен",
            "жертв",
            "сколько человек",
            "кому-то плохо",
            "травм",
            "в сознании",
            "жив",
            "дышит",
        ],
        "order": 5,
    },
    {
        "code": "danger",
        "title": "Угроза и обстановка",
        "keywords": ["угроза", "опасн", "распростран", "дым", "огонь", "оружи", "газ", "заблокир"],
        "order": 6,
    },
    {
        "code": "count_people",
        "title": "Количество людей",
        "keywords": ["сколько", "количество", "человек", "людей"],
        "order": 7,
    },
    {
        "code": "vehicle",
        "title": "Транспортное средство",
        "keywords": ["машин", "автомоб", "номер", "марка", "цвет", "гос"],
        "order": 8,
    },
    {
        "code": "time",
        "title": "Когда произошло",
        "keywords": ["когда", "как давно", "во сколько", "сколько времени"],
        "order": 9,
    },
    {
        "code": "caller_name",
        "title": "Имя заявителя",
        "keywords": ["как вас зовут", "представьтесь", "ваше имя", "фамилия", "кто звонит"],
        "order": 10,
    },
    {
        "code": "callback_phone",
        "title": "Телефон для связи",
        "keywords": ["телефон", "номер для связи", "перезвонить", "связаться"],
        "order": 11,
    },
    {
        "code": "caller_role",
        "title": "Отношение заявителя к происшествию",
        "keywords": ["очевидец", "вы сами", "кем приходитесь", "участник", "родствен"],
        "order": 12,
    },
    {
        "code": "repeat",
        "title": "Просьба повторить",
        "keywords": ["повторите", "не расслышал", "ещё раз", "громче"],
        "order": 13,
    },
    {
        "code": "unknown",
        "title": "Вне темы",
        "keywords": [],
        "order": 14,
    },
]
