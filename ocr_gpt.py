# ocr_gpt.py
import base64
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from dotenv import load_dotenv
from openai import OpenAI

# Загружаем переменные окружения
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("В .env не найден OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


def _load_image(image_path: str):
    path = Path(image_path)
    image = cv2.imread(str(path))
    return path, image


def _find_table_regions(binary: np.ndarray) -> List[Tuple[int, int]]:
    """Ищем крупные горизонтальные блоки с данными (таблицы)."""

    h, w = binary.shape[:2]
    if h == 0:
        return []

    projection = np.sum(binary > 0, axis=1)
    blank_threshold = max(1, int(0.01 * w))
    is_blank = projection <= blank_threshold

    min_gap = max(25, int(0.015 * h))
    separators: List[Tuple[int, int]] = []
    i = 0
    while i < h:
        if is_blank[i]:
            j = i
            while j < h and is_blank[j]:
                j += 1
            if (j - i) >= min_gap:
                separators.append((i, j))
            i = j
        else:
            i += 1

    if not separators:
        return []

    regions: List[Tuple[int, int]] = []
    start = 0
    for sep_start, sep_end in separators:
        regions.append((start, sep_start))
        start = sep_end
    regions.append((start, h))

    min_table_height = max(120, int(0.12 * h))
    filtered = [r for r in regions if (r[1] - r[0]) >= min_table_height]

    if len(filtered) >= 2:
        return filtered

    # Если не нашли два крупных блока — считаем, что таблица одна
    return []


def split_image_into_tables(image_path: str) -> Tuple[List[Path], Optional[Path]]:
    """Определяет количество таблиц и при необходимости режет фото на части."""

    path, image = _load_image(image_path)
    if image is None:
        return [path], None

    h, w = image.shape[:2]
    if h < 400 or w < 400:
        return [path], None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = 255 - binary

    regions = _find_table_regions(binary)
    if len(regions) < 2:
        return [path], None

    padding = max(15, int(0.01 * h))
    tmp_dir = Path(tempfile.mkdtemp(prefix="tables_", dir=str(path.parent)))
    saved_paths: List[Path] = []
    base_name = path.stem

    for idx, (top, bottom) in enumerate(regions, start=1):
        t = max(0, top - padding)
        b = min(h, bottom + padding)
        crop = image[t:b, :]
        out_path = tmp_dir / f"{base_name}_part{idx}.jpg"
        cv2.imwrite(str(out_path), crop)
        saved_paths.append(out_path)

    return saved_paths, tmp_dir


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


def _extract_doc_from_image_gpt_single(image_path: str) -> Dict:
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


def extract_doc_from_image_gpt(image_path: str) -> Dict:
    """Распознаёт таблицу (или несколько таблиц подряд) и объединяет результат."""

    parts, tmp_dir = split_image_into_tables(image_path)
    doc_type_counter: Counter = Counter()
    combined_items: List[Dict] = []
    detected_types: List[str] = []

    try:
        for part in parts:
            doc = _extract_doc_from_image_gpt_single(str(part))
            doc_type = doc.get("doc_type", "production")
            detected_types.append(doc_type)
            weight = max(1, len(doc.get("items", [])))
            doc_type_counter[doc_type] += weight
            combined_items.extend(doc.get("items", []))
    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if doc_type_counter:
        final_doc_type = doc_type_counter.most_common(1)[0][0]
    elif detected_types:
        final_doc_type = detected_types[0]
    else:
        final_doc_type = "production"

    return {
        "doc_type": final_doc_type,
        "items": combined_items,
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
