# name_matching.py
"""
Утилиты для сопоставления введённых названий с каталогом товаров и
реестром полуфабрикатов.

Цель — сгладить опечатки, двойные пробелы и прочий шум, чтобы при
формировании документов использовать канонические наименования из
Excel-файлов.
"""

from difflib import SequenceMatcher
from typing import Dict, List, Tuple

from catalog_lookup import DF_CAT
from compositions import DF_COMP


def _normalize(name: str) -> str:
    """Убираем лишние пробелы и приводим к нижнему регистру."""
    return " ".join(str(name or "").split()).strip().casefold()


def _unique_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# Списки канонических названий
PARENT_NAMES: List[str] = _unique_preserve_order(
    [str(n).strip() for n in DF_COMP["Родитель"].dropna()]
)
CATALOG_NAMES: List[str] = _unique_preserve_order(
    [str(n).strip() for n in DF_CAT["Наименование"].dropna()]
)

# Индексы «нормализованное → каноническое»
PARENT_INDEX: List[Tuple[str, str]] = [(_normalize(n), n) for n in PARENT_NAMES]
CATALOG_INDEX: List[Tuple[str, str]] = [(_normalize(n), n) for n in CATALOG_NAMES]


def _best_match(target_norm: str, index: List[Tuple[str, str]]):
    """
    Возвращает (каноническое название, score) с максимальным похожестью
    по SequenceMatcher.
    """
    best_name = None
    best_score = 0.0

    for norm, canonical in index:
        if target_norm == norm:
            return canonical, 1.0

        score = SequenceMatcher(None, target_norm, norm).ratio()
        if score > best_score:
            best_score = score
            best_name = canonical

    return best_name, best_score


def resolve_known_name(name: str, doc_type: str = None, cutoff: float = 0.6) -> Dict:
    """
    Подбираем наиболее подходящее название из полуфабрикатов или Каталога.

    Возвращает словарь:
    {
      "name": <каноническое название>,
      "source": "composition" | "catalog" | "raw",
      "score": <0..1>
    }
    """

    normalized = _normalize(name)

    parent_name, parent_score = _best_match(normalized, PARENT_INDEX)
    catalog_name, catalog_score = _best_match(normalized, CATALOG_INDEX)

    prefer_parents = doc_type in {"production", "writeoff"}
    prefer_catalog = doc_type == "income"

    candidates: List[Tuple[str, str, float]] = []
    if parent_name:
        candidates.append(("composition", parent_name, parent_score))
    if catalog_name:
        candidates.append(("catalog", catalog_name, catalog_score))

    chosen: Tuple[str, str, float] | None = None
    if prefer_parents and parent_name:
        chosen = ("composition", parent_name, parent_score)
    elif prefer_catalog and catalog_name:
        chosen = ("catalog", catalog_name, catalog_score)
    elif candidates:
        chosen = max(candidates, key=lambda c: c[2])

    if chosen and chosen[2] >= cutoff:
        return {"name": chosen[1], "source": chosen[0], "score": chosen[2]}

    # Если мы сюда дошли, то либо кандидаты слишком слабые, либо их нет.
    if chosen:
        return {"name": chosen[1], "source": chosen[0], "score": chosen[2]}

    return {"name": name, "source": "raw", "score": 0.0}


def align_items_with_catalog(items: List[Dict], doc_type: str = None):
    """
    Пробегает по списку позиций и подменяет name на канонический вариант.
    Возвращает кортеж:
      (обновлённый_items, список_поправок)
    где список_поправок — строки вида "<что было> → <во что исправили> (каталог/состав)".
    """

    aligned: List[Dict] = []
    corrections: List[str] = []

    for item in items:
        name_raw = str(item.get("name", "")).strip()
        if not name_raw:
            continue

        try:
            qty_val = float(item.get("qty"))
        except (TypeError, ValueError):
            continue

        resolved = resolve_known_name(name_raw, doc_type=doc_type)
        aligned.append({"name": resolved["name"], "qty": qty_val})

        if resolved["name"] != name_raw:
            source_label = "каталог" if resolved["source"] == "catalog" else "состав"
            corrections.append(f"{name_raw} → {resolved['name']} ({source_label})")

    return aligned, corrections
