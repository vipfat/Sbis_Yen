# compositions.py
from pathlib import Path
from typing import Dict, List

import pandas as pd

from config import PATHS
from utils import to_float_safe
from name_matching import find_best_match

# Грузим один раз
DF_COMP = pd.read_excel(PATHS.compositions_excel)
DF_PROD = pd.read_excel(PATHS.production_excel, sheet_name="Таблица")

# ОКЕИ по единицам измерения
OKEI_BY_UNIT = {
    "кг": "166",
    "г": "163",
    "л": "112",
    "шт": "796",
    # добавишь по мере надобности: "мл", "упак" и т.п.
}


def resolve_parent_name(name: str) -> str:
    """
    Ищем родителя в Реестре составов по имени, без учёта регистра.
    Возвращаем каноническое имя (ровно как в Excel).
    """
    name_clean = name.strip()

    candidate, score = find_best_match(name_clean, DF_COMP["Родитель"].astype(str).tolist())
    if candidate and score >= 0.55:
        return candidate

    raise ValueError(f"Родитель '{name_clean}' не найден в Реестре составов.")


def get_recipe(parent_name: str, composition_no: int = 1) -> Dict:
    """
    Структура по родителю:
    {
      parent_name,
      parent_code,
      base_output,     # 'Состав на'
      components: [
        { name, code, unit, okee, qty_base }
      ]
    }
    """
    canonical = resolve_parent_name(parent_name)

    sub = DF_COMP[
        (DF_COMP["Родитель"] == canonical)
        & (DF_COMP["Номер состава"] == composition_no)
    ]
    if sub.empty:
        raise ValueError(
            f"Для '{canonical}' нет состава с Номер состава = {composition_no}"
        )

    parent_code = sub["Код родителя"].iloc[0]
    base_output = float(sub["Состав на"].iloc[0])

    components: List[Dict] = []
    for _, row in sub.iterrows():
        unit = str(row["Ед.изм составляющей"]).strip()
        components.append({
            "name": row["Название составляющей"],
            "code": row["Код составляющей"],
            "unit": unit,
            "okeei": OKEI_BY_UNIT.get(unit, ""),
            "qty_base": float(row["Кол-во"]),  # на base_output готового продукта
        })

    return {
        "parent_name": canonical,
        "parent_code": parent_code,
        "base_output": base_output,
        "components": components,
    }


def get_parent_meta(parent_code: str) -> Dict:
    """
    Берём Ед. изм и ОКЕИ готовой продукции из Производство.xlsx по коду родителя.
    """
    sub = DF_PROD[DF_PROD["Код"] == parent_code]
    if sub.empty:
        raise ValueError(
            f"Код родителя '{parent_code}' не найден в Производство.xlsx"
        )

    row = sub.iloc[0]
    unit = str(row["Единицы измерения"]).strip()

    return {
        "name": row["Наименование"],
        "unit": unit,
        "okeei": OKEI_BY_UNIT.get(unit, ""),
        "price": to_float_safe(row.get("Цена")),
        "cost": to_float_safe(row.get("Себест.")),
    }


def build_components_for_output(parent_name: str,
                                output_qty: float,
                                composition_no: int = 1) -> Dict:
    """
    На вход: название родителя (как ты пишешь в бота) и сколько хотим произвести.
    На выход:
    {
      parent_name,       # как в реестре
      parent_code,
      parent_unit,
      parent_okeei,
      output_qty,
      base_output,
      k,
      components: [ { name, code, unit, okeei, qty } ]
    }
    """
    recipe = get_recipe(parent_name, composition_no)
    base_output = recipe["base_output"]
    if base_output == 0:
        raise ValueError(f"У '{recipe['parent_name']}' базовый 'Состав на' = 0.")

    k = output_qty / base_output

    scaled_components: List[Dict] = []
    for comp in recipe["components"]:
        scaled_components.append({
            **comp,
            "qty": round(comp["qty_base"] * k, 6),
        })

    parent_meta = get_parent_meta(recipe["parent_code"])

    return {
        "parent_name": recipe["parent_name"],
        "parent_code": recipe["parent_code"],
        "parent_unit": parent_meta["unit"],
        "parent_okeei": parent_meta["okeei"],
        "output_qty": output_qty,
        "base_output": base_output,
        "k": k,
        "components": scaled_components,
    }
