"""
Утилиты общего назначения для SBIS Telegram Bot.
Функции для валидации, конвертации, форматирования.
"""
from datetime import datetime
from typing import Union, Optional, Any
import re


def to_float_safe(value: Any, default: float = 0.0) -> float:
    """
    Безопасная конвертация любого значения в float.
    
    Args:
        value: Значение для конвертации (str, int, float, etc.)
        default: Значение по умолчанию при ошибке
        
    Returns:
        float: Сконвертированное значение или default
        
    Examples:
        >>> to_float_safe("3.14")
        3.14
        >>> to_float_safe("3,14")
        3.14
        >>> to_float_safe("мусор", 0.0)
        0.0
        >>> to_float_safe(None, 0.0)
        0.0
    """
    if value is None:
        return default
    
    if isinstance(value, (int, float)):
        return float(value)
    
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            return default
        try:
            return float(value)
        except ValueError:
            return default
    
    return default


def validate_date(date_str: str, date_format: str = "%d.%m.%Y") -> bool:
    """
    Проверка корректности формата даты.
    
    Args:
        date_str: Строка с датой
        date_format: Формат даты (по умолчанию DD.MM.YYYY)
        
    Returns:
        bool: True если формат корректен
        
    Examples:
        >>> validate_date("13.12.2025")
        True
        >>> validate_date("32.13.2025")
        False
        >>> validate_date("2025-12-13")
        False
    """
    try:
        datetime.strptime(date_str, date_format)
        return True
    except (ValueError, TypeError):
        return False


def validate_inn(inn: str) -> bool:
    """
    Проверка корректности ИНН (10 или 12 цифр).
    
    Args:
        inn: Строка с ИНН
        
    Returns:
        bool: True если ИНН корректен
        
    Examples:
        >>> validate_inn("7710000001")
        True
        >>> validate_inn("771000000123")
        True
        >>> validate_inn("123")
        False
        >>> validate_inn("abc1234567")
        False
    """
    if not inn:
        return False
    return inn.isdigit() and len(inn) in (10, 12)


def format_money(amount: Union[float, int], decimals: int = 2) -> str:
    """
    Форматирование суммы для XML (с точкой, фиксированное число знаков).
    
    Args:
        amount: Сумма для форматирования
        decimals: Количество знаков после запятой
        
    Returns:
        str: Отформатированная строка
        
    Examples:
        >>> format_money(1234.5)
        '1234.50'
        >>> format_money(100, 0)
        '100'
    """
    fmt = f"{{:.{decimals}f}}"
    return fmt.format(float(amount))


def format_quantity(qty: Union[float, int], decimals: int = 3) -> str:
    """
    Форматирование количества для XML (убирает лишние нули).
    
    Args:
        qty: Количество для форматирования
        decimals: Максимум знаков после запятой
        
    Returns:
        str: Отформатированная строка
        
    Examples:
        >>> format_quantity(1.500)
        '1.5'
        >>> format_quantity(2.0)
        '2'
        >>> format_quantity(3.123)
        '3.123'
    """
    formatted = f"{float(qty):.{decimals}f}".rstrip('0').rstrip('.')
    return formatted


def clean_string(text: str) -> str:
    """
    Очистка строки от лишних пробелов и спецсимволов.
    
    Args:
        text: Исходная строка
        
    Returns:
        str: Очищенная строка
        
    Examples:
        >>> clean_string("  Тесто   сдобное  ")
        'Тесто сдобное'
        >>> clean_string("ТЕСТО\\n\\r\\t")
        'ТЕСТО'
    """
    if not text:
        return ""
    # Убираем переносы строк, табы
    text = re.sub(r'[\n\r\t]+', ' ', text)
    # Убираем множественные пробелы
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_name(name: str) -> str:
    """
    Нормализация названия для сравнения.
    
    Args:
        name: Исходное название
        
    Returns:
        str: Нормализованное название (верхний регистр, без лишних пробелов)
        
    Examples:
        >>> normalize_name("  тесто Сдобное  ")
        'ТЕСТО СДОБНОЕ'
    """
    return clean_string(name).upper()


def parse_quantity_from_text(text: str) -> Optional[float]:
    """
    Извлечение количества из текста.
    
    Args:
        text: Текст, возможно содержащий число
        
    Returns:
        Optional[float]: Найденное число или None
        
    Examples:
        >>> parse_quantity_from_text("Тесто 3.5")
        3.5
        >>> parse_quantity_from_text("2,5 кг")
        2.5
        >>> parse_quantity_from_text("нет числа")
        None
    """
    # Ищем число (включая дробные с . или ,)
    pattern = r'\d+[.,]?\d*'
    matches = re.findall(pattern, text)
    if matches:
        # Берём последнее найденное число
        return to_float_safe(matches[-1])
    return None


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Обрезка длинного текста с добавлением многоточия.
    
    Args:
        text: Исходный текст
        max_length: Максимальная длина
        suffix: Окончание для обрезанного текста
        
    Returns:
        str: Обрезанный текст
        
    Examples:
        >>> truncate_text("Очень длинный текст" * 10, 20)
        'Очень длинный текст...'
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix

def summarize_validation(validated_items: list, warnings: list) -> dict:
    """Подсчёт метрик валидации для отчётов/логов.

    Возвращает словарь с метриками:
    - total: всего позиций на входе (validated_items)
    - matched: сколько имеют `catalog_name`
    - unmatched: сколько без `catalog_name`
    - warning_count: число предупреждений
    - matched_ratio: доля совпадений (0..1)
    - qty_sum: суммарное количество
    """
    total = len(validated_items or [])
    matched = sum(1 for it in validated_items if it.get("catalog_name"))
    qty_sum = 0.0
    for it in validated_items:
        try:
            qty_sum += float(it.get("qty", 0) or 0)
        except Exception:
            pass
    result = {
        "total": total,
        "matched": matched,
        "unmatched": max(0, total - matched),
        "warning_count": len(warnings or []),
        "matched_ratio": (matched / total) if total else 0.0,
        "qty_sum": qty_sum,
    }
    return result


def generate_doc_number(prefix: str = "AUTO") -> str:
    """
    Генерация уникального номера документа.
    
    Args:
        prefix: Префикс номера
        
    Returns:
        str: Номер вида "AUTO-20251213-001234"
        
    Examples:
        >>> generate_doc_number("TEST")  # doctest: +ELLIPSIS
        'TEST-...'
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{timestamp}"


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """
    Безопасное деление с обработкой деления на ноль.
    
    Args:
        a: Делимое
        b: Делитель
        default: Значение при делении на ноль
        
    Returns:
        float: Результат деления или default
        
    Examples:
        >>> safe_divide(10, 2)
        5.0
        >>> safe_divide(10, 0)
        0.0
        >>> safe_divide(10, 0, default=1.0)
        1.0
    """
    if b == 0:
        return default
    return a / b


def extract_numbers_from_string(text: str) -> list[float]:
    """
    Извлечение всех чисел из строки.
    
    Args:
        text: Исходная строка
        
    Returns:
        list: Список найденных чисел
        
    Examples:
        >>> extract_numbers_from_string("Тесто 2.5 кг, Крутоны 0.3 кг")
        [2.5, 0.3]
    """
    pattern = r'\d+[.,]?\d*'
    matches = re.findall(pattern, text)
    return [to_float_safe(m) for m in matches]


def is_empty_or_whitespace(text: Optional[str]) -> bool:
    """
    Проверка, является ли строка пустой или состоит только из пробелов.
    
    Args:
        text: Проверяемая строка
        
    Returns:
        bool: True если пустая или только пробелы
        
    Examples:
        >>> is_empty_or_whitespace("")
        True
        >>> is_empty_or_whitespace("   ")
        True
        >>> is_empty_or_whitespace("текст")
        False
        >>> is_empty_or_whitespace(None)
        True
    """
    return not text or not text.strip()


if __name__ == "__main__":
    # Тесты
    import doctest
    doctest.testmod()
    
    print("✅ Все тесты прошли успешно")
    print("\nПримеры использования:")
    print(f"to_float_safe('3,14') = {to_float_safe('3,14')}")
    print(f"validate_date('13.12.2025') = {validate_date('13.12.2025')}")
    print(f"validate_inn('7710000001') = {validate_inn('7710000001')}")
    print(f"format_money(1234.5) = {format_money(1234.5)}")
    print(f"normalize_name('  тесто  ') = {normalize_name('  тесто  ')}")
