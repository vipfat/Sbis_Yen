import os
import base64
import json
from datetime import datetime

from dotenv import load_dotenv
from pysaby import SABYManager
from inspect_act import read_act
from build_act_xml import build_act_xml_from_template

load_dotenv()

LOGIN = os.getenv("SBIS_LOGIN")
PASSWORD = os.getenv("SBIS_PASSWORD")

if not LOGIN or not PASSWORD:
    raise RuntimeError("В .env нет SBIS_LOGIN / SBIS_PASSWORD")

manager = SABYManager(LOGIN, PASSWORD)

# Исходный акт, который ты сделал руками
TEMPLATE_ACT_ID = "54f4dabe-9b30-4626-8a5e-dcc545ecfb32"
# Исходный XML, который мы скачали (с 1 порцией наггетсов)
TEMPLATE_XML = "act_accounting.xml"


def create_act_vypuska(code_to_qty: dict):
    """
    code_to_qty: { 'X5237747': 3, ... }
    Делает новый XML на основе act_accounting.xml и создаёт новый АктВыпуска.
    """
    today = datetime.now().strftime("%d.%m.%Y")

    # 1. Строим новый XML
    new_xml_path = "act_generated.xml"
    build_act_xml_from_template(
        template_xml_path=TEMPLATE_XML,
        code_to_qty=code_to_qty,
        out_xml_path=new_xml_path,
        doc_date=today,
    )

    # 2. Берём шаблонный акт как источник реквизитов (орг, подразделение, регламент, автор)
    template = read_act(TEMPLATE_ACT_ID)

    org = template.get("НашаОрганизация")
    subdivision = template.get("Подразделение")
    author = template.get("Автор")
    responsible = template.get("Ответственный")
    reglament = template.get("Регламент")

    # 3. Читаем новый XML и кодируем в base64
    with open(new_xml_path, "rb") as f:
        xml_bytes = f.read()
    xml_b64 = base64.b64encode(xml_bytes).decode("ascii")

    new_doc = {
        "Тип": "АктВыпуска",
        "Подтип": "АктВыпуска",
        "Направление": "Внутренний",
        "Дата": today,
        "Номер": "",  # пусть СБИС сам присвоит номер
        "НашаОрганизация": org,
        "Подразделение": subdivision,
        "Автор": author,
        "Ответственный": responsible,
        "Регламент": reglament,
        "Примечание": f"Создан через API (автовыпуск, коды: {', '.join(code_to_qty.keys())})",
        "ВложениеУчета": [
            {
                "Тип": "АктВыпуска",
                "Подтип": "АктВыпуска",
                "Служебный": "Нет",
                "Файл": {
                    "Имя": "act_vypuska_api.xml",
                    "ДвоичныеДанные": xml_b64,
                },
            }
        ],
    }

    payload = {"Документ": new_doc}

    print("Отправляем СБИС.ЗаписатьДокумент с телом:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    res = manager.send_query("СБИС.ЗаписатьДокумент", payload)

    print("\nОтвет СБИС:")
    print(json.dumps(res, ensure_ascii=False, indent=2))

    return res


if __name__ == "__main__":
    # Тест: выпустить 5 порций наггетсов (X5237747)
    create_act_vypuska({"X5237747": 5})
