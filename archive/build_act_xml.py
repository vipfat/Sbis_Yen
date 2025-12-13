import xml.etree.ElementTree as ET
from typing import Dict, Optional
from datetime import datetime


def _scale_value_str(old_str: str, ratio: float) -> str:
    """
    Умножает строковое число old_str на ratio
    и возвращает строку с тем же количеством знаков после запятой.
    """
    old_str = old_str.strip()
    if not old_str:
        return old_str

    # Определяем разделитель и количество знаков после запятой
    if "." in old_str:
        sep = "."
    elif "," in old_str:
        sep = ","
    else:
        # без дробной части
        try:
            new_val = float(old_str) * ratio
        except ValueError:
            return old_str
        return str(new_val)

    int_part, frac_part = old_str.split(sep, 1)
    decimals = len(frac_part)

    try:
        old_val = float(old_str.replace(",", "."))
    except ValueError:
        return old_str

    new_val = old_val * ratio
    fmt = f"{{:.{decimals}f}}"
    out = fmt.format(new_val)

    # Возвращаем с тем же разделителем
    if sep == ",":
        out = out.replace(".", ",")
    return out


def build_act_xml_from_template(
    template_xml_path: str,
    code_to_qty: Dict[str, float],
    out_xml_path: str,
    doc_date: Optional[str] = None,
):
    """
    Берём шаблонный XML акта выпуска (Формат='АктВып'),
    ищем строки <СтрТабл Идентификатор="код"> и подставляем новые Кол_во/Сумма.

    code_to_qty = {
        "X5237747": 5.0,
        ...
    }

    doc_date — если передан, то записываем его в атрибут Дата у <Документ>.
    Формат даты: '15.11.2025'
    """

    tree = ET.parse(template_xml_path)
    root = tree.getroot()

    # 1. Обновляем дату документа (если нужно)
    # <Документ Дата="15.11.2025" Номер="158">
    doc_el = root.find("Документ")
    if doc_el is not None and doc_date:
        doc_el.set("Дата", doc_date)

    # 2. Ищем таблицу и строки
    # <ТаблДок> <СтрТабл ... Идентификатор="X5237747" Кол_во="1" ...>
    table_el = None
    if doc_el is not None:
        table_el = doc_el.find("ТаблДок")

    if table_el is None:
        raise RuntimeError("Не найден узел <ТаблДок> в XML акта.")

    count_changed = 0

    for row in table_el.findall("СтрТабл"):
        code = row.get("Идентификатор")
        if not code or code not in code_to_qty:
            continue

        new_qty = float(code_to_qty[code])

        old_qty_str = row.get("Кол_во", "0")
        try:
            old_qty = float(old_qty_str.replace(",", "."))
        except ValueError:
            old_qty = 0.0

        if old_qty <= 0:
            # Если в шаблоне 0 или криво — просто ставим новое значение без масштабирования
            ratio = 0.0
        else:
            ratio = new_qty / old_qty

        # Обновляем количество по строке
        row.set("Кол_во", str(new_qty))

        # Масштабируем сумму по строке
        sum_str = row.get("Сумма")
        if sum_str and ratio:
            row.set("Сумма", _scale_value_str(sum_str, ratio))

        # 3. Состав строки: <СоставСтрТабл ... Кол_во="0.02" Кол_во_План="0.02" Сумма="1.12"/>
        for comp in row.findall("СоставСтрТабл"):
            for attr in ("Кол_во", "Кол_во_План", "Сумма"):
                val = comp.get(attr)
                if val and ratio:
                    comp.set(attr, _scale_value_str(val, ratio))

        count_changed += 1

    if count_changed == 0:
        raise RuntimeError(
            f"Не найдено ни одной строки <СтрТабл> с Идентификатором из: {list(code_to_qty.keys())}"
        )

    # Пишем в WINDOWS-1251, как в исходном файле
    tree.write(out_xml_path, encoding="windows-1251", xml_declaration=True)
    print(
        f"XML с обновлёнными количествами сохранён в {out_xml_path}, "
        f"изменено строк: {count_changed}"
    )


if __name__ == "__main__":
    # Пример: 3 порции наггетсов вместо 1
    today = datetime.now().strftime("%d.%m.%Y")
    build_act_xml_from_template(
        template_xml_path="act_accounting.xml",
        code_to_qty={"X5237747": 3},
        out_xml_path="act_accounting_3_nuggets.xml",
        doc_date=today,
    )
