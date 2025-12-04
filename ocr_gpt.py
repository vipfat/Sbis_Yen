# ocr_gpt.py
import base64
import os
import json
import re
from collections import Counter
from io import BytesIO
from typing import List, Dict, Iterable

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

# Загружаем переменные окружения
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("В .env не найден OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


def encode_image_pil(image: Image.Image) -> str:
    """Преобразуем PIL-изображение в base64 (JPEG) для передачи в GPT."""
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


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


def _split_image_to_blocks(image_path: str, target_height: int = 1200, overlap: int = 80) -> List[Image.Image]:
    """
    Делим изображение на вертикальные блоки с перекрытием, чтобы улучшить OCR.

    target_height — целевая высота блока; overlap — перекрытие блоков (px),
    чтобы строки на границе не потерялись.
    """
    img = Image.open(image_path).convert("RGB")
    width, height = img.size

    blocks: List[Image.Image] = []
    y = 0
    while y < height:
        y_end = min(y + target_height, height)
        y0 = max(0, y - overlap // 2)
        y1 = min(height, y_end + overlap // 2)
        blocks.append(img.crop((0, y0, width, y1)))
        y = y_end

    return blocks


def _merge_items(block_items: Iterable[Dict]) -> List[Dict]:
    """
    Объединяем позиции из разных блоков, избегая дублирования.

    Дубликаты определяются по названию (без регистра). Если количества отличаются,
    берём значение с большим модулем, чтобы сохранить финальную цифру из табличной строки.
    """
    merged = {}
    for item in block_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        qty = item.get("qty")
        if not name:
            continue
        try:
            qty_val = float(qty)
        except (TypeError, ValueError):
            continue

        key = name.lower()
        if key in merged:
            existing_qty = merged[key]["qty"]
            if abs(qty_val) > abs(existing_qty):
                merged[key] = {"name": name, "qty": qty_val}
        else:
            merged[key] = {"name": name, "qty": qty_val}

    return list(merged.values())


def _normalize_items(items_raw) -> List[Dict]:
    """Приводим сырой ответ GPT по одному фрагменту к стабильному списку."""
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
    return items


def extract_doc_from_image_gpt(image_path: str) -> Dict:
    """
    Отправляет фото таблицы в GPT и возвращает структуру:
    {
      "doc_type": "production" | "writeoff" | "income",
      "items": [ {"name": "...", "qty": float}, ... ]
    }
    """
    blocks = _split_image_to_blocks(image_path)
    total_blocks = len(blocks)

    base_prompt = """
Ты — система распознавания таблиц для кафе.

Работаешь по шагам: изображение заранее поделено на несколько вертикальных фрагментов.
На каждом фрагменте может быть только часть таблицы. Тебе нужно аккуратно разобрать
ТОЛЬКО тот фрагмент, который сейчас отправлен, чтобы потом объединить результаты без дублирования.

На листе есть таблица и заголовок. В заголовке (или рядом с таблицей) будет одно из слов:

- "Производство"
- "Списание"
- "Приход"

Твоя задача по каждому фрагменту:

1) Определить тип документа по заголовку, если он виден на фрагменте, и вернуть одно из значений:
   - "production"  — если в заголовке написано "Производство"
   - "writeoff"    — если в заголовке написано "Списание"
   - "income"      — если в заголовке написано "Приход"
   Если заголовка не видно на текущем фрагменте — сделай осторожное предположение по контексту.

2) Извлечь строки таблицы с полуфабрикатами ТОЛЬКО из этого фрагмента:
   - читай таблицу по строкам слева направо, не перескакивай между колонками;
   - первая колонка: название полуфабриката (например: "Тесто", "Крутоны");
   - в последних колонках — итоговое количество для этой же строки (рукописное или печатное);
   - бери только строки, где количество явно указано (не пусто, не прочерк);
   - если несколько чисел в строке, выбирай конечное значение количества (обычно самый правый столбец);
   - не перемешивай данные между строками: название и количество принадлежат одной строке;
   - не выдумывай и не дублируй строки, которых нет на фрагменте.

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

    doc_type_votes: Counter = Counter()
    collected_items: List[Dict] = []

    for idx, block in enumerate(blocks, start=1):
        block_prompt = (
            base_prompt
            + "\n\nРаботаешь с фрагментом {idx}/{total}.".format(
                idx=idx, total=total_blocks
            )
            + " Учти, что соседние фрагменты обрабатываются отдельно, поэтому"
            " не добавляй лишние строки и не дублируй то, чего нет на изображении."
        )

        b64 = encode_image_pil(block)
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": block_prompt},
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
            raise RuntimeError(
                f"Ожидался JSON-объект для блока {idx}, а пришло:\n{text}"
            )

        doc_type = raw.get("doc_type", "").strip()
        items_raw = raw.get("items", [])

        if doc_type not in ("production", "writeoff", "income"):
            doc_type = "production"

        doc_type_votes[doc_type] += 1
        collected_items.extend(_normalize_items(items_raw))

    final_doc_type = doc_type_votes.most_common(1)
    doc_type = final_doc_type[0][0] if final_doc_type else "production"

    items = _merge_items(collected_items)

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
