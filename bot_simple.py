# bot_simple.py
import os
import re
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv

from daily_act import (
    send_daily_act,
    send_writeoff_act,
    send_income_act,
)
from catalog_lookup import ProductNotFoundError, MultipleProductsNotFoundError
from voice_handler import transcribe_audio, enhance_transcription_with_gpt

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Не падаем при импорте, если токен не задан (для тестов);
# сетевые функции проверят наличие токена при вызове.
API_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else None

# Память по пользователям:
# items: список позиций
# pending_confirm: ждём ли "да"/правку после распознавания
# doc_type: 'production' | 'writeoff' | 'income'
USER_STATE: Dict[int, Dict] = {}


def get_state(chat_id: int) -> Dict:
    st = USER_STATE.setdefault(chat_id, {})
    st.setdefault("items", [])
    st.setdefault("pending_confirm", False)
    st.setdefault("doc_type", "production")
    st.setdefault("pending_product_choice", None)  # Текущий спорный товар
    st.setdefault("pending_errors_queue", [])  # Очередь остальных спорных товаров
    return st


def api_get(method: str, params: dict = None):
    resp = requests.get(f"{API_URL}/{method}", params=params, timeout=35)
    return resp.json()


def api_post(method: str, data: dict):
    resp = requests.post(f"{API_URL}/{method}", data=data, timeout=35)
    return resp.json()


def send_message(chat_id: int, text: str, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    api_post("sendMessage", data)


def get_control_buttons() -> dict:
    """Возвращает стандартные кнопки управления для всех сообщений."""
    return {
        "inline_keyboard": [
            [
                {"text": "📋 Показать список", "callback_data": "cmd:list"},
                {"text": "🗑 Удалить позицию", "callback_data": "cmd:delete_menu"}
            ],
            [
                {"text": "🧹 Очистить всё", "callback_data": "cmd:clear"},
                {"text": "📤 Отправить", "callback_data": "cmd:send"}
            ]
        ]
    }


def send_message_with_controls(chat_id: int, text: str):
    """Отправляет сообщение со стандартными кнопками управления."""
    send_message(chat_id, text, get_control_buttons())


def send_photo(chat_id: int, photo_path: str, caption: str = None):
    """Отправляет фото в чат."""
    url = f"{API_URL}/sendPhoto"
    with open(photo_path, 'rb') as photo:
        files = {'photo': photo}
        data = {'chat_id': chat_id}
        if caption:
            data['caption'] = caption
        resp = requests.post(url, files=files, data=data, timeout=60)
    return resp.json()


def send_product_choice(chat_id: int, original: str, suggestions: List[tuple], item_index: int, progress: str = None):
    """
    Отправляет сообщение с inline кнопками для выбора похожего товара.
    
    Args:
        original: Оригинальное название (не найдено)
        suggestions: Список (name, score)
        item_index: Индекс товара в списке items для замены
        progress: Прогресс вида "1/3" (опционально)
    """
    text = f"❌ Товар '{original}' не найден в каталоге.\n\n"
    if progress:
        text = f"[{progress}] " + text
    text += "Выберите подходящий вариант:"
    
    # Создаем inline кнопки
    buttons = []
    for idx, (name, score) in enumerate(suggestions[:5], 1):  # Топ-5
        callback_data = json.dumps({
            "action": "replace_product",
            "item_index": item_index,
            "new_name": name
        })
        # Telegram callback_data ограничен 64 байтами, используем короткий формат
        callback_short = f"prod:{item_index}:{idx-1}"
        
        button_text = f"{idx}. {name} ({score:.2f})"
        buttons.append([{"text": button_text, "callback_data": callback_short}])
    
    # Кнопка "Пропустить"
    buttons.append([{"text": "❌ Пропустить этот товар", "callback_data": f"prod:{item_index}:skip"}])
    
    reply_markup = {"inline_keyboard": buttons}
    send_message(chat_id, text, reply_markup)


def transcribe_voice_from_telegram(file_id: str) -> str:
    file_info = api_get("getFile", {"file_id": file_id})
    if not file_info.get("ok"):
        raise RuntimeError(f"Не удалось получить файл голосового: {file_info}")

    file_path = file_info["result"].get("file_path")
    if not file_path:
        raise RuntimeError("Telegram не вернул путь к файлу голосового.")

    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

    resp = requests.get(file_url, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Ошибка загрузки голосового: HTTP {resp.status_code}")

    tmp_dir = Path("tmp_images")
    tmp_dir.mkdir(exist_ok=True)

    suffix = Path(file_path).suffix or ".ogg"
    local_path = tmp_dir / f"voice_{file_id}{suffix}"
    with open(local_path, "wb") as f:
        f.write(resp.content)

    return transcribe_audio(str(local_path))


def format_items(items: List[Dict]) -> str:
    """Форматирует список в виде красивой таблицы."""
    if not items:
        return "Список пуст."
    
    lines = ["📋 Текущий список:\n"]
    lines.append("┌─────┬──────────────────────────┬───────────┐")
    lines.append("│  №  │ Название                 │ Кол-во    │")
    lines.append("├─────┼──────────────────────────┼───────────┤")
    
    for i, it in enumerate(items, 1):
        name = it.get('catalog_name') or it.get('name', '')
        qty = it.get('qty', 0)
        
        # Форматируем для выравнивания
        num_str = f"{i:^3}"
        name_str = f"{name[:24]:<24}"
        qty_str = f"{qty:>9.3f}"
        
        lines.append(f"│ {num_str} │ {name_str} │ {qty_str} │")
    
    lines.append("└─────┴──────────────────────────┴───────────┘")
    
    # Показываем общее количество позиций
    total_qty = sum(it.get('qty', 0) for it in items)
    lines.append(f"\nВсего позиций: {len(items)}, количество: {total_qty:.3f}")
    
    return "\n".join(lines)


def _smart_parse_quantity(parts: list) -> tuple:
    """
    Умный парсинг количества из списка слов.
    Возвращает (name, qty) или (None, None) если не смог распознать.
    
    Обрабатывает случаи:
    - "Ветчина 2" → ("Ветчина", 2.0)
    - "Ветчина 2 0.97" → ("Ветчина", 2.097)  # голосовой ввод "два ноль девяносто семь"
    - "Вода 3 0.33" → ("Вода", 3.033)
    - "Мука 5,5" → ("Мука", 5.5)
    """
    if len(parts) < 2:
        return None, None
    
    # Ищем все числа с конца
    numbers = []
    name_parts = []
    
    for part in reversed(parts):
        try:
            # Пробуем преобразовать в число
            num = float(part.replace(",", "."))
            numbers.append(num)
        except ValueError:
            # Это не число - часть названия
            name_parts.append(part)
            # После первого не-числа все остальное - название
            name_parts.extend(reversed(parts[:len(parts)-len(numbers)-len(name_parts)]))
            break
    
    if not numbers:
        return None, None
    
    name = " ".join(reversed(name_parts)).strip()
    
    if not name:
        return None, None
    
    # Логика объединения чисел
    if len(numbers) == 1:
        # Простой случай: одно число
        qty = numbers[0]
    elif len(numbers) == 2:
        # Два числа: скорее всего голосовой ввод типа "2 0.97" = "два ноль девяносто семь"
        num1, num2 = numbers[1], numbers[0]  # инвертируем обратно (первое идет раньше)
        
        # Если оба числа целые и маленькие - это скорее всего отдельное количество
        # Например "капуста 2, картофель 3" не должно превращаться в "капуста картофель 2.3"
        # Поэтому берем только последнее
        if num1 == int(num1) and num2 == int(num2):
            qty = num2
        # Если первое целое < 100, а второе дробное < 1 - объединяем
        elif num1 < 100 and num1 == int(num1) and 0 < num2 < 1:
            # "2 0.97" → "2.97" (объединяем как дробное число)
            # Переводим в строки и объединяем: "2" + "." + "97"
            qty = float(f"{int(num1)}.{str(num2).split('.')[1]}")
        else:
            # Остальные случаи - берём последнее число
            qty = num2
    else:
        # Больше двух чисел - берём последнее
        qty = numbers[0]
    
    return name, qty


def parse_items_from_text(text: str):
    """Достаём из строки позиции вида "Название Количество", перечисленные через разделители.

    Особенности:
    - Запятая с пробелом после ("1.5, Лук") - разделитель
    - Запятая без пробела в числе ("2,170") - НЕ разделитель
    - Разделяем по точке только когда это конец предложения ("." далее пробел/конец).
    - Очищаем хвостовую пунктуацию у фрагментов.
    - Умный парсинг чисел: "2 0.97" → 2.97, "0,44" → 0.44.
    """

    # Разделители между позициями:
    # - переносы строк
    # - точка с запятой
    # - запятая с пробелом после (но не "2,5" внутри числа)
    # - точка на границе предложения
    separator_regex = r"(?:\n|;|,\s+|\.(?=\s|$))"
    raw_chunks = re.split(separator_regex, text or "")
    chunks = []
    for c in raw_chunks:
        c = c.strip()
        if not c:
            continue
        # Удаляем завершающую пунктуацию
        c = re.sub(r"[\.,;:]+$", "", c).strip()
        if c:
            chunks.append(c)

    items = []
    errors = []

    for chunk in chunks:
        chunk_norm = re.sub(r"\s+", " ", chunk).strip()
        parts = chunk_norm.split()
        name, qty = _smart_parse_quantity(parts)
        if name is None or qty is None:
            errors.append(chunk)
            continue
        items.append({"name": name, "qty": qty})

    return items, errors


DOC_TYPE_LABELS = {
    "production": "Производство",
    "writeoff": "Списание",
    "income": "Приход",
}


def handle_start(chat_id: int):
    st = get_state(chat_id)
    st["items"] = []
    st["pending_confirm"] = False
    st["doc_type"] = "production"

    send_message(
        chat_id,
        "🎯 Привет! Я бот для актов в СБИС через голосовой ввод.\n\n"
        "📝 Как работать:\n"
        "1️⃣ Выбери тип документа: напиши «Производство», «Списание» или «Приход»\n"
        "2️⃣ Добавляй позиции — текстом или голосом в формате «Название Количество»:\n"
        "   • Текст: «Борило 2,5», «Песто 1,2 Крутоны 0,8»\n"
        "   • Голос: просто надикт��й список\n"
        "3️⃣ Когда закончишь — напиши «отправить»\n\n"
        "🎤 Голосовой ввод:\n"
        "  • Работает Whisper + GPT для исправления ошибок\n"
        "  • Можно диктовать несколько позиций подряд\n"
        "  • Дробные числа: «два целых семнадцать» → 2,17\n\n"
        "📋 Команды:\n"
        "  /list — показать текущий список\n"
        "  /clear — очистить список\n"
        "  /send <номер> [дд.мм.гггг] — отправить акт\n\n"
        "💡 Бот автоматически проверяет товары по каталогу и нормализует названия."
    )


def handle_list(chat_id: int):
    st = get_state(chat_id)
    label = DOC_TYPE_LABELS.get(st["doc_type"], st["doc_type"])
    msg = f"📋 Тип: {label}\n\n"
    msg += format_items(st["items"])
    send_message_with_controls(chat_id, msg)


def handle_clear(chat_id: int):
    st = get_state(chat_id)
    st["items"] = []
    st["pending_confirm"] = False
    send_message_with_controls(chat_id, "🧹 Список очищен")


def validate_and_normalize_items(items: List[Dict], doc_type: str) -> tuple:
    """
    Валидирует и нормализует названия товаров в списке.
    Возвращает (validated_items, warnings)
    
    validated_items содержит:
    - name: исходное название из OCR
    - qty: количество
    - catalog_name: нормализованное название из каталога/составов (если найдено)
    
    warnings: список предупреждений о проблемах
    """
    from daily_act import _pick_best_known_names, _parse_item_quantity
    from catalog_lookup import get_purchase_item
    from compositions import build_components_for_output
    
    validated = []
    warnings = []
    
    for idx, item in enumerate(items):
        name_input = str(item.get("name", "")).strip()
        if not name_input:
            continue
        
        qty = _parse_item_quantity(item.get("qty", ""))
        if qty == 0:
            warnings.append(f"• {name_input} — пустое или нулевое количество, пропущено")
            continue
        
        try:
            # Находим лучшее совпадение
            best_match = _pick_best_known_names(name_input)
            best_by_source = best_match.get("by_source", {})
            catalog_name = None
            
            if doc_type == "income":
                # Для прихода используем каталог
                catalog_candidate = best_by_source.get("catalog")
                target_name = catalog_candidate["name"] if catalog_candidate and catalog_candidate.get("name") else name_input
                meta = get_purchase_item(target_name)
                catalog_name = meta["name"]
            else:
                # Для производства/списания пробуем состав, потом каталог
                composition_candidate = best_by_source.get("composition") or best_by_source.get("production")
                recipe_name = composition_candidate["name"] if composition_candidate else name_input
                
                try:
                    recipe = build_components_for_output(recipe_name, output_qty=qty)
                    catalog_name = recipe["parent_name"]
                except Exception:
                    # Нет в составах - пробуем каталог
                    catalog_candidate = best_by_source.get("catalog")
                    target_name = catalog_candidate.get("name") if catalog_candidate and catalog_candidate.get("name") else name_input
                    meta = get_purchase_item(target_name)
                    catalog_name = meta["name"]
            
            validated.append({
                "name": name_input,  # Исходное название
                "qty": qty,
                "catalog_name": catalog_name  # Нормализованное название
            })
            
        except Exception as e:
            # Товар не найден - пробуем найти похожие для подсказки
            from name_matching import find_candidates
            candidates = find_candidates(name_input, limit=3)
            
            validated.append({
                "name": name_input,
                "qty": qty,
                "catalog_name": None  # Не найден в каталоге
            })
            
            if candidates:
                candidates_str = ", ".join([f"'{c['name']}'" for c in candidates[:3]])
                warnings.append(f"⚠️ {name_input} — не найден. Может быть: {candidates_str}?")
            else:
                warnings.append(f"⚠️ {name_input} — не найден в каталоге")
    
    return validated, warnings


def split_valid_invalid_items(items: List[Dict]):
    """
    Делим позиции на:
      - валидные (нормальное количество)
      - проблемные (пусто/мусор/ноль)
    """
    valid = []
    bad = []

    for it in items:
        name = str(it.get("name", "")).strip()
        if not name:
            continue

        raw = it.get("qty", "")
        raw_str = ""
        qty = None

        # Уже число?
        if isinstance(raw, (int, float)):
            qty = float(raw)
            raw_str = str(raw)
        else:
            raw_str = str(raw).strip()
            if not raw_str:
                bad.append({"name": name, "qty_raw": raw_str, "reason": "empty"})
                continue
            try:
                qty = float(raw_str.replace(",", "."))
            except ValueError:
                bad.append({"name": name, "qty_raw": raw_str, "reason": "invalid"})
                continue

        if qty == 0:
            bad.append({"name": name, "qty_raw": raw_str, "reason": "zero"})
            continue

        valid.append({"name": name, "qty": qty})

    return valid, bad


def send_act_by_type(chat_id: int,
                     doc_type: str,
                     doc_date: str,
                     doc_number: str,
                     items: List[Dict]):
    """
    Вызов нужной функции отправки в СБИС по типу документа.
    Теперь items уже содержат валидированные данные с catalog_name.
    """
    # Подготовим items для отправки - используем catalog_name если есть
    prepared_items = []
    for it in items:
        # Если есть catalog_name - используем его, иначе оригинальное name
        item_name = it.get("catalog_name") or it.get("name")
        prepared_items.append({
            "name": item_name,
            "qty": it.get("qty")
        })
    
    # Делим позиции на валидные и сломанные (на всякий случай)
    valid_items, bad_items = split_valid_invalid_items(prepared_items)

    if not valid_items:
        send_message(
            chat_id,
            "Во всех позициях пустое или некорректное количество. "
            "Акт не отправил.\n"
            "Исправь строки (например: «Помидор 1.2») и попробуй ещё раз."
        )
        return

    if bad_items:
        lines = []
        for b in bad_items:
            q = b["qty_raw"] if b["qty_raw"] else "пусто"
            lines.append(f"- {b['name']} (количество: {q})")
        msg = (
            "⚠ Эти позиции я не смог обработать, потому что количество пустое, ноль или непонятное:\n"
            + "\n".join(lines)
            + "\n\nЯ их в акт НЕ отправляю.\n"
            "Если они нужны — введи их заново в формате «Название Количество» "
            "или поправь через фразу типа «помидор не  , а 1.2», и я пересоберу список."
        )
        send_message(chat_id, msg)

    label = DOC_TYPE_LABELS.get(doc_type, doc_type)
    send_message(
        chat_id,
        f"Отправляю акт ({label}) №{doc_number} от {doc_date}.\n"
        f"Позиций: {len(valid_items)}"
    )

    try:
        if doc_type == "production":
            result = send_daily_act(doc_date, doc_number, valid_items)
        elif doc_type == "writeoff":
            result = send_writeoff_act(doc_date, doc_number, valid_items)
        elif doc_type == "income":
            result = send_income_act(doc_date, doc_number, valid_items)
        else:
            # fallback — как производство
            result = send_daily_act(doc_date, doc_number, valid_items)
    except MultipleProductsNotFoundError as e:
        # Несколько товаров не найдены - обрабатываем по очереди
        st = get_state(chat_id)
        
        errors = e.errors
        send_message(chat_id, f"⚠️ Найдено {len(errors)} спорных товаров. Разберём по порядку...")
        
        # Первую ошибку показываем сразу
        first_error = errors[0]
        
        # Остальные в очередь
        st["pending_errors_queue"] = errors[1:]
        
        # Сохраняем контекст выбора первого товара
        st["pending_product_choice"] = {
            "original": first_error.query,
            "suggestions": first_error.suggestions,
            "item_index": first_error.item_index,
            "doc_date": doc_date,
            "doc_number": doc_number,
            "total_errors": len(errors),
            "current_error_num": 1,
        }
        
        # Отправляем кнопки выбора для первого товара
        send_product_choice(
            chat_id, 
            first_error.query, 
            first_error.suggestions, 
            first_error.item_index,
            progress=f"1/{len(errors)}"
        )
        return
    except ProductNotFoundError as e:
        # Один товар не найден (старый путь, на всякий случай)
        st = get_state(chat_id)
        
        st["pending_product_choice"] = {
            "original": e.query,
            "suggestions": e.suggestions,
            "item_index": e.item_index,
            "doc_date": doc_date,
            "doc_number": doc_number,
            "total_errors": 1,
            "current_error_num": 1,
        }
        
        send_product_choice(chat_id, e.query, e.suggestions, e.item_index, progress="1/1")
        return
    except Exception as e:
        send_message(chat_id, f"Ошибка при формировании/отправке акта: {e}")
        return

    if isinstance(result, dict) and "error" in result:
        send_message(chat_id, "СБИС вернул ошибку:\n" + str(result["error"]))
    else:
        send_message(chat_id, "Акт отправлен в СБИС ✅")
        st = get_state(chat_id)
        st["items"] = []
        st["pending_confirm"] = False


def handle_send_manual(chat_id: int, args: List[str]):
    st = get_state(chat_id)
    items = st["items"]
    if not items:
        send_message(chat_id, "Список пуст, нечего отправлять.")
        return

    if not args:
        send_message(chat_id, "Нужен номер акта. Пример: /send 201 16.11.2025")
        return

    doc_number = args[0]
    if len(args) > 1:
        doc_date = args[1]
    else:
        doc_date = datetime.today().strftime("%d.%m.%Y")

    # Здесь автоматически отфильтруем мусор и предупредим, если что
    send_act_by_type(chat_id, st["doc_type"], doc_date, doc_number, items)


def auto_send_act(chat_id: int):
    """
    Автоматическая отправка акта после ответа «да».
    Номер — авто: BOT-ГГГГММДД-ЧЧММСС, дата — сегодня.
    """
    st = get_state(chat_id)
    items = st["items"]
    if not items:
        send_message(chat_id, "Список пуст, нечего отправлять.")
        st["pending_confirm"] = False
        return

    now = datetime.now()
    doc_date = now.strftime("%d.%m.%Y")
    doc_number = now.strftime("BOT-%Y%m%d-%H%M%S")

    # Тут же сработает split_valid_invalid_items, бот предупредит о кривых строках
    send_act_by_type(chat_id, st["doc_type"], doc_date, doc_number, items)


def handle_command(chat_id: int, text: str):
    parts = text.split()
    cmd = parts[0]
    args = parts[1:]

    if cmd == "/start":
        handle_start(chat_id)
    elif cmd == "/list":
        handle_list(chat_id)
    elif cmd == "/clear":
        handle_clear(chat_id)
    elif cmd == "/send":
        handle_send_manual(chat_id, args)
    else:
        send_message(chat_id, "Неизвестная команда.")


def is_yes(text: str) -> bool:
    t = text.strip().lower()
    return t in {
        "да", "да.", "да!", "верно", "все верно", "всё верно",
        "ок", "окей", "ага", "угу", "да, все верно", "да, всё верно"
    }


def handle_voice(chat_id: int, voice: Dict):
    file_id = voice.get("file_id")
    if not file_id:
        return

    send_message(chat_id, "🎤 Распознаю голосовое...")

    try:
        # Шаг 1: Распознаем через Whisper
        raw_text = transcribe_voice_from_telegram(file_id)
        
        # Шаг 2: Улучшаем через GPT (исправляем ошибки)
        enhanced_text = enhance_transcription_with_gpt(raw_text)
        
        text = enhanced_text
    except Exception as e:
        send_message(chat_id, f"❌ Не смог распознать голосовое: {e}")
        return

    if not text:
        send_message(chat_id, "В голосовом не разобрал текст.")
        return

    send_message_with_controls(chat_id, f"✓ Распознал:\n{text}")
    handle_text(chat_id, text)


def handle_text(chat_id: int, text: str):
    from edit_commands import parse_edit_command, apply_edit_command
    
    st = get_state(chat_id)
    text = text.strip()
    text_lower = text.lower()

    # Команда?
    if text.startswith("/"):
        handle_command(chat_id, text)
        return

    # Быстрая смена типа документа текстом
    if text_lower in {"производство", "списание", "приход"}:
        new_doc_type = {
            "производство": "production",
            "списание": "writeoff",
            "приход": "income",
        }[text_lower]
        st["doc_type"] = new_doc_type
        st["items"] = []
        st["pending_confirm"] = False

        label = DOC_TYPE_LABELS.get(new_doc_type, new_doc_type)
        msg = f"✅ Режим: {label}\n\n"
        msg += "Теперь можешь:\n"
        msg += "• Диктовать позиции: «Борило 2,5 Песто 1,2»\n"
        msg += "• Редактировать: «удали последнюю», «лука не 7 а 0,7»\n"
        msg += "• Отправить: нажми кнопку или напиши «отправить»"
        send_message_with_controls(chat_id, msg)
        return

    # Явный запрос на отправку текущего списка
    if text_lower == "отправить":
        auto_send_act(chat_id)
        return

    # Быстрая проверка: если текст содержит несколько чисел - это добавление позиций
    numbers_count = len(re.findall(r'\d+[.,]?\d*', text))
    
    # Пробуем распознать как команду редактирования только если:
    # 1) Уже есть позиции в списке
    # 2) И текст НЕ выглядит как список позиций (не больше 2 чисел)
    if st["items"] and numbers_count <= 2:
        edit_cmd = parse_edit_command(text, st["items"])
        
        if edit_cmd and edit_cmd.get("action") not in ["unknown", "add"]:
            new_items, result_msg = apply_edit_command(edit_cmd, st["items"])
            
            # Специальный случай: добавление новых позиций
            if result_msg.startswith("add:"):
                items_to_add = json.loads(result_msg[4:])
                # Валидируем новые позиции
                send_message(chat_id, "Проверяю новые позиции...")
                try:
                    validated, warnings = validate_and_normalize_items(items_to_add, st["doc_type"])
                    if validated:
                        st["items"].extend(validated)
                        msg = "✅ Добавил:\n" + format_items(st["items"])
                        if warnings:
                            msg += "\n\n⚠️ " + "\n".join(warnings)
                        send_message_with_controls(chat_id, msg)
                    else:
                        send_message_with_controls(chat_id, "❌ Не смог добавить позиции")
                except Exception as e:
                    send_message_with_controls(chat_id, f"❌ Ошибка: {e}")
                return
            
            # Обычное редактирование
            st["items"] = new_items
            
            # Если команда rename - нужно ревалидировать
            if edit_cmd.get("action") == "rename":
                send_message(chat_id, "Проверяю новое название...")
                try:
                    validated, warnings = validate_and_normalize_items(new_items, st["doc_type"])
                    st["items"] = validated
                    result_msg += "\n\n" + format_items(validated)
                    if warnings:
                        result_msg += "\n\n⚠️ " + "\n".join(warnings)
                except Exception as e:
                    result_msg += f"\n❌ Ошибка валидации: {e}"
            else:
                result_msg += "\n\n" + format_items(new_items)
            
            send_message_with_controls(chat_id, result_msg)
            return

    # Обычный режим: парсим как добавление позиций «Название Количество»
    items, errors = parse_items_from_text(text)
    if not items:
        send_message(
            chat_id,
            "Не смог прочитать позиции. Формат: НАЗВАНИЕ КОЛИЧЕСТВО\n"
            "Можно перечислять через запятую: «капуста 2, картофель 3».",
        )
        return

    # Валидируем введённые позиции через каталог
    send_message(chat_id, "Проверяю по каталогу...")
    
    valid_items = []
    invalid_items = []
    
    for item in items:
        name_input = item["name"]
        qty = item["qty"]
        
        try:
            # Пробуем валидировать
            validated, warnings = validate_and_normalize_items([item], st["doc_type"])
            if validated and validated[0].get("catalog_name"):
                # Нашли в каталоге
                valid_items.append(validated[0])
            else:
                # Не нашли - запомним для показа кнопок
                invalid_items.append(item)
        except Exception:
            # Ошибка валидации - запомним для показа кнопок
            invalid_items.append(item)
    
    # Добавляем валидированные позиции
    if valid_items:
        st["items"].extend(valid_items)
        msg = "✅ Добавил:\n" + format_items(st["items"])
        send_message_with_controls(chat_id, msg)
    
    # Для невалидированных показываем кнопки с вариантами
    for item in invalid_items:
        from name_matching import find_candidates
        candidates = find_candidates(item["name"], limit=5)
        
        if candidates:
            # Показываем кнопки с вариантами
            item_index = len(st["items"])
            st["items"].append(item)  # Добавляем временно
            send_product_choice(chat_id, item["name"], candidates, item_index)
        else:
            send_message(chat_id, f"⚠️ Товар '{item['name']}' не найден в каталоге и нет похожих.")
    
    if errors:
        msg = "❌ Не разобрал строки:\n" + "\n".join(f"- {e}" for e in errors)
        send_message(chat_id, msg)





def handle_callback_query(callback_query: dict):
    """Обработка нажатий на inline кнопки."""
    query_id = callback_query.get("id")
    data = callback_query.get("data", "")
    from_user = callback_query.get("from") or {}
    chat_id = from_user.get("id")
    message = callback_query.get("message") or {}
    
    if not chat_id:
        return
    
    # Подтверждаем получение callback
    api_post("answerCallbackQuery", {"callback_query_id": query_id})
    
    st = get_state(chat_id)
    
    # Команды управления: cmd:action
    if data.startswith("cmd:"):
        action = data.split(":")[1]
        
        if action == "list":
            label = DOC_TYPE_LABELS.get(st["doc_type"], st["doc_type"])
            msg = f"📋 Тип: {label}\n\n"
            msg += format_items(st["items"])
            send_message_with_controls(chat_id, msg)
            return
        
        elif action == "clear":
            st["items"] = []
            send_message_with_controls(chat_id, "🧹 Список очищен")
            return
        
        elif action == "delete_menu":
            if not st["items"]:
                send_message_with_controls(chat_id, "Список пуст, нечего удалять")
                return
            
            # Показываем кнопки с позициями для удаления
            buttons = []
            for i, item in enumerate(st["items"]):
                name = item.get("catalog_name") or item.get("name")
                qty = item.get("qty", 0)
                button_text = f"{i+1}. {name} ({qty})"
                buttons.append([{"text": button_text, "callback_data": f"del:{i}"}])
            
            buttons.append([{"text": "❌ Отмена", "callback_data": "cmd:list"}])
            
            send_message(chat_id, "Выбери позицию для удаления:", {"inline_keyboard": buttons})
            return
        
        elif action == "send":
            auto_send_act(chat_id)
            return
    
    # Удаление позиции: del:index
    if data.startswith("del:"):
        index = int(data.split(":")[1])
        if 0 <= index < len(st["items"]):
            removed = st["items"].pop(index)
            name = removed.get("catalog_name") or removed.get("name")
            msg = f"✓ Удалил: {name}\n\n"
            msg += format_items(st["items"])
            send_message_with_controls(chat_id, msg)
        else:
            send_message_with_controls(chat_id, "❌ Позиция не найдена")
        return
    
    # Выбор товара из каталога: prod:item_index:choice_index
    if data.startswith("prod:"):
        parts = data.split(":")
        if len(parts) != 3:
            return
    
    _, item_index_str, choice = parts
    item_index = int(item_index_str)
    
    st = get_state(chat_id)
    choice_ctx = st.get("pending_product_choice")
    
    if not choice_ctx:
        send_message(chat_id, "⚠️ Контекст выбора потерян. Попробуй заново.")
        return
    
    suggestions = choice_ctx["suggestions"]
    
    if choice == "skip":
        # Пропускаем товар - удаляем из списка
        if 0 <= item_index < len(st["items"]):
            removed_item = st["items"].pop(item_index)
            send_message(chat_id, f"❌ Товар '{removed_item['name']}' пропущен.")
        
        # Проверяем, есть ли ещё спорные товары в очереди
        if st.get("pending_errors_queue"):
            next_error = st["pending_errors_queue"].pop(0)
            current_num = choice_ctx.get("current_error_num", 1) + 1
            total = choice_ctx.get("total_errors", 1)
            
            st["pending_product_choice"] = {
                "original": next_error.query,
                "suggestions": next_error.suggestions,
                "item_index": next_error.item_index,
                "doc_date": choice_ctx["doc_date"],
                "doc_number": choice_ctx["doc_number"],
                "total_errors": total,
                "current_error_num": current_num,
            }
            
            send_product_choice(
                chat_id,
                next_error.query,
                next_error.suggestions,
                next_error.item_index,
                progress=f"{current_num}/{total}"
            )
            return
        
        # Очередь пуста - пробуем отправить акт
        st["pending_product_choice"] = None
        
        if st["items"]:
            send_message(chat_id, "Пробую отправить акт с оставшимися товарами...")
            send_act_by_type(
                chat_id,
                st["doc_type"],
                choice_ctx["doc_date"],
                choice_ctx["doc_number"],
                st["items"]
            )
        else:
            send_message(chat_id, "Список товаров пуст. Добавь товары заново.")
        return
    
    # Выбран конкретный вариант
    choice_idx = int(choice)
    if 0 <= choice_idx < len(suggestions):
        chosen_name, score = suggestions[choice_idx]
        
        # Заменяем название в списке
        if 0 <= item_index < len(st["items"]):
            old_name = st["items"][item_index]["name"]
            st["items"][item_index]["name"] = chosen_name
            send_message(
                chat_id,
                f"✅ Заменено:\n'{old_name}' → '{chosen_name}' (score: {score:.2f})"
            )
        
        # Проверяем, есть ли ещё спорные товары в очереди
        if st.get("pending_errors_queue"):
            next_error = st["pending_errors_queue"].pop(0)
            current_num = choice_ctx.get("current_error_num", 1) + 1
            total = choice_ctx.get("total_errors", 1)
            
            st["pending_product_choice"] = {
                "original": next_error.query,
                "suggestions": next_error.suggestions,
                "item_index": next_error.item_index,
                "doc_date": choice_ctx["doc_date"],
                "doc_number": choice_ctx["doc_number"],
                "total_errors": total,
                "current_error_num": current_num,
            }
            
            send_product_choice(
                chat_id,
                next_error.query,
                next_error.suggestions,
                next_error.item_index,
                progress=f"{current_num}/{total}"
            )
            return
        
        # Очередь пуста - отправляем акт
        st["pending_product_choice"] = None
        
        send_message(chat_id, "✅ Все товары обработаны! Отправляю акт...")
        send_act_by_type(
            chat_id,
            st["doc_type"],
            choice_ctx["doc_date"],
            choice_ctx["doc_number"],
            st["items"]
        )


def process_update(update: dict):
    # Обработка callback от inline кнопок
    if "callback_query" in update:
        handle_callback_query(update["callback_query"])
        return
    
    if "message" not in update:
        return

    msg = update["message"]
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    # Голосовые
    if "voice" in msg:
        handle_voice(chat_id, msg["voice"])
        return

    # Фото не поддерживаются
    if "photo" in msg:
        send_message(chat_id, "📵 Распознавание фото отключено. Используй голосовой или текстовый ввод.")
        return

    # Текст
    text = msg.get("text")
    if text:
        handle_text(chat_id, text)


def main():
    print("Бот запущен. Нажми Ctrl+C, чтобы остановить.")
    offset = None

    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset

            data = api_get("getUpdates", params)
            if not data.get("ok"):
                print("Ошибка ответа Telegram:", data)
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                process_update(update)

        except KeyboardInterrupt:
            print("Остановка бота.")
            break
        except Exception as e:
            print("Ошибка в основном цикле:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
