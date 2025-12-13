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


def _log_ocr_result(image_path: str, gpt_response: str):
    """Логируем результат OCR для отладки."""
    from datetime import datetime
    from pathlib import Path
    
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "ocr_tables.log"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    img_name = Path(image_path).name
    
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Image: {img_name}\n")
        f.write(f"GPT Response:\n{gpt_response}\n")
        f.write("=" * 80 + "\n\n")


def transcribe_audio(file_path: str) -> str:
    """Преобразуем аудио-файл (голосовое) в текст через Whisper."""

    with open(file_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="ru",
        )

    text = getattr(result, "text", None)
    if isinstance(result, dict):
        text = text or result.get("text")

    if not text:
        raise RuntimeError("Не удалось распознать речь в голосовом сообщении.")

    return text.strip()


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
Ты — профессиональная система распознавания таблиц для кафе.

На фото — лист с таблицей и заголовком. В заголовке будет написано ОДНО из слов:
- "Производство"
- "Списание" 
- "Приход"

Твоя задача:

1) ОПРЕДЕЛИ ТИП ДОКУМЕНТА по заголовку:
   - "production" — если написано "Производство"
   - "writeoff" — если написано "Списание"
   - "income" — если написано "Приход"

2) ИЗВЛЕКИ СТРОКИ ТАБЛИЦЫ с максимальной точностью:
   
   КРИТИЧЕСКИ ВАЖНО для больших таблиц:
   - Читай таблицу СТРОГО ПО СТРОКАМ (слева направо, сверху вниз)
   - НЕ перескакивай между колонками вертикально!
   - КАЖДАЯ строка содержит: [Название] ... [Количество]
   - Название ВСЕГДА в ПЕРВОЙ (левой) колонке
   - Количество ВСЕГДА в ПОСЛЕДНЕЙ (правой) колонке той же строки
   
   Алгоритм обработки каждой строки:
   a) Найди название в первой колонке
   b) В ТОЙ ЖЕ строке найди количество в последней колонке
   c) Если количество пустое/прочерк — ПРОПУСТИ эту строку
   d) НЕ бери количество из другой строки!
   
   Проверка перед добавлением позиции:
   - Название и количество из ОДНОЙ строки?
   - Количество — это число (не текст, не прочерк)?
   - Позиция имеет смысл (не пустое название)?

   Если в строке несколько чисел — бери ПОСЛЕДНЕЕ (итоговое).

ПРОВЕРЬ СЕБЯ перед ответом:
- Каждая позиция: название и количество из одной строки?
- Нет перемешивания данных из разных строк?
- Количества — это числа, а не текст?

Верни JSON-объект СТРОГО такого вида:

{
  "doc_type": "production" | "writeoff" | "income",
  "items": [
    {"name": "Тесто", "qty": 5},
    {"name": "Крутоны", "qty": 2}
  ]
}

Где:
- name — чистое название полуфабриката (без мусора, пробелов по краям)
- qty — число (float), без единиц измерения

Если ничего не найдено: {"doc_type": "...", "items": []}
Только JSON, никакого лишнего текста.
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
    
    # Логируем сырой ответ GPT
    _log_ocr_result(image_path, text)
    
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
