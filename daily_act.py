# daily_act.py
import base64
import json
from datetime import datetime
from typing import List, Dict

import xml.etree.ElementTree as ET
import requests

from sbis_auth import get_auth_headers
from compositions import build_components_for_output
from catalog_lookup import get_purchase_item


SBIS_URL = "https://online.sbis.ru/service/?srv=1"

# Константы твоей организации (как в рабочем акте)
SENDER_TITLE = "Плетнёв Виталий Николаевич, ИП, точка продаж"
ORG_FL = {
    "ИНН": "940200200247",
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



def build_native_xml(doc_kind: str,
                     doc_date: str,
                     doc_number: str,
                     daily_items: List[Dict]) -> bytes:
    """
    Собираем XML, который кладем во вложение.

    Для production/writeoff – это native 3.01.
    Для income – используем ту же простую структуру, но с другим "Формат".
    """
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

    tab = ET.SubElement(doc, "ТаблДок")

    line_index = 1
    for item in daily_items:
        # Название строки
        name_input = str(item.get("name", "")).strip()
        if not name_input:
            # пустое название – выбрасываем
            continue

        # Сырые данные по количеству
        raw_qty = item.get("qty", "")

        # 1) если уже число – просто приводим к float
        if isinstance(raw_qty, (int, float)):
            qty = float(raw_qty)
        else:
            # 2) строка – чистим пробелы и пытаемся распарсить
            raw_str = str(raw_qty).strip()
            if not raw_str:
                # было "", " ", "\t" и т.п. – пропускаем
                continue
            try:
                qty = float(raw_str.replace(",", "."))
            except ValueError:
                # вообще мусор (например "шт", "ок", "—") – пропускаем,
                # но можно заодно лог вывести
                print(f"[WARN] Пропускаю строку '{name_input}', не могу понять количество: {raw_str!r}")
                continue

        # 3) нулевые количества нам в акте не нужны
        if qty == 0:
            continue

        if doc_kind == "income":
            # ПРИХОД: берём товар из Каталога, без составов
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

        else:
            # ПРОИЗВОДСТВО / СПИСАНИЕ:
            # 1) пробуем реестр составов (полуфабрикаты)
            # 2) если нет — используем Каталог как обычный товар

            try:
                # Попытка взять техкарту
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
                print(f"[WARN] '{name_input}' нет в реестре составов → иду в Каталог. Причина: {e}")

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

    # === Отправитель / Получатель ===
    sender = ET.SubElement(doc, "Отправитель", {
        "Название": SENDER_TITLE,
    })
    ET.SubElement(sender, "СвФЛ", ORG_FL)
    ET.SubElement(sender, "Склад", {
        "Идентификатор": "284",
        "Название": "ИП Плетнев",
    })

    receiver = ET.SubElement(doc, "Получатель")
    ET.SubElement(receiver, "Склад", {
        "Название": "ИП Плетнев",
    })

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
