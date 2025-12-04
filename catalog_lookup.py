# catalog_lookup.py
from pathlib import Path
from typing import Dict

import pandas as pd

from name_matching import find_best_match

# Путь к Каталогу
CATALOG_PATH = Path("Каталог.xlsx")

# Грузим один раз
DF_RAW = pd.read_excel(CATALOG_PATH, sheet_name="Таблица")

# Чистим строки без единиц измерения (типа "ИП ПЛЕТНЁВ", "Фишер" и т.п.)
DF_CAT = DF_RAW.copy()
DF_CAT["Ед"] = DF_CAT["Единицы измерения"].astype(str).str.strip()
DF_CAT = DF_CAT[DF_CAT["Ед"] != ""]

# ОКЕИ по единицам (как в compositions.py)
OKEI_BY_UNIT = {
    "кг": "166",
    "г": "163",
    "л": "112",
    "шт": "796",
}


def safe_price(value, default: float = 0.0) -> float:
    """
    Любая дичь в цене (пусто, пробелы, '—', 'н/д' и т.п.) -> default (0.0).
    Нормальные числа/строки с запятой тоже понимает.
    """
    # Уже число
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip().replace(",", ".")
    if not s:
        return float(default)

    try:
        return float(s)
    except ValueError:
        print(f"[WARN] Мусор в цене: {value!r}, подставляю {default}")
        return float(default)


def resolve_purchase_name(name: str) -> str:
    """
    Ищем товар в Каталоге по названию.
    1) точное совпадение
    2) без учёта регистра
    3) подстрока (если в Каталоге название длиннее)
    Возвращаем каноническое название из Каталога.
    """
    name_clean = name.strip()
    if not name_clean:
        raise ValueError("Пустое название товара.")

    candidate, score = find_best_match(name_clean, DF_CAT["Наименование"].astype(str).tolist())
    if candidate and score >= 0.55:
        return candidate

    raise ValueError(
        "Товар '{name}' не найден в Каталог.xlsx".format(name=name_clean)
    )


def get_purchase_item(name: str) -> Dict:
    """
    Возвращает мету товара для приходных документов:
    {
      "name": <строго как в Каталоге>,
      "code": <Код>,
      "unit": <Единицы измерения>,
      "okeei": <ОКЕИ>,
      "price": <закупочная цена (float)>
    }
    """
    canonical = resolve_purchase_name(name)
    sub = DF_CAT[DF_CAT["Наименование"] == canonical]
    if sub.empty:
        raise ValueError(
            f"Товар '{canonical}' неожиданно не найден в отфильтрованном Каталоге."
        )

    row = sub.iloc[0]
    unit = str(row["Единицы измерения"]).strip()
    code = str(row["Код"]).strip()

    # Берём цену из колонки "Себест." и безопасно приводим к float
    raw_price = row["Себест."]
    purchase_price = safe_price(raw_price, default=0.0)

    return {
        "name": canonical,
        "code": code,
        "unit": unit,
        "okeei": OKEI_BY_UNIT.get(unit, ""),
        "price": purchase_price,
    }
