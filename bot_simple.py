# bot_simple.py
import os
import time
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
from ocr_gpt import extract_doc_from_image_gpt, correct_items_with_instruction

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("В .env не найден TELEGRAM_BOT_TOKEN")

API_URL = f"https://api.telegram.org/bot{TOKEN}"

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
    return st


def api_get(method: str, params: dict = None):
    resp = requests.get(f"{API_URL}/{method}", params=params, timeout=35)
    return resp.json()


def api_post(method: str, data: dict):
    resp = requests.post(f"{API_URL}/{method}", data=data, timeout=35)
    return resp.json()


def send_message(chat_id: int, text: str):
    api_post("sendMessage", {
        "chat_id": chat_id,
        "text": text,
    })


def format_items(items: List[Dict]) -> str:
    if not items:
        return "Список пуст."
    lines = [f"{i+1}. {it['name']} — {it['qty']}" for i, it in enumerate(items)]
    return "\n".join(lines)


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
        "Привет! Я бот для актов в СБИС.\n\n"
        "Режимы по фото:\n"
        "  В заголовке листа пишешь: Производство / Списание / Приход,\n"
        "  кидаешь фото таблицы с количествами — я определяю тип документа, "
        "распознаю позиции, показываю и спрашиваю: «Все верно?». "
        "Если ответишь «да» — отправлю акт нужного типа в СБИС.\n\n"
        "Можно править текстом:\n"
        "  «тесто не 2, а 3», «измени песто на тесто», «убери крутоны, добавь Крылышки 4».\n\n"
        "Команды:\n"
        "  /list — показать текущий список\n"
        "  /clear — очистить список\n"
        "  /send <номер> [дд.мм.гггг] — вручную отправить акт с текущим списком (тип берётся из последнего фото)\n\n"
        "Можно без фото и команд: напиши Производство/Списание/Приход, затем позиции в формате «Название Количество»,"
        " а для отправки — слово «отправить» (номер/дату придумаю сам)."
    )


def handle_list(chat_id: int):
    st = get_state(chat_id)
    label = DOC_TYPE_LABELS.get(st["doc_type"], st["doc_type"])
    send_message(
        chat_id,
        f"Тип документа: {label}\n"
        "Текущий список:\n" + format_items(st["items"])
    )


def handle_clear(chat_id: int):
    st = get_state(chat_id)
    st["items"] = []
    st["pending_confirm"] = False
    send_message(chat_id, "Список очищен.")

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
    Перед этим чистим список от мусора и предупреждаем о битых строках.
    """
    # Сначала делим позиции на валидные и сломанные
    valid_items, bad_items = split_valid_invalid_items(items)

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


def handle_text(chat_id: int, text: str):
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
        send_message(
            chat_id,
            f"Режим: {label}.\n"
            "Вводи позиции в формате «Название Количество».\n"
            "Когда закончишь — напиши «отправить», я сам поставлю номер и дату."
        )
        return

    # Явный запрос на отправку текущего списка
    if text_lower == "отправить":
        auto_send_act(chat_id)
        return

    # Если ждём подтверждение после OCR
    if st["pending_confirm"]:
        if is_yes(text):
            auto_send_act(chat_id)
            return

        # Иначе — это инструкция для правки
        try:
            new_items = correct_items_with_instruction(st["items"], text)
        except Exception as e:
            send_message(chat_id, f"Не смог применить правку через GPT: {e}")
            return

        st["items"] = new_items
        if not new_items:
            send_message(chat_id, "После правки список пуст. Можешь прислать новую фотку или ввести позиции заново.")
            st["pending_confirm"] = False
            return

        label = DOC_TYPE_LABELS.get(st["doc_type"], st["doc_type"])
        send_message(
            chat_id,
            f"Тип документа: {label}\n"
            "Обновлённый список позиций:\n"
            + format_items(new_items)
            + "\n\nВсе верно?"
        )
        # остаёмся в pending_confirm
        return

    # Обычный режим: ручной ввод «Название Количество»
    parts = text.split()
    if len(parts) < 2:
        send_message(chat_id, "Формат: НАЗВАНИЕ КОЛИЧЕСТВО\nНапример: Тесто 5")
        return

    try:
        qty = float(parts[-1].replace(",", "."))
    except ValueError:
        send_message(chat_id, "Не смог прочитать количество. Пример: Тесто 5")
        return

    name = " ".join(parts[:-1])
    st["items"].append({"name": name, "qty": qty})

    send_message(chat_id, f"Добавил: {name} — {qty}")


def handle_photo(chat_id: int, photos: List[Dict]):
    """
    Обработка фото: скачиваем, отправляем в GPT-OCR, кладём список в state и спрашиваем подтверждение.
    """
    st = get_state(chat_id)

    if not photos:
        return

    # Берём самое большое фото
    photo = photos[-1]
    file_id = photo["file_id"]

    file_info = api_get("getFile", {"file_id": file_id})
    if not file_info.get("ok"):
        send_message(chat_id, f"Не удалось получить файл фото: {file_info}")
        return

    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

    resp = requests.get(file_url, timeout=60)
    if resp.status_code != 200:
        send_message(chat_id, f"Ошибка загрузки фото: HTTP {resp.status_code}")
        return

    tmp_dir = Path("tmp_images")
    tmp_dir.mkdir(exist_ok=True)
    local_path = tmp_dir / f"{chat_id}_{file_id}.jpg"
    with open(local_path, "wb") as f:
        f.write(resp.content)

    send_message(chat_id, "Обрабатываю таблицу на фото через GPT...")

    try:
        doc = extract_doc_from_image_gpt(str(local_path))
    except Exception as e:
        send_message(chat_id, f"Ошибка распознавания таблицы: {e}")
        return

    doc_type = doc.get("doc_type", "production")
    items = doc.get("items", [])
    tables_processed = doc.get("tables_processed", 1)

    if not items:
        send_message(chat_id, "Не нашёл ни одной строки с количеством на фото 😔")
        return

    st["items"] = items
    st["doc_type"] = doc_type
    st["pending_confirm"] = True

    label = DOC_TYPE_LABELS.get(doc_type, doc_type)
    tables_comment = ""
    if tables_processed > 1:
        tables_comment = (
            f"\n(На фото было найдено {tables_processed} таблиц подряд, "
            "я разделил их и объединил результаты.)"
        )
    send_message(
        chat_id,
        f"Тип документа: {label}\n"
        "Нашёл такие позиции:\n"
        + format_items(items)
        + tables_comment
        + "\n\nВсе верно?"
    )


def process_update(update: dict):
    if "message" not in update:
        return

    msg = update["message"]
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    # Фото
    if "photo" in msg:
        handle_photo(chat_id, msg["photo"])
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
