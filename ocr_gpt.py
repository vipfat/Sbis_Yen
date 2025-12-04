# ocr_gpt.py
import base64
import os
import json
import re
import importlib.util
from io import BytesIO
from typing import List, Dict

from dotenv import load_dotenv
from openai import OpenAI
from catalog_lookup import resolve_purchase_name

_pillow_spec = importlib.util.find_spec("PIL")
if _pillow_spec:
    from PIL import Image
else:
    Image = None  # type: ignore[assignment]

# Загружаем переменные окружения
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("В .env не найден OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# Более свежая модель для плотных таблиц
VISION_MODEL = "gpt-4o"


def encode_image(image_path: str) -> str:
    """Преобразуем картинку в base64 для передачи в GPT."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def encode_image_bytes(image: "Image.Image") -> str:
    """Конвертирует PIL-изображение в base64 (JPEG)."""
    if Image is None:
        raise RuntimeError("Для разбивки изображения нужен Pillow. Установи пакет Pillow.")

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def split_image_into_tiles(
    image: "Image.Image", tile_size: int = 1400, overlap: int = 100
) -> List["Image.Image"]:
    """
    Делит изображение на перекрывающиеся тайлы, чтобы не терять строки
    на больших таблицах. Если картинка и так небольшая, вернёт её одну.
    """
    if Image is None:
        return [image]
    width, height = image.size
    if width <= tile_size and height <= tile_size:
        return [image]

    step = tile_size - overlap
    tiles: List[Image.Image] = []

    for top in range(0, height, step):
        for left in range(0, width, step):
            right = min(left + tile_size, width)
            bottom = min(top + tile_size, height)
            tiles.append(image.crop((left, top, right, bottom)))

    return tiles


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
    Отправляет фото таблицы в GPT и возвращает структуру:
    {
      "doc_type": "production" | "writeoff" | "income",
      "items": [ {"name": "...", "qty": float}, ... ]
    }
    """
    def _request_vision(b64_image: str) -> Dict:
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
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=700,
            temperature=0,
        )

        text = response.choices[0].message.content or ""
        raw_resp = _parse_json_strict_or_relaxed(text)

        if not isinstance(raw_resp, dict):
            raise RuntimeError(f"Ожидался JSON-объект, а пришло:\n{text}")

        return raw_resp

    def _normalize_items(items_raw: List[Dict]) -> List[Dict]:
        items: List[Dict] = []
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

    def _filter_catalog(items: List[Dict]) -> List[Dict]:
        """Фильтруем позиции по Каталогу, чтобы отсеять шум."""
        filtered: List[Dict] = []
        for it in items:
            try:
                canonical = resolve_purchase_name(it["name"])
            except Exception:  # noqa: BLE001
                continue
            filtered.append({"name": canonical, "qty": it["qty"]})
        return filtered

    if Image is None:
        # Фолбэк: Pillow недоступен, шлём цельное изображение, как раньше
        raw_responses: List[Dict] = [_request_vision(encode_image(image_path))]
    else:
        image = Image.open(image_path).convert("RGB")
        tiles = split_image_into_tiles(image)

        raw_responses = []
        for tile in tiles:
            b64_tile = encode_image_bytes(tile)
            raw_responses.append(_request_vision(b64_tile))

    doc_type_candidates = [
        str(r.get("doc_type", "")).strip() for r in raw_responses if isinstance(r, dict)
    ]
    items_raw: List[Dict] = []
    for r in raw_responses:
        tile_items = r.get("items", []) if isinstance(r, dict) else []
        if isinstance(tile_items, list):
            items_raw.extend(tile_items)

    doc_type = next(
        (dt for dt in doc_type_candidates if dt in ("production", "writeoff", "income")),
        "production",
    )

    items = _normalize_items(items_raw)
    filtered_items = _filter_catalog(items)

    return {
        "doc_type": doc_type,
        "items": filtered_items,
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
