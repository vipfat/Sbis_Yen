# catalog_lookup.py
from typing import Dict, List, Tuple

import pandas as pd

from config import PATHS
from utils import to_float_safe
from name_matching import find_best_match


class ProductNotFoundError(Exception):
    """Исключение когда товар не найден, с вариантами для выбора."""
    def __init__(self, query: str, suggestions: List[Tuple[str, float]]):
        self.query = query
        self.suggestions = suggestions  # [(name, score), ...]
        
        msg = f"Товар '{query}' не найден в каталоге (лучший score: {suggestions[0][1]:.3f}).\n"
        msg += "Похожие товары:\n"
        for name, score in suggestions[:3]:
            msg += f"  - {name} (score: {score:.3f})\n"
        
        super().__init__(msg)

# Грузим один раз
DF_RAW = pd.read_excel(PATHS.catalog_excel, sheet_name="Таблица")

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

    # Специальная обработка для "охотничьи" vs "хот"
    # OCR и голосовой ввод часто путают эти слова
    name_lower = name_clean.lower()
    if "хот" in name_lower and "соус" not in name_lower:
        # Если есть "хот" но нет "соус" - скорее всего "охотничьи"
        # Также если просто "хот" без других слов - тоже "охотничьи"
        if (any(word in name_lower for word in ["колбас", "охот", "кол"]) or 
            name_lower.strip() in ["хот", "хот."]):
            # Явно ищем КОЛБАСКИ ОХОТНИЧЬИ
            for cat_name in DF_CAT["Наименование"].astype(str).tolist():
                if "КОЛБАСКИ ОХОТНИЧЬИ" in cat_name.upper():
                    import sys
                    print(f"[INFO] Специальная обработка: '{name_clean}' → 'КОЛБАСКИ ОХОТНИЧЬИ'", 
                          file=sys.stderr)
                    _log_catalog_match(name_clean, cat_name, 1.0)
                    return cat_name

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

    # Если не найдено, показываем топ-5 похожих для выбора
    from name_matching import calc_similarity
    scores = [(n, calc_similarity(name_clean, n)) for n in catalog_names if n.strip()]
    scores.sort(key=lambda x: x[1], reverse=True)
    top_matches = scores[:5]  # Топ-5 для выбора
    
    raise ProductNotFoundError(name_clean, top_matches)


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
    purchase_price = to_float_safe(raw_price, default=0.0)

    return {
        "name": canonical,
        "code": code,
        "unit": unit,
        "okeei": OKEI_BY_UNIT.get(unit, ""),
        "price": purchase_price,
    }
