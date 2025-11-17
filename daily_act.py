# daily_act.py
import base64
import json
from datetime import datetime
from typing import List, Dict
import uuid

import xml.etree.ElementTree as ET
import requests

from sbis_auth import get_auth_headers
from compositions import build_components_for_output
from catalog_lookup import get_purchase_item
from income_upd import send_income_upd


SBIS_URL = "https://online.sbis.ru/service/?srv=1"

# Описание типов документов в СБИС
DOC_KINDS = {
    "production": {
        "sbis_type": "АктВыпуска",
        "label": "Производство",
    },
    "writeoff": {
        "sbis_type": "АктСписания",
        "label": "Списание",
    },
    "income": {
        # Приход сейчас отправляем через УПД (income_upd),
        # поэтому sbis_type напрямую не используется.
        "sbis_type": "ДокОтгрВх",
        "label": "Приход",
    },
}


def build_native_xml(doc_kind: str,
                     doc_date: str,
                     doc_number: str,
                     items: List[Dict]) -> bytes:
    """
    Собирает XML-документ для АктВыпуска / АктСписания.
    Для полуфабрикатов используется реестр составов (compositions),
    для обычных товаров — данные из Каталога (Каталог.xlsx).
    """
    if doc_kind not in ("production", "writeoff"):
        raise ValueError(f"build_native_xml поддерживает только production/writeoff, а пришло: {doc_kind!r}")

    kind_info = DOC_KINDS[doc_kind]

    # Корневой элемент (упрощённо, но с теми же полями, что ты уже используешь в строках)
    root = ET.Element(
        "Документ",
        {
            "ВидДок": kind_info["sbis_type"],
            "Дата": doc_date,
            "Номер": doc_number,
        },
    )

    tab = ET.SubElement(root, "Таблица")

    line_index = 1
    for item in items:
        name_input = str(item.get("name", "")).strip()
        if not name_input:
            continue

        qty_raw = item.get("qty", 0)
        try:
            qty = float(qty_raw)
        except Exception:
            # split_valid_invalid_items уже должен был всё почистить,
            # но на всякий пожарный.
            continue

        if qty == 0:
            continue

        # Для производства/списания:
        # 1) пробуем реестр составов (полуфабрикаты)
        # 2) если записи нет — считаем, что это обычный товар из Каталога
        try:
            recipe = build_components_for_output(name_input, output_qty=qty)

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
                "Кол_во": f"{qty:.3f}",
                "Название": parent_name,
                "ОКЕИ": okee,
                "ПорНомер": str(line_index),
                "Сумма": "0.00",
                "Цена": "0.00",
            }
            row = ET.SubElement(tab, "СтрТабл", row_attrs)

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

        except Exception as e:
            # Нет в реестре составов — считаем обычным товаром из Каталога
            print(f"[WARN] '{name_input}' нет в реестре составов → использую Каталог. Причина: {e}")

            meta = get_purchase_item(name_input)

            row_attrs = {
                "Вместимость": "0",
                "ЕдИзм": meta["unit"],
                "ЗаказатьКодов": "0",
                "Идентификатор": meta["code"],
                "Кол_во": f"{qty:.3f}",
                "Название": meta["name"],
                "ОКЕИ": meta["okeei"],
                "ПорНомер": str(line_index),
                "Сумма": "0.00",
                "Цена": "0.00",
            }
            ET.SubElement(tab, "СтрТабл", row_attrs)

        line_index += 1

    xml_bytes = ET.tostring(
        root,
        encoding="windows-1251",
        xml_declaration=True,
    )
    return xml_bytes


def build_payload_for_sbis(doc_kind: str,
                           doc_date: str,
                           doc_number: str,
                           xml_bytes: bytes) -> Dict:
    """
    Формирует JSON-RPC запрос для метода СБИС.ЗаписатьДокумент
    с вложением нашего XML.
    """
    kind_info = DOC_KINDS.get(doc_kind)
    if not kind_info:
        raise ValueError(f"Неизвестный тип документа: {doc_kind}")

    xml_b64 = base64.b64encode(xml_bytes).decode("ascii")

    sbis_type = kind_info["sbis_type"]
    title = f"{kind_info['label']} {doc_number}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "СБИС.ЗаписатьДокумент",
        "params": {
            "Документ": {
                "Тип": sbis_type,
                "Направление": "Исходящий",
                "Номер": doc_number,
                "Дата": doc_date,
                "Вложение": [
                    {
                        "Идентификатор": str(uuid.uuid4()),
                        "Тип": "Xml",
                        "Подтип": "",
                        "ВерсияФормата": "",
                        "ПодверсияФормата": "",
                        "Название": title,
                        "Служебный": "Нет",
                        "Дата": doc_date,
                        "Номер": doc_number,
                        "Зашифрован": "Нет",
                        "ТипШифрования": "Отсутствует",
                        "Файл": {
                            "Имя": f"{doc_number}.xml",
                            "ДвоичныеДанные": xml_b64,
                        },
                    }
                ],
            }
        },
    }
    return payload


def send_document_to_sbis(payload: Dict) -> Dict:
    """
    Общая отправка документа в СБИС.
    """
    headers = get_auth_headers()
    resp = requests.post(SBIS_URL, json=payload, headers=headers, timeout=30)
    try:
        data = resp.json()
    except Exception:
        data = {"error": f"HTTP {resp.status_code}", "text": resp.text}

    return data


def send_daily_act(doc_date: str,
                   doc_number: str,
                   daily_items: List[Dict]) -> Dict:
    """
    Производство (АктВыпуска).
    """
    xml_bytes = build_native_xml("production", doc_date, doc_number, daily_items)
    payload = build_payload_for_sbis("production", doc_date, doc_number, xml_bytes)
    return send_document_to_sbis(payload)


def send_writeoff_act(doc_date: str,
                      doc_number: str,
                      daily_items: List[Dict]) -> Dict:
    """
    Списание (АктСписания).
    """
    xml_bytes = build_native_xml("writeoff", doc_date, doc_number, daily_items)
    payload = build_payload_for_sbis("writeoff", doc_date, doc_number, xml_bytes)
    return send_document_to_sbis(payload)


def send_income_act(doc_date: str,
                    doc_number: str,
                    daily_items: List[Dict]) -> Dict:
    """
    Приход — делаем через УПД на базе шаблона (см. income_upd.py).
    """
    return send_income_upd(doc_date, doc_number, daily_items)


# Для ручного теста
if __name__ == "__main__":
    items = [
        {"name": "Тесто", "qty": 5},
    ]
    today = datetime.today().strftime("%d.%м.%Y")
    print(json.dumps(send_daily_act(today, "TEST-001", items), ensure_ascii=False, indent=2))
