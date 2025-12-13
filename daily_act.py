# daily_act.py
import base64
import json
from datetime import datetime
from typing import List, Dict

import xml.etree.ElementTree as ET
import requests

from config import COMPANY, TIMEOUTS
from utils import to_float_safe, format_quantity
from sbis_auth import get_auth_headers
from compositions import DF_COMP, DF_PROD, build_components_for_output
from catalog_lookup import DF_CAT, get_purchase_item
from name_matching import find_best_match


SBIS_URL = "https://online.sbis.ru/service/?srv=1"

# Константы твоей организации (из config)
SENDER_TITLE = "Плетнёв Виталий Николаевич, ИП, точка продаж"
ORG_FL = {
    "ИНН": COMPANY.inn,
    "Имя": "Виталий",
    "Название": SENDER_TITLE,
    "Отчество": "Николаевич",
    "Пол": "0",
    "Фамилия": "Плетнёв",
}
NASH_ORG = {"СвФЛ": ORG_FL}

# Настройки типов документов.
# production / writeoff – внутренние native (3.01)
# income – входящий отгрузочный + УПД (УпдДоп, КНД 1115131, ВерсияФормата 5.03)
DOC_KINDS = {
    "production": {
        "sbis_doc_type": "АктВыпуска",
        "file_format": "АктВыпуска",
        "attachment_type": "АктВыпуска",
        "attachment_subtype": "АктВыпуска",
        "version": "3.01",
        "title_prefix": "Акт выпуска",
        "filename_prefix": "act_prod_",
    },
    "writeoff": {
        "sbis_doc_type": "АктСписания",
        "file_format": "АктСписания",
        "attachment_type": "АктСписания",
        "attachment_subtype": "АктСписания",
        "version": "3.01",
        "title_prefix": "Акт списания",
        "filename_prefix": "act_wr_",
    },
    "income": {
        # Приход / поступление от поставщика: ДокОтгрВх + УПД (формат ФНС 1115131, версия 5.03)
        "sbis_doc_type": "ДокОтгрВх",

        # Формализованное вложение – УПД без счета-фактуры (УпдДоп).
        # Эта тройка attachment_type / attachment_subtype / version —
        # как раз то, из чего СБИС собирает строку
        # 'УпдДоп/1115131/5.03' и по ней находит формат.
        "file_format": "1115131",         # просто маркер формата, можешь оставить как есть
        "attachment_type": "УпдДоп",      # тип вложения
        "attachment_subtype": "1115131",  # КНД
        "version": "5.03",                # ВерсФорм из УПД

        "title_prefix": "Поступление",
        "filename_prefix": "income_",
    },

}


def _pick_best_known_names(user_input: str) -> Dict:
    """Ищем самое подходящее название во всех справочниках."""

    # СПЕЦИАЛЬНАЯ ОБРАБОТКА: "хот" vs "охотничьи"
    # Если в запросе "хот" без "соус" - это КОЛБАСКИ ОХОТНИЧЬИ, а не СОУС ХОТ
    user_lower = user_input.lower().strip()
    if "хот" in user_lower and "соус" not in user_lower:
        if any(word in user_lower for word in ["колбас", "охот", "кол"]) or user_lower in ["хот", "хот."]:
            # Принудительно ищем КОЛБАСКИ ОХОТНИЧЬИ во всех справочниках
            forced_name = "КОЛБАСКИ ОХОТНИЧЬИ"
            result = {
                "overall": {"score": 1.0, "name": forced_name, "source": "catalog"},
                "by_source": {
                    "catalog": {"name": forced_name, "score": 1.0},
                }
            }
            import sys
            print(f"[INFO] Принудительное сопоставление: '{user_input}' → '{forced_name}'", file=sys.stderr)
            return result

    sources = {
        "composition": DF_COMP["Родитель"].astype(str).tolist(),
        "production": DF_PROD["Наименование"].astype(str).tolist(),
        "catalog": DF_CAT["Наименование"].astype(str).tolist(),
    }

    best_overall = {"score": 0.0, "name": None, "source": None}
    per_source: Dict[str, Dict] = {}

    for source, names in sources.items():
        candidate, score = find_best_match(user_input, names)
        if candidate:
            per_source[source] = {"name": candidate, "score": score}
            if score > best_overall["score"]:
                best_overall = {"score": score, "name": candidate, "source": source}
    
    # Для каталога используем resolve_purchase_name с специальной логикой
    try:
        from catalog_lookup import resolve_purchase_name
        catalog_resolved = resolve_purchase_name(user_input, min_score=0.5)
        # Пересчитываем score для точного соответствия
        catalog_score = find_best_match(user_input, [catalog_resolved])[1]
        if catalog_score >= 0.5:
            per_source["catalog"] = {"name": catalog_resolved, "score": catalog_score}
            if catalog_score > best_overall["score"]:
                best_overall = {"score": catalog_score, "name": catalog_resolved, "source": "catalog"}
    except:
        pass  # Если не нашли - используем результат find_best_match выше

    return {"overall": best_overall, "by_source": per_source}


def _create_xml_root(doc_kind: str, doc_date: str, doc_number: str) -> tuple:
    """Создает корневой элемент XML и документ."""
    kind = DOC_KINDS.get(doc_kind)
    if not kind:
        raise ValueError(f"Неизвестный тип документа: {doc_kind}")
    
    root = ET.Element("Файл", {
        "ВерсияФормата": kind.get("version", "3.01"),
        "Формат": kind["file_format"],
    })
    
    doc = ET.SubElement(root, "Документ", {
        "Дата": doc_date,
        "Номер": doc_number,
    })
    
    return root, doc


def _parse_item_quantity(raw_qty) -> float:
    """
    Безопасно извлекает количество из сырых данных.
    
    Returns:
        float: Количество или 0.0 если не удалось распарсить
    """
    if isinstance(raw_qty, (int, float)):
        return float(raw_qty)
    
    raw_str = str(raw_qty).strip()
    if not raw_str:
        return 0.0
    
    return to_float_safe(raw_str, default=0.0)


def _build_income_row(item_name: str, qty: float, line_index: int, best_by_source: Dict) -> Dict:
    """Строит атрибуты строки для акта прихода."""
    catalog_candidate = best_by_source.get("catalog")
    target_name = catalog_candidate["name"] if catalog_candidate and catalog_candidate.get("name") else item_name
    meta = get_purchase_item(target_name)
    
    return {
        "Вместимость": "0",
        "ЕдИзм": meta["unit"],
        "ЗаказатьКодов": "0",
        "Идентификатор": meta["code"],
        "Кол_во": format_quantity(qty),
        "Название": meta["name"],
        "ОКЕИ": meta["okeei"],
        "ПорНомер": str(line_index),
        "Сумма": "0.00",
        "Цена": "0.00",
    }


def _build_production_row_with_recipe(item_name: str, qty: float, line_index: int, 
                                      best_by_source: Dict, tab_element) -> int:
    """
    Строит строку производства/списания с рецептом (составом).
    
    Returns:
        int: Количество добавленных строк (1 + компоненты)
    """
    composition_candidate = best_by_source.get("composition") or best_by_source.get("production")
    recipe_name = composition_candidate["name"] if composition_candidate else item_name
    
    recipe = build_components_for_output(recipe_name, output_qty=qty)
    
    parent_name = recipe["parent_name"]
    parent_code = recipe["parent_code"]
    unit = recipe["parent_unit"]
    okee = recipe["parent_okeei"]
    
    # Строка родителя
    row_attrs = {
        "Вместимость": "0",
        "ЕдИзм": unit,
        "ЗаказатьКодов": "0",
        "Идентификатор": parent_code,
        "Кол_во": format_quantity(qty),
        "Название": parent_name,
        "ОКЕИ": okee,
        "ПорНомер": str(line_index),
        "Сумма": "0.00",
        "Цена": "0.00",
    }
    row = ET.SubElement(tab_element, "СтрТабл", row_attrs)
    
    # Состав
    comp_index = 1
    for comp in recipe["components"]:
        comp_attrs = {
            "Вместимость": "0",
            "ЕдИзм": comp["unit"],
            "Идентификатор": comp["code"],
            "Кол_во": f"{comp['qty']:.6f}",
            "Кол_во_План": f"{comp['qty']:.6f}",
            "Название": comp["name"],
            "ОКЕИ": comp["okeei"],
            "ПорНомер": str(comp_index),
            "Сумма": "0.00",
            "Цена": "0.00",
        }
        ET.SubElement(row, "СоставСтрТабл", comp_attrs)
        comp_index += 1
    
    return 1


def _build_catalog_row(item_name: str, qty: float, line_index: int, best_by_source: Dict) -> Dict:
    """Строит атрибуты строки для обычного товара из каталога."""
    catalog_candidate = best_by_source.get("catalog")
    target_name = catalog_candidate.get("name") if catalog_candidate and catalog_candidate.get("name") else item_name
    meta = get_purchase_item(target_name)
    
    return {
        "Вместимость": "0",
        "ЕдИзм": meta["unit"],
        "ЗаказатьКодов": "0",
        "Идентификатор": meta["code"],
        "Кол_во": format_quantity(qty),
        "Название": meta["name"],
        "ОКЕИ": meta["okeei"],
        "ПорНомер": str(line_index),
        "Сумма": "0.00",
        "Цена": "0.00",
    }


def _add_sender_receiver(doc_element):
    """Добавляет секции Отправитель и Получатель в документ."""
    sender = ET.SubElement(doc_element, "Отправитель", {
        "Название": SENDER_TITLE,
    })
    ET.SubElement(sender, "СвФЛ", ORG_FL)
    ET.SubElement(sender, "Склад", {
        "Идентификатор": COMPANY.warehouse_id,
        "Название": COMPANY.warehouse_name,
    })
    
    receiver = ET.SubElement(doc_element, "Получатель")
    ET.SubElement(receiver, "Склад", {
        "Название": COMPANY.warehouse_name,
    })


def _add_writeoff_reason(doc_element):
    """Добавляет причину списания (ТаблСклад) для актов списания."""
    tabl_sklad = ET.SubElement(doc_element, "ТаблСклад")
    ET.SubElement(tabl_sklad, "СтрТабл", {
        "Назначение": COMPANY.writeoff_purpose,
        "Получатель": COMPANY.recipient_name,
        "Склад": COMPANY.warehouse_name,
        "Счет": COMPANY.account,
    })


def build_native_xml(doc_kind: str,
                     doc_date: str,
                     doc_number: str,
                     daily_items: List[Dict]) -> bytes:
    """
    Собираем XML для акта (производство/списание/приход).
    
    Разбит на вспомогательные функции для улучшения читаемости.
    """
    # Создаем корневую структуру XML
    root, doc = _create_xml_root(doc_kind, doc_date, doc_number)
    tab = ET.SubElement(doc, "ТаблДок")

    line_index = 1
    for item in daily_items:
        # Валидация названия
        name_input = str(item.get("name", "")).strip()
        if not name_input:
            continue

        # Сопоставление с каталогом/составами
        best_match = _pick_best_known_names(name_input)
        best_by_source = best_match.get("by_source", {})
        best_overall = best_match.get("overall", {})

        # Парсинг количества
        qty = _parse_item_quantity(item.get("qty", ""))
        if qty == 0:
            print(f"[WARN] Пропускаю '{name_input}': некорректное или нулевое количество")
            continue

        # Обработка в зависимости от типа документа
        if doc_kind == "income":
            # ПРИХОД: только каталог, без составов
            row_attrs = _build_income_row(name_input, qty, line_index, best_by_source)
            ET.SubElement(tab, "СтрТабл", row_attrs)
            line_index += 1

        else:
            # ПРОИЗВОДСТВО / СПИСАНИЕ: пробуем состав, затем каталог
            try:
                _build_production_row_with_recipe(name_input, qty, line_index, best_by_source, tab)
                line_index += 1
            except Exception as e:
                # Нет в составах - обычный товар из каталога
                print(f"[WARN] '{name_input}' нет в реестре составов → иду в Каталог. Причина: {e}")
                row_attrs = _build_catalog_row(name_input, qty, line_index, best_by_source)
                ET.SubElement(tab, "СтрТабл", row_attrs)
                line_index += 1

    # Добавляем секции отправителя/получателя
    _add_sender_receiver(doc)
    
    # Для списания добавляем причину
    if doc_kind == "writeoff":
        _add_writeoff_reason(doc)

    # Генерация итогового XML
    xml_bytes = ET.tostring(root, encoding="windows-1251", xml_declaration=True)
    return xml_bytes


def build_payload_for_sbis(doc_kind: str,
                           doc_date: str,
                           doc_number: str,
                           xml_bytes: bytes) -> Dict:
    kind = DOC_KINDS.get(doc_kind)
    if not kind:
        raise ValueError(f"Неизвестный тип документа: {doc_kind}")

    xml_b64 = base64.b64encode(xml_bytes).decode("ascii")
    version = kind.get("version", "3.01")

    document = {
        "Тип": kind["sbis_doc_type"],
        "Номер": doc_number,
        "Дата": doc_date,
        "НашаОрганизация": NASH_ORG,
        "Вложение": [
            {
                "Тип": kind["attachment_type"],
                "Подтип": kind["attachment_subtype"],
                "ВерсияФормата": version,
                "ПодверсияФормата": "",
                "Название": f"{kind['title_prefix']} {doc_date} № {doc_number}",
                "Зашифрован": "Нет",
                "Файл": {
                    "Имя": f"{kind['filename_prefix']}{doc_number}.xml",
                    "ДвоичныеДанные": xml_b64,
                },
            }
        ],
    }

    return {
        "jsonrpc": "2.0",
        "method": "СБИС.ЗаписатьДокумент",
        "params": {"Документ": document},
        "id": 1,
    }


def send_any_act(doc_kind: str,
                 doc_date: str,
                 doc_number: str,
                 daily_items: List[Dict]) -> Dict:
    """
    Универсальная отправка акта (производство/списание/приход).
    """
    xml_bytes = build_native_xml(doc_kind, doc_date, doc_number, daily_items)
    payload = build_payload_for_sbis(doc_kind, doc_date, doc_number, xml_bytes)

    headers = get_auth_headers()
    headers.update({
        "Content-Type": "application/json-rpc;charset=utf-8",
        "User-Agent": "YenPrestoBot/1.0",
    })

    resp = requests.post(
        SBIS_URL,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=30,
    )

    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# Обёртки под разные типы
def send_daily_act(doc_date: str,
                   doc_number: str,
                   daily_items: List[Dict]) -> Dict:
    return send_any_act("production", doc_date, doc_number, daily_items)


def send_writeoff_act(doc_date: str,
                      doc_number: str,
                      daily_items: List[Dict]) -> Dict:
    return send_any_act("writeoff", doc_date, doc_number, daily_items)


from income_upd import send_income_upd


def send_income_act(doc_date: str,
                    doc_number: str,
                    daily_items: List[Dict]) -> Dict:
    # Приход теперь делаем через нормальный УПД на базе шаблона
    return send_income_upd(doc_date, doc_number, daily_items)

# Для ручного теста
if __name__ == "__main__":
    items = [
        {"name": "Тесто", "qty": 5},
    ]
    today = datetime.today().strftime("%d.%m.%Y")
    print(json.dumps(send_daily_act(today, "TEST-001", items), ensure_ascii=False, indent=2))
