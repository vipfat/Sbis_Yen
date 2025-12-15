# edit_commands.py
"""
Модуль для обработки команд редактирования списка через GPT.
"""

import json
from typing import Dict, List, Optional
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

_OPENAI_CLIENT = None


def _get_openai_client() -> OpenAI:
    """Ленивая инициализация OpenAI клиента."""
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY не установлен в .env")
        _OPENAI_CLIENT = OpenAI(api_key=api_key)
    return _OPENAI_CLIENT


def parse_edit_command(text: str, current_items: List[Dict]) -> Optional[Dict]:
    """
    Определяет тип команды редактирования и извлекает параметры через GPT.
    
    Args:
        text: Текст команды ("удали последнюю позицию", "лука не 7 а 0.7")
        current_items: Текущий список позиций [{"name": str, "qty": float}, ...]
        
    Returns:
        {
            "action": "delete_last" | "delete_by_name" | "change_qty" | "rename" | "add" | "unknown",
            "params": {...}  # зависит от действия
        }
        или None если это не команда редактирования
    """
    client = _get_openai_client()
    
    # Формируем список для контекста
    items_text = "\n".join([f"{i+1}. {it['name']} — {it['qty']}" for i, it in enumerate(current_items)])
    
    prompt = f"""Ты — система распознавания команд редактирования списка товаров.

Текущий список:
{items_text}

Команда пользователя: "{text}"

Определи ТИП действия и извлеки параметры. Верни JSON:

{{
  "action": "<тип>",
  "params": {{...}}
}}

Типы действий:
1. "delete_last" - удалить последнюю позицию
   params: {{}}
   
2. "delete_by_name" - удалить товар по названию
   params: {{"name": "название товара"}}
   Примеры: "убери картофель", "удали лук"
   
3. "change_qty" - изменить количество
   params: {{"name": "товар", "old_qty": число, "new_qty": число}}
   Примеры: "лука не 7 а 0.7", "борило было 2 теперь 3"
   
4. "rename" - переименовать товар
   params: {{"old_name": "старое", "new_name": "новое"}}
   Примеры: "не тесто а песто", "измени тесто на песто"
   
5. "add" - добавить позиции (обычный формат "Название Количество")
   params: {{"items": [{{"name": "...", "qty": число}}, ...]}}
   Примеры: "Борило 2.5", "Лук 1.2 Картофель 3"
   
6. "unknown" - непонятная команда или обычное добавление позиций
   params: {{"reason": "это просто список товаров для добавления"}}

ВАЖНО:
- Если команда начинается с "не X а Y" или "X не N а M" — это rename или change_qty
- Если текст содержит много пар "Название Число" — это НЕ команда, верни "unknown"
- Команды редактирования обычно содержат слова: удали, убери, измени, не...а
- Числа с запятой заменяй на точку: 0,7 → 0.7
- Названия товаров пиши с заглавной буквы
- Если в тексте есть название из списка — используй его точно

Верни ТОЛЬКО JSON, без пояснений."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=500
        )
        
        result_text = response.choices[0].message.content.strip()
        result = json.loads(result_text)
        
        return result
        
    except Exception as e:
        print(f"[EDIT_CMD] Ошибка парсинга команды: {e}")
        return None


def apply_edit_command(command: Dict, items: List[Dict]) -> tuple[List[Dict], str]:
    """
    Применяет команду редактирования к списку.
    
    Returns:
        (new_items, message) - обновленный список и сообщение о результате
    """
    action = command.get("action")
    params = command.get("params", {})
    
    if action == "delete_last":
        if not items:
            return items, "❌ Список пуст, нечего удалять"
        removed = items[-1]
        new_items = items[:-1]
        return new_items, f"✓ Удалил последнюю позицию: {removed['name']}"
    
    elif action == "delete_by_name":
        target_name = params.get("name", "").upper()
        new_items = []
        deleted = []
        
        for item in items:
            item_name = item.get("catalog_name") or item.get("name")
            if target_name in item_name.upper():
                deleted.append(item_name)
            else:
                new_items.append(item)
        
        if deleted:
            return new_items, f"✓ Удалил: {', '.join(deleted)}"
        else:
            return items, f"❌ Не нашел товар '{params.get('name')}' в списке"
    
    elif action == "change_qty":
        target_name = params.get("name", "").upper()
        new_qty = float(params.get("new_qty", 0))
        updated = False
        
        new_items = []
        for item in items:
            item_name = item.get("catalog_name") or item.get("name")
            if target_name in item_name.upper():
                item_copy = item.copy()
                old_qty = item_copy["qty"]
                item_copy["qty"] = new_qty
                new_items.append(item_copy)
                updated = True
                msg = f"✓ Изменил {item_name}: {old_qty} → {new_qty}"
            else:
                new_items.append(item)
        
        if updated:
            return new_items, msg
        else:
            return items, f"❌ Не нашел товар '{params.get('name')}' в списке"
    
    elif action == "rename":
        old_name = params.get("old_name", "").upper()
        new_name = params.get("new_name", "")
        updated = False
        
        new_items = []
        for item in items:
            item_name = item.get("catalog_name") or item.get("name")
            if old_name in item_name.upper():
                item_copy = item.copy()
                item_copy["name"] = new_name
                # Сбрасываем catalog_name чтобы пройти валидацию заново
                item_copy.pop("catalog_name", None)
                new_items.append(item_copy)
                updated = True
                msg = f"✓ Переименовал {item_name} → {new_name}"
            else:
                new_items.append(item)
        
        if updated:
            return new_items, msg
        else:
            return items, f"❌ Не нашел товар '{params.get('old_name')}' в списке"
    
    elif action == "add":
        # Возвращаем новые позиции для добавления (они пройдут валидацию в основном коде)
        new_items_to_add = params.get("items", [])
        if new_items_to_add:
            # items остаются без изменений, новые позиции добавятся отдельно
            return items, f"add:{json.dumps(new_items_to_add)}"  # Специальный маркер
        return items, "❌ Нет позиций для добавления"
    
    else:
        return items, f"❓ Не понял команду. {params.get('reason', '')}"
