import base64
import uuid
from copy import deepcopy
from datetime import datetime
from typing import List, Dict
import xml.etree.ElementTree as ET
import requests

from sbis_auth import get_auth_headers
from catalog_lookup import get_purchase_item

SBIS_URL = "https://online.sbis.ru/service/?srv=1"

# Путь к эталонному входящему УПД (тот XML, что ты загрузил)
TEMPLATE_UPD_PATH = "ON_NSCHFDOPPR__940200200247_20251116_3EFE5FF7-D5B2-421B-9362-66BF0089799A_0_0_0_0_0_00.xml"


def _fmt_qty(x: float) -> str:
    s = f"{float(x):.3f}"
    s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def _to_float_safe(val, default: float = 0.0) -> float:
    """
    Аккуратно приводим к float:
    - режем пробелы
    - меняем запятую на точку
    - пустое => default
    - мусор => предупреждение и default
    """
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip().replace(",", ".")
    if not s:
        return float(default)

    try:
        return float(s)
    except ValueError:
        print(f"[WARN] Не могу привести к float: {val!r}, беру {default}")
        return float(default)


def _fmt_money(x) -> str:
    """
    Деньги в формате 0.00, с точкой. x может быть строкой или числом.
    """
    value = _to_float_safe(x, default=0.0)
    return f"{value:.2f}".replace(",", ".")


def _extract_seller_inn(root) -> str:
    """
    Достаём ИНН продавца из старого ИдФайл, чтобы СБИС распознал поставщика 'Рынок'.
    """
    old_id = root.attrib.get("ИдФайл", "")
    try:
        part = old_id.split("__", 1)[1]
        inn = part.split("_")[0]
        return inn
    except Exception:
        return "940200200247"  # fallback


def build_income_upd_xml(doc_date: str, doc_number: str, daily_items: List[Dict]) -> bytes:
    tree = ET.parse(TEMPLATE_UPD_PATH)
    root = tree.getroot()
    doc = root.find("Документ")

    if doc is None:
        raise RuntimeError("В шаблонном УПД нет <Документ>")

    # --------------------
    # ИдФайл – правильно
    # --------------------
    dt = datetime.strptime(doc_date, "%d.%m.%Y")
    date_for_id = dt.strftime("%Y%m%d")
    new_uuid = str(uuid.uuid4())

    seller_inn = _extract_seller_inn(root)

    root.set(
        "ИдФайл",
        f"ON_NSCHFDOPPR__{seller_inn}_{date_for_id}_{new_uuid}_0_0_0_0_0_00"
    )

    # --------------------
    # Шапка УПД
    # --------------------
    sv_schf = doc.find("СвСчФакт")
    if sv_schf is None:
        raise RuntimeError("В шаблонном УПД нет <СвСчФакт>")

    sv_schf.set("ДатаДок", doc_date)
    sv_schf.set("НомерДок", doc_number)

    # --------------------
    # Табличная часть
    # --------------------
    tbl = doc.find("ТаблСчФакт")
    if tbl is None:
        raise RuntimeError("В шаблонном УПД нет <ТаблСчФакт>")

    row_template = tbl.find("СведТов")
    if row_template is None:
        raise RuntimeError("В шаблонном УПД нет <СведТов>")

    for old in list(tbl.findall("СведТов")):
        tbl.remove(old)

    total_sum = 0.0
    total_qty = 0.0

    for idx, item in enumerate(daily_items, start=1):
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        raw_qty = item.get("qty", "")

        # Кол-во — аккуратно парсим
        if isinstance(raw_qty, (int, float)):
            qty = float(raw_qty)
        else:
            raw_str = str(raw_qty).strip()
            if not raw_str:
                # пустое количество вообще не берём в УПД
                continue
            try:
                qty = float(raw_str.replace(",", "."))
            except ValueError:
                print(f"[WARN] Пропускаю строку '{name}' в УПД, не могу понять количество: {raw_str!r}")
                continue

        if qty == 0:
            # нулевые строки нам в приходе не нужны
            continue

        # Берём данные из каталога
        meta = get_purchase_item(name)
        code = meta["code"]
        unit = meta["unit"]
        okee = meta.get("okeei", "")
        full_name = meta["name"]

        # >>> ТУТ БЫЛА ПРОБЛЕМА <<<
        raw_price = meta.get("price", 0.0)
        price = _to_float_safe(raw_price, default=0.0)

        line_sum = qty * price

        total_sum += line_sum
        total_qty += qty

        new_row = deepcopy(row_template)

        new_row.set("КолТов", _fmt_qty(qty))
        new_row.set("НаимТов", full_name)
        new_row.set("НаимЕдИзм", unit)
        if okee:
            new_row.set("ОКЕИ_Тов", okee)

        new_row.set("НомСтр", str(idx))
        new_row.set("ЦенаТов", _fmt_money(price))
        new_row.set("СтТовБезНДС", _fmt_money(line_sum))
        new_row.set("СтТовУчНал", _fmt_money(line_sum))

        dop = new_row.find("ДопСведТов")
        if dop is None:
            dop = ET.SubElement(new_row, "ДопСведТов", {"КодТов": code, "ПрТовРаб": "1"})
        else:
            dop.set("КодТов", code)

        for inf in list(new_row.findall("ИнфПолФХЖ2")):
            ident = inf.attrib.get("Идентиф")
            if ident == "КодПокупателя":
                inf.set("Значен", code)
            elif ident == "НазваниеПокупателя":
                inf.set("Значен", full_name)

        tbl.append(new_row)

    totals = tbl.find("ВсегоОпл")
    if totals is not None:
        totals.set("КолНеттоВс", _fmt_qty(total_qty))
        totals.set("СтТовБезНДСВсего", _fmt_money(total_sum))
        totals.set("СтТовУчНалВсего", _fmt_money(total_sum))

    xml_bytes = ET.tostring(
        root,
        encoding="windows-1251",
        xml_declaration=True
    )
    return xml_bytes


def send_income_upd(doc_date: str, doc_number: str, daily_items: List[Dict]):
    xml_bytes = build_income_upd_xml(doc_date, doc_number, daily_items)
    xml_b64 = base64.b64encode(xml_bytes).decode("ascii")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "СБИС.ЗаписатьДокумент",
        "params": {
            "Документ": {
                "Тип": "ДокОтгрВх",
                "Направление": "Входящий",
                "Номер": doc_number,
                "Дата": doc_date,
                "Вложение": [
                    {
                        "Идентификатор": str(uuid.uuid4()),
                        "Тип": "УпдДоп",
                        "Подтип": "1115131",
                        "ВерсияФормата": "5.03",
                        "ПодверсияФормата": "",
                        "Название": f"Акт прихода {doc_number}",
                        "Служебный": "Нет",
                        "Дата": doc_date,
                        "Номер": doc_number,
                        "Зашифрован": "Нет",
                        "ТипШифрования": "Отсутствует",
                        "Файл": {
                            "Имя": f"{doc_number}.xml",
                            "ДвоичныеДанные": xml_b64
                        },
                    }
                ],
            }
        },
    }

    headers = get_auth_headers()
    resp = requests.post(SBIS_URL, json=payload, headers=headers, timeout=30)
    return resp.json()


if __name__ == "__main__":
    today = datetime.today().strftime("%d.%m.%Y")
    test_items = [
        {"name": "АНАНАС КОНСЕРВИРОВАННЫЙ", "qty": 0.43},
        {"name": "КРАХМАЛ", "qty": 0.8},
    ]
    print(send_income_upd(today, "TEST-001", test_items))
