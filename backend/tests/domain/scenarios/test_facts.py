"""Fact sheet parser on the organizers' ticket wording."""

from app.domain.scenarios.facts import find_phone, injured_summary, parse_address, parse_ticket


def test_phone_formats_are_normalized() -> None:
    assert find_phone("Иванов, 916-126-34-71")[0] == "916-126-34-71"
    assert find_phone("бросил трубку, 916 896 3254")[0] == "916-896-32-54"
    assert find_phone("вызывает мама, 9163201283")[0] == "916-320-12-83"
    assert find_phone("Яшин, 916- 126-34-71, (этажность 17)")[0] == "916-126-34-71"
    assert find_phone("без телефона") == (None, "без телефона")


def test_caller_name_phone_and_role() -> None:
    facts = parse_ticket(
        "Возгорание мусорного контейнера, пострадавших нет, Сидоров Иван Сергеевич, 916-126-34-71",
        "Москва, ул. Берзарина, дом 21",
    )
    assert facts.caller.name == "Сидоров Иван Сергеевич"
    assert facts.caller.phone == "916-126-34-71"
    assert facts.caller.role == "очевидец"
    assert facts.injured == "нет"
    assert facts.what_happened == "Возгорание мусорного контейнера, пострадавших нет"


def test_relative_calls_for_the_victim() -> None:
    facts = parse_ticket(
        "Ребенок 11 лет, Смирнов Илья упал с велосипеда, отек руки и ноги. Вызывает мама, "
        "9163201283",
        "Волгоградская обл., г. Волжский, ул. Карла Маркса около Волжского Молсыркомбината",
    )
    assert facts.caller.role == "родственник"
    assert facts.caller.relation == "мама"
    assert facts.caller.name is None  # the named person is the child, not the caller
    assert facts.child_involved
    assert facts.other_region
    assert facts.address.region == "Волгоградская область"
    assert facts.address.city == "Волжский"
    assert facts.address.street == "ул. Карла Маркса"
    assert facts.address.descriptive  # landmark, no house number


def test_named_caller_after_relative_word() -> None:
    facts = parse_ticket(
        "Петрова Светлана Павловна 35 лет, выпила упаковку снотворного, вызывает муж, Петров Олег, "
        "916 896 3254",
        "Москва, ул. Знаменские Садки, дом 7 корп.2, кв. 216, под. 4, эт. 4, код 216",
    )
    assert facts.caller.name == "Петров Олег"
    assert facts.caller.relation == "муж"
    assert facts.injured == "есть"
    address = facts.address
    assert (address.street, address.house, address.building) == ("ул. Знаменские Садки", "7", "2")
    assert (address.apartment, address.entrance, address.floor, address.code) == (
        "216",
        "4",
        "4",
        "216",
    )
    assert address.region is None and address.descriptive is None


def test_dropped_call_and_participant() -> None:
    facts = parse_ticket(
        "Поругался с продавцом «Мегафон», бросил трубку, 916 896 3254",
        "Москва, Сущевский Вал дом 5 стр.1",
    )
    assert facts.drops_call
    assert facts.caller.role == "участник"
    assert facts.address.street == "Сущевский Вал"
    assert facts.address.house == "5"
    assert facts.address.structure == "1"


def test_exact_address_in_brackets_after_landmark() -> None:
    address = parse_address(
        "Москва, рядом с посольством Азербайджана на тротуаре "
        "(Леонтьевский переулок, дом 16, стр.1)"
    )
    assert address.street == "Леонтьевский переулок"
    assert address.house == "16"
    assert address.structure == "1"
    assert address.descriptive and "посольством" in address.descriptive


def test_direction_word_is_not_a_region() -> None:
    address = parse_address("Москва, набережная Яузы, в область, напротив Большого Нижнего пруда")
    assert address.region is None
    assert address.street == "набережная Яузы"
    address = parse_address("ул. Станционная, дом 28 (при уточнении адреса - г. Королёв, МО)")
    assert address.region == "Московская область"
    assert address.city == "Королёв"


def test_route_description_does_not_become_a_street() -> None:
    """The card's street list holds nominative names of Moscow streets. A ticket that points at
    a route («дорога от…», МКАД, «стоят на Ленинградском ш.») has no street to pick: such an
    address belongs in the description, otherwise the trainee cannot fill the card at all."""
    for text in (
        "Дорога от Киевского ш. (М3) в сторону Минского ш. (М1) через Крекшино",
        "Москва, МКАД, напротив рынка Мельница, если ехать от Рублевского шоссе",
        "Стоят на Ленинградском ш. около памятника Ленина, напротив вокзала станции Ржев-1",
        "Москва, парк Лосиный остров, вход от улицы Красной сосны, далее по дорожке",
    ):
        assert parse_address(text).street is None, text


def test_street_behind_a_preposition_is_still_a_street() -> None:
    address = parse_address(
        "Москва, ул. Фабрициуса, остановка 62 автобуса «ул. Штурвальная» "
        "(на стороне ул. Фабрициуса, дом 18)"
    )
    assert address.street == "ул. Фабрициуса"
    assert address.house == "18"
    # An intersection names the street and describes the place.
    crossing = parse_address("Москва, ул. Тюменская на пересечении с Тюменский проездом")
    assert crossing.street == "ул. Тюменская"
    assert crossing.descriptive and "пересечении" in crossing.descriptive


def test_caller_gender_from_patronymic_and_relative() -> None:
    """The voice of the scenario follows it: a ticket's «Ивлев Артем Олегович» must not answer
    in a woman's voice."""
    male = parse_ticket("Задымление в торговом центре, Ивлев Артем Олегович, 916-123-98-78", "")
    assert male.caller.gender == "male"
    female = parse_ticket("Горит крыша, Иванова Инна Степановна, 916-126-34-71", "")
    assert female.caller.gender == "female"
    mother = parse_ticket("Ребенок упал с велосипеда. Вызывает мама, 9163201283", "")
    assert mother.caller.gender == "female"
    husband = parse_ticket("Отошли воды, вызывает супруг Минин Сергей Антонович, 916 897 5623", "")
    assert husband.caller.gender == "male"
    unknown = parse_ticket("Дерутся 3 человека, без пострадавших", "")
    assert unknown.caller.gender is None


def test_injured_summary() -> None:
    assert injured_summary("Дерутся 10-15 человек, 5 пострадавших с травмами") == "есть, 5"
    assert injured_summary("ДТП, Б/П, Б/Р, пежо + фольксваген") == "нет"
    assert injured_summary("Громко играет музыка во дворе") == "неизвестно"
    assert injured_summary("Плохо женщине, потеря сознания") == "есть"


def test_phrase_with_caller_hint() -> None:
    facts = parse_ticket("пожар в подземном паркинге, звонит ребёнок", "")
    assert facts.what_happened == "Пожар в подземном паркинге"
    assert facts.caller.relation == "ребёнок"
    assert facts.address.street is None
