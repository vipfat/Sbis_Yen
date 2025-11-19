# ocr_gpt.py
import base64
import io
import os
import json
import re
import logging
from collections import Counter
from pathlib import Path
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


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOGGER = logging.getLogger("ocr_tables")
if not LOGGER.handlers:
    handler = logging.FileHandler(LOG_DIR / "ocr_tables.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)


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


def _segment_axis(
    activity_mask: np.ndarray,
    min_length: int,
    min_gap: int,
    margin: int,
    limit: int,
) -> List[Tuple[int, int]]:
    """Находит активные непрерывные участки с запасом по краям."""

    segments: List[Tuple[int, int]] = []
    start: Optional[int] = None
    gap = 0

    for idx, active in enumerate(activity_mask):
        if active:
            if start is None:
                start = idx
            gap = 0
            continue

        if start is None:
            continue

        gap += 1
        if gap >= min_gap:
            end = idx - gap
            length = end - start
            if length >= min_length:
                seg_start = max(start - margin, 0)
                seg_end = min(end + margin + 1, limit)
                segments.append((seg_start, seg_end))
            start = None
            gap = 0

    if start is not None:
        end = len(activity_mask) - 1
        length = end - start
        if length >= min_length:
            seg_start = max(start - margin, 0)
            seg_end = min(end + margin + 1, limit)
            segments.append((seg_start, seg_end))

    return segments


def detect_table_regions(
    image_path: str,
    content_threshold: float = 0.015,
    min_table_height: int = 200,
    min_gap_height: int = 50,
    margin: int = 10,
    column_threshold: float = 0.02,
    min_column_width: int = 150,
    min_column_gap: int = 40,
) -> List[Tuple[int, int, int, int]]:
    """
    Поиск таблиц на листе. Работает как по горизонтали, так и по вертикали,
    чтобы корректно обрабатывать листы с несколькими колонками таблиц.
    Возвращает список crop-box (left, top, right, bottom).
    """

    try:
        with Image.open(image_path) as img:
            gray = img.convert("L")
            width, height = img.size
            arr = np.array(gray)
    except Exception:
        with Image.open(image_path) as img:
            width, height = img.size
        return [(0, 0, width, height)]

    row_activity = (arr < 230).mean(axis=1)
    has_row_content = row_activity > content_threshold
    row_segments = _segment_axis(
        has_row_content, min_table_height, min_gap_height, margin, height
    )
    if not row_segments:
        row_segments = [(0, height)]

    boxes: List[Tuple[int, int, int, int]] = []

    for top, bottom in row_segments:
        slice_arr = arr[top:bottom, :]
        if slice_arr.size == 0:
            continue
        column_activity = (slice_arr < 230).mean(axis=0)
        has_column_content = column_activity > column_threshold
        column_segments = _segment_axis(
            has_column_content, min_column_width, min_column_gap, margin, width
        )
        if not column_segments:
            boxes.append((0, top, width, bottom))
            continue
        for left, right in column_segments:
            boxes.append((left, top, right, bottom))

    if not boxes:
        return [(0, 0, width, height)]

    return boxes


def save_table_crops(image_path: str, boxes: List[Tuple[int, int, int, int]]) -> List[str]:
    """Сохраняет нарезанные кусочки таблиц для отладки."""

    saved_paths: List[str] = []
    if not boxes:
        return saved_paths

    parts_dir = Path("tmp_images") / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(image_path) as img:
            stem = Path(image_path).stem
            for idx, box in enumerate(boxes, start=1):
                crop = img.crop(box)
                out_path = parts_dir / f"{stem}_part{idx}.jpg"
                crop.save(out_path, format="JPEG", quality=95)
                saved_paths.append(str(out_path))
    except Exception as exc:
        LOGGER.warning(
            "Не удалось сохранить вырезанные таблицы для %s: %s", image_path, exc
        )

    return saved_paths


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

    saved_parts: List[str] = []
    if len(boxes) > 1:
        saved_parts = save_table_crops(image_path, boxes)
        LOGGER.info(
            "Фото %s разделено на %d частей. Кусочки: %s",
            image_path,
            len(boxes),
            ", ".join(saved_parts) if saved_parts else "не удалось сохранить вырезки",
        )

    if len(boxes) <= 1:
        result = _extract_single_table(image_path, boxes[0] if boxes else None)
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
