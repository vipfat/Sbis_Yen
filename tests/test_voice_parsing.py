import pytest

from bot_simple import parse_items_from_text


def test_voice_parsing_decimal_commas_and_sentences():
    voice = (
        "Борило номер 5 ПФ 2,170. Крем Наполеон ПФ 2. Крем суп из шампиньонов ПФ 2. "
        "Лапша ПФ 0,44. Лосось ПФ 0,838. Соус Хот ПФ 5. Соус Цезарь ПФ 2,200. "
        "Фарш котлеты ПФ 5,660."
    )
    items, errors = parse_items_from_text(voice)

    assert not errors
    # Проверяем ключевые пары
    expected = {
        "Борило номер 5 ПФ": 2.17,
        "Крем Наполеон ПФ": 2.0,
        "Крем суп из шампиньонов ПФ": 2.0,
        "Лапша ПФ": 0.44,
        "Лосось ПФ": 0.838,
        "Соус Хот ПФ": 5.0,
        "Соус Цезарь ПФ": 2.2,
        "Фарш котлеты ПФ": 5.66,
    }
    got = {it["name"]: pytest.approx(float(it["qty"])) for it in items}
    for k, v in expected.items():
        assert k in got
        assert got[k] == pytest.approx(v, rel=1e-6)
