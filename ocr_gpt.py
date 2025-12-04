# ocr_gpt.py
import base64
import os
import json
import re
from typing import List, Dict

from dotenv import load_dotenv
from openai import OpenAI

# Загружаем переменные окружения
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("В .env не найден OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


def encode_image(image_path: str) -> str:
    """Преобразуем картинку в base64 для передачи в GPT."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _parse_json_strict_or_relaxed(text: str):
    """
    Пытаемся аккуратно вытащить JSON из ответа GPT.
    Сначала пробуем как есть, потом ищем { ... } или [ ... ].
    """
    text = (text or "").strip()

    # Пробуем как есть
    try:
        return json.loads(text)
    except Exception:
        pass

    # Ищем JSON-объект или массив
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError(f"GPT вернул невалидный JSON:\n{text}")
    return json.loads(m.group(0))


def transcribe_audio_file(audio_path: str) -> str:
    """Преобразует аудио/голосовое сообщение в текст через Whisper."""
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="ru",
            response_format="text",
        )
    return (result or "").strip()


def extract_doc_from_image_gpt(image_path: str) -> Dict:
    """
    Отправляет фото таблицы в GPT и возвращает структуру:
    {
      "doc_type": "production" | "writeoff" | "income",
      "items": [ {"name": "...", "qty": float}, ... ]
    }
    """
    b64 = encode_image(image_path)

    prompt = """
Ты — система распознавания таблиц для кафе.

На фото находится лист с таблицей и заголовком. В заголовке листа (или рядом с таблицей)
будет написано ОДНО из слов:

- "Производство"
- "Списание"
- "Приход"

Твоя задача:

1) Определить тип документа по заголовку и вернуть одно из значений:
   - "production"  — если в заголовке написано "Производство"
   - "writeoff"    — если в заголовке написано "Списание"
   - "income"      — если в заголовке написано "Приход"

2) Извлечь строки таблицы с полуфабрикатами:
   - читаем ТАБЛИЦУ ПО СТРОКАМ слева направо, не перескакивая между колонками;
   - первая колонка: название полуфабриката (например: "Тесто", "Крутоны");
   - в последних колонках — итоговое количество для этой же строки (рукописное или печатное);
   - берём ТОЛЬКО строки, где количество явно указано (не пусто, не прочерк);
   - если несколько чисел в строке, выбирай конечное значение количества (обычно самый правый столбец);
   - не перемешивай данные между строками: название и количество должны принадлежать одной строке.

Перед ответом мысленно проверь каждую строку: совпадают ли название и количество в одной строке,
не перепутаны ли соседние значения. Нужна максимальная точность без ручных правок.

Верни JSON-объект строго такого вида:

{
  "doc_type": "production" | "writeoff" | "income",
  "items": [
    {"name": "Тесто", "qty": 5},
    {"name": "Крутоны", "qty": 2}
  ]
}

Где:
- name — название полуфабриката (читаемое, без мусора)
- qty — число (float), без единиц измерения
Если ничего не найдено, верни: {"doc_type": "...", "items": []}
Никакого лишнего текста кроме JSON.
"""

    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        },
                    },
                ],
            }
        ],
        max_tokens=700,
        temperature=0,
    )

    text = response.choices[0].message.content or ""
    raw = _parse_json_strict_or_relaxed(text)

    if not isinstance(raw, dict):
        raise RuntimeError(f"Ожидался JSON-объект, а пришло:\n{text}")

    doc_type = raw.get("doc_type", "").strip()
    items_raw = raw.get("items", [])

    # Нормализуем doc_type
    if doc_type not in ("production", "writeoff", "income"):
        # если GPT тупанул — по умолчанию считаем производством
        doc_type = "production"

    # Нормализуем список позиций
    items: List[Dict] = []
    if isinstance(items_raw, list):
        for it in items_raw:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "")).strip()
            qty = it.get("qty")
            if not name:
                continue
            try:
                qty_val = float(qty)
            except (TypeError, ValueError):
                continue
            if qty_val == 0:
                continue
            items.append({"name": name, "qty": qty_val})

    return {
        "doc_type": doc_type,
        "items": items,
    }


def correct_items_with_instruction(items: List[Dict], instruction: str) -> List[Dict]:
    """
    Принимает текущий список items и текстовую инструкцию (на русском),
    возвращает НОВЫЙ список items после правок.
    """
    items_json = json.dumps(items, ensure_ascii=False)

    prompt = (
        "Ты — помощник по редактированию списка блюд и количеств для акта "
        "выпуска/списания/прихода.\n\n"
        "Вот текущий список позиций в формате JSON:\n"
        f"{items_json}\n\n"
        "Пользователь пишет на естественном русском языке, например:\n"
        "- \"измени песто на тесто\"\n"
        "- \"тесто не 2, а 3\"\n"
        "- \"убери крутоны, добавь Крылышки 4\"\n"
        "- \"добавь Тесто 5\"\n\n"
        "Твоя задача:\n"
        "1. Понять, что хочет пользователь.\n"
        "2. Аккуратно применить изменения к списку.\n"
        "3. Вернуть ОБНОВЛЁННЫЙ список в ТОЧНО ТАКОМ ЖЕ JSON-ФОРМАТЕ — "
        "массив объектов с полями name (строка) и qty (число).\n\n"
        "Важные правила:\n"
        "- Верни только JSON-массив, без пояснений и текста.\n"
        "- name — строка, qty — число (float).\n"
        "- Если нужно что-то удалить — просто не включай это в итоговый массив.\n\n"
        f"Инструкция пользователя:\n{instruction}\n"
    )

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        max_tokens=500,
        temperature=0,
    )

    text = response.choices[0].message.content or ""
    raw_items = _parse_json_strict_or_relaxed(text)

    if not isinstance(raw_items, list):
        raise RuntimeError(f"Ожидался JSON-массив после правки, а пришло:\n{text}")

    norm_items: List[Dict] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "")).strip()
        qty = it.get("qty")
        if not name:
            continue
        try:
            qty_val = float(qty)
        except (TypeError, ValueError):
            continue
        norm_items.append({"name": name, "qty": qty_val})

    return norm_items


def parse_items_from_freeform(text: str) -> List[Dict]:
    """
    Пытается выделить пары «название — количество» из произвольного текста
    (включая голосовое, распознанное в текст). Поддерживает перечисление
    нескольких позиций подряд и запись количества словами.
    """

    def try_simple_parse(line: str) -> List[Dict]:
        pairs: List[Dict] = []
        chunks = re.split(r"[\n;,]+", line)
        for chunk in chunks:
            part = chunk.strip()
            if not part:
                continue
            tokens = part.split()
            if len(tokens) < 2:
                continue
            try:
                qty_val = float(tokens[-1].replace(",", "."))
            except ValueError:
                continue
            name_val = " ".join(tokens[:-1]).strip()
            if not name_val:
                continue
            pairs.append({"name": name_val, "qty": qty_val})
        return pairs

    # Сначала пробуем выделить явные пары «слово ... число» без GPT
    simple_items = try_simple_parse(text)
    if simple_items:
        return simple_items

    prompt = (
        "Ты помогаешь составлять список позиций (название + количество).\n"
        "Пользователь может написать или продиктовать сразу несколько строк подряд,\n"
        "в том числе без запятых и переносов. Количество может быть числом или\n"
        "написано словами. Нужно вернуть JSON-массив объектов вида \"name\" + \"qty\"."\n
        "Правила:\n"
        "- Вытяни все пары (название, количество), даже если они перечислены подряд.\n"
        "- Преобразуй числительные словами в число. Пример: \"три\" → 3, \"один ноль два\" → 1.02.\n"
        "- Если текста про позиции нет — верни пустой массив [].\n"
        "- Никаких пояснений, только JSON-массив.\n\n"
        f"Текст пользователя:\n{text}\n"
    )

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0,
    )

    ai_text = response.choices[0].message.content or ""
    raw_items = _parse_json_strict_or_relaxed(ai_text)

    if not isinstance(raw_items, list):
        return []

    items: List[Dict] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "")).strip()
        qty = it.get("qty")
        if not name:
            continue
        try:
            qty_val = float(qty)
        except (TypeError, ValueError):
            continue
        if qty_val == 0:
            continue
        items.append({"name": name, "qty": qty_val})

    return items
