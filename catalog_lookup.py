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


def resolve_purchase_name(name: str, min_score: float = 0.55) -> str:
    """
    Ищем товар в Каталоге по названию с улучшенной обработкой опечаток.
    
    Args:
        name: Название товара для поиска
        min_score: Минимальный порог похожести (0.0-1.0)
    
    Returns:
        Каноническое название из Каталога
    
    Raises:
        ValueError: Если товар не найден или score слишком низкий
    """
    name_clean = name.strip()
    if not name_clean:
        raise ValueError("Пустое название товара.")

    catalog_names = DF_CAT["Наименование"].astype(str).tolist()
    candidate, score = find_best_match(name_clean, catalog_names)
    
    # Логируем результат поиска
    _log_catalog_match(name_clean, candidate, score)
    
    if candidate and score >= min_score:
        # Если score между 0.55 и 0.75, это подозрительное совпадение - логируем
        if min_score <= score < 0.75:
            import sys
            print(f"[WARN] Неточное совпадение: '{name_clean}' -> '{candidate}' (score: {score:.3f})", 
                  file=sys.stderr)
        return candidate

    # Если не найдено, показываем топ-3 похожих для отладки
    from name_matching import calc_similarity
    scores = [(n, calc_similarity(name_clean, n)) for n in catalog_names if n.strip()]
    scores.sort(key=lambda x: x[1], reverse=True)
    top_matches = scores[:3]
    
    err_msg = f"Товар '{name_clean}' не найден в Каталог.xlsx (лучший score: {score:.3f}).\n"
    err_msg += "Похожие товары:\n"
    for match_name, match_score in top_matches:
        err_msg += f"  - {match_name} (score: {match_score:.3f})\n"
    
    raise ValueError(err_msg)


def _log_catalog_match(query: str, result: str, score: float):
    """Логирует результаты поиска в каталоге."""
    from datetime import datetime
    from pathlib import Path
    
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "catalog_matching.log"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} | Query: '{query}' -> Result: '{result}' (score: {score:.3f})\n")


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
