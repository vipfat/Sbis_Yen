# catalog_lookup.py
from pathlib import Path
from typing import Dict

import pandas as pd

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

    # 1. Точное совпадение
    m = DF_CAT["Наименование"] == name_clean
    if m.any():
        return DF_CAT.loc[m, "Наименование"].iloc[0]

    # 2. Без учёта регистра
    lower = name_clean.casefold()
    m = DF_CAT["Наименование"].astype(str).str.casefold() == lower
    if m.any():
        return DF_CAT.loc[m, "Наименование"].iloc[0]

    # 3. Содержит (когда в Каталоге, например, 'ЛАЙМ КУХНЯ', а пришло 'лайм')
    contains = DF_CAT["Наименование"].astype(str).str.casefold().str.contains(lower)
    if contains.any():
        return DF_CAT.loc[contains, "Наименование"].iloc[0]

    raise ValueError(f"Товар '{name_clean}' не найден в Каталог.xlsx")


def get_purchase_item(name: str) -> Dict:
    """
    Возвращает мету товара для приходных документов:
    {
      "name": <строго как в Каталоге>,
      "code": <Код>,
      "unit": <Единицы измерения>,
      "okeei": <ОКЕИ>
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

# Всегда безопасно приводим цену к float, пустота → 0
raw_price = row["Себест."]

def safe_price(x):
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", ".")
    if not s:           # пустая ячейка или пробелы
        return 0.0
    try:
        return float(s)
    except:
        print(f"[WARN] Мусор в цене '{x}', ставлю 0.0")
        return 0.0

purchase_price = safe_price(raw_price)

    return {
        "name": canonical,
        "code": code,
        "unit": unit,
        "okeei": OKEI_BY_UNIT.get(unit, ""),
        "price": purchase_price,
    }
