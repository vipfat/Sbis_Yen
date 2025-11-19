# ocr_gpt.py
import base64
import io
import os
import json
import re
from collections import Counter
from typing import List, Dict, Tuple, Optional

from dotenv import load_dotenv
from openai import OpenAI
import numpy as np
from PIL import Image

# Загружаем переменные окружения
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("В .env не найден OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


def encode_image(image_path: str, crop_box: Optional[Tuple[int, int, int, int]] = None) -> str:
    """
    Преобразуем картинку (или её часть) в base64 для передачи в GPT.
    crop_box — (left, top, right, bottom) как в PIL.Image.crop.
    """
    if crop_box is None:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    with Image.open(image_path) as img:
        cropped = img.crop(crop_box)
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=95)
        return base64.b64encode(buf.getvalue()).decode("utf-8")


def detect_table_regions(
    image_path: str,
    content_threshold: float = 0.015,
    min_table_height: int = 200,
    min_gap_height: int = 50,
    margin: int = 10,
) -> List[Tuple[int, int, int, int]]:
    """
    Поиск горизонтально расположенных таблиц на листе.
    Возвращает список crop-box (left, top, right, bottom).
    Если найти отдельные таблицы не получилось, возвращает весь лист.
    """
    try:
        with Image.open(image_path) as img:
            gray = img.convert("L")
            width, height = img.size
            arr = np.array(gray)
    except Exception:
        # Если не получилось загрузить — пусть дальше отработает весь лист целиком
        with Image.open(image_path) as img:
            width, height = img.size
        return [(0, 0, width, height)]

    # Оцениваем «насыщенность» каждой строки: сколько пикселей не похоже на фон
    row_activity = (arr < 230).mean(axis=1)
    has_content = row_activity > content_threshold

    boxes: List[Tuple[int, int, int, int]] = []
    start = None
    gap = 0

    def flush_segment(end_row: int):
        nonlocal start
        if start is None:
            return
        if end_row - start < min_table_height:
            start = None
            return
        top = max(start - margin, 0)
        bottom = min(end_row + margin, height - 1)
        boxes.append((0, top, width, bottom + 1))  # нижняя граница в PIL эксклюзивна
        start = None

    for y, active in enumerate(has_content):
        if active:
            if start is None:
                start = y
            gap = 0
        else:
            if start is not None:
                gap += 1
                if gap >= min_gap_height:
                    flush_segment(y - gap)
                    gap = 0

    if start is not None:
        flush_segment(len(has_content) - 1)

    if len(boxes) <= 1:
        return [(0, 0, width, height)]

    return boxes


def _normalize_doc_result(raw: Dict) -> Dict:
    doc_type = raw.get("doc_type", "").strip()
    items_raw = raw.get("items", [])

    if doc_type not in ("production", "writeoff", "income"):
        doc_type = "production"

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

    return {"doc_type": doc_type, "items": items}


def _extract_single_table(image_path: str, crop_box: Optional[Tuple[int, int, int, int]] = None) -> Dict:
    b64 = encode_image(image_path, crop_box)

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

2) Извлечь из таблицы строки с полуфабрикатами:
   - первая колонка: название полуфабриката (например: "Тесто", "Крутоны")
   - в последних колонках — количество (может быть написано от руки или напечатано)
   - нужно брать ТОЛЬКО те строки, где количество реально указано (не пусто)

Нужно вернуть JSON-объект строго такого вида:

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

    return _normalize_doc_result(raw)


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


def extract_doc_from_image_gpt(image_path: str) -> Dict:
    """
    Отправляет фото таблицы в GPT.
    Если на фото несколько таблиц подряд, они будут автоматически выделены,
    распознаны по отдельности и объединены в один список.
    """
    boxes = detect_table_regions(image_path)

    if len(boxes) <= 1:
        result = _extract_single_table(image_path, None)
        result["tables_processed"] = 1
        return result

    doc_type_counter: Counter = Counter()
    combined_items: List[Dict] = []

    for box in boxes:
        part_doc = _extract_single_table(image_path, box)
        doc_type_counter[part_doc.get("doc_type", "production")] += 1
        combined_items.extend(part_doc.get("items", []))

    dominant_doc_type = (
        doc_type_counter.most_common(1)[0][0]
        if doc_type_counter
        else "production"
    )

    return {
        "doc_type": dominant_doc_type,
        "items": combined_items,
        "tables_processed": len(boxes),
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
