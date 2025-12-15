"""
OCR с использованием Tesseract для распознавания таблиц.
Более быстрый и дешевый альтернативный метод для GPT Vision.
"""
import re
import cv2
import numpy as np
import pytesseract
from typing import List, Dict, Optional, Tuple
from pathlib import Path


def preprocess_image(image_path: str) -> np.ndarray:
    """
    Предобработка изображения для улучшения качества OCR.
    
    Применяет:
    - Увеличение контраста (CLAHE)
    - Бинаризацию (Otsu)
    - Удаление шума
    - Выравнивание (deskew) если нужно
    
    Returns:
        Обработанное изображение (numpy array)
    """
    # Читаем изображение
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Не удалось прочитать изображение: {image_path}")
    
    # Конвертируем в grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # CLAHE для улучшения контраста
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    
    # Удаление шума
    denoised = cv2.fastNlMeansDenoising(enhanced, None, h=10, templateWindowSize=7, searchWindowSize=21)
    
    # Бинаризация (Otsu)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    return binary


def extract_table_structure(image: np.ndarray) -> Dict[int, List[Dict]]:
    """
    Извлекает структурированные данные из изображения таблицы.
    
    Returns:
        Dict[line_num, List[{text, conf, x, y, w, h}]]
    """
    # Tesseract с детальными данными
    custom_config = r'--oem 3 --psm 6 -l rus+eng'
    data = pytesseract.image_to_data(image, config=custom_config, output_type=pytesseract.Output.DICT)
    
    # Группируем по строкам таблицы
    lines = {}
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        conf = int(data['conf'][i])
        
        # Пропускаем пустые или низкого качества
        if not text or conf < 30:
            continue
        
        line_num = data['line_num'][i]
        block_num = data['block_num'][i]
        
        # Создаем уникальный ключ строки (block + line)
        line_key = (block_num, line_num)
        
        if line_key not in lines:
            lines[line_key] = []
        
        lines[line_key].append({
            'text': text,
            'conf': conf,
            'x': data['left'][i],
            'y': data['top'][i],
            'w': data['width'][i],
            'h': data['height'][i],
        })
    
    # Сортируем элементы в каждой строке по X (слева направо)
    for line_key in lines:
        lines[line_key].sort(key=lambda item: item['x'])
    
    return lines


def parse_table_rows(lines_data: Dict) -> List[Dict[str, any]]:
    """
    Парсит строки таблицы и извлекает пары (название, количество).
    
    Логика:
    - Первый элемент в строке = название
    - Последний элемент, который является числом = количество
    - Если количества нет или не число — пропускаем строку
    
    Returns:
        List[{"name": str, "qty": float, "confidence": float}]
    """
    items = []
    
    for line_key, words in lines_data.items():
        if not words:
            continue
        
        # Название — все слова до последнего числа
        # Количество — последнее число в строке
        name_parts = []
        qty_candidate = None
        qty_conf = 0
        
        for i, word_data in enumerate(words):
            text = word_data['text']
            conf = word_data['conf']
            
            # Пытаемся распознать как число
            try:
                # Очищаем: заменяем запятую на точку, убираем пробелы
                num_text = text.replace(',', '.').replace(' ', '').replace('O', '0').replace('o', '0')
                qty_val = float(num_text)
                
                # Это похоже на количество — запоминаем
                qty_candidate = qty_val
                qty_conf = conf
                
                # Всё до этого момента — название
                name_parts = [w['text'] for w in words[:i]]
                
            except ValueError:
                # Это не число — часть названия
                if qty_candidate is None:
                    name_parts.append(text)
        
        # Формируем название
        name = ' '.join(name_parts).strip()
        
        # Проверяем валидность
        if not name or qty_candidate is None or qty_candidate <= 0:
            continue
        
        # Средний confidence для строки
        avg_conf = sum(w['conf'] for w in words) / len(words)
        
        items.append({
            'name': name,
            'qty': qty_candidate,
            'confidence': avg_conf,
        })
    
    return items


def assess_quality(items: List[Dict]) -> Dict[str, any]:
    """
    Оценивает качество распознавания Tesseract.
    
    Returns:
        {
            'is_good': bool,  # Достаточно ли хорошее качество
            'avg_confidence': float,  # Средний confidence
            'items_count': int,  # Количество распознанных позиций
            'low_conf_count': int,  # Количество низкого качества
        }
    """
    if not items:
        return {
            'is_good': False,
            'avg_confidence': 0.0,
            'items_count': 0,
            'low_conf_count': 0,
        }
    
    confidences = [it['confidence'] for it in items]
    avg_conf = sum(confidences) / len(confidences)
    low_conf_count = sum(1 for c in confidences if c < 60)
    
    # Хорошее качество если:
    # - Средний confidence > 70
    # - Распознано хотя бы 3 позиции
    # - Меньше 30% позиций с низким confidence
    is_good = (
        avg_conf > 70 and
        len(items) >= 3 and
        low_conf_count < len(items) * 0.3
    )
    
    return {
        'is_good': is_good,
        'avg_confidence': avg_conf,
        'items_count': len(items),
        'low_conf_count': low_conf_count,
    }


def extract_table_tesseract(image_path: str) -> Tuple[List[Dict], Dict]:
    """
    Извлекает таблицу с помощью Tesseract OCR.
    
    Returns:
        (items, quality_metrics)
        
        items: List[{"name": str, "qty": float}]
        quality_metrics: Dict с оценкой качества
    """
    import sys
    
    try:
        # Предобработка
        print(f"[TESSERACT] Предобработка изображения: {Path(image_path).name}", file=sys.stderr)
        preprocessed = preprocess_image(image_path)
        
        # Извлечение структуры
        print(f"[TESSERACT] Извлечение текста...", file=sys.stderr)
        lines_data = extract_table_structure(preprocessed)
        
        # Парсинг строк
        print(f"[TESSERACT] Парсинг {len(lines_data)} строк...", file=sys.stderr)
        items_with_conf = parse_table_rows(lines_data)
        
        # Оценка качества
        quality = assess_quality(items_with_conf)
        
        print(f"[TESSERACT] Распознано {quality['items_count']} позиций, "
              f"avg conf={quality['avg_confidence']:.1f}%, "
              f"качество={'✓' if quality['is_good'] else '✗'}", file=sys.stderr)
        
        # Убираем confidence из итогового результата
        items = [{'name': it['name'], 'qty': it['qty']} for it in items_with_conf]
        
        return items, quality
        
    except Exception as e:
        import sys
        print(f"[TESSERACT] Ошибка: {e}", file=sys.stderr)
        return [], {'is_good': False, 'avg_confidence': 0, 'items_count': 0, 'low_conf_count': 0}


def detect_doc_type_simple(image_path: str) -> str:
    """
    Простая детекция типа документа по заголовку (Tesseract).
    
    Returns:
        'production' | 'writeoff' | 'income' | 'unknown'
    """
    try:
        img = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Берём только верхние 15% изображения (заголовок)
        h = gray.shape[0]
        header = gray[:int(h * 0.15), :]
        
        # OCR заголовка
        text = pytesseract.image_to_string(header, lang='rus', config='--psm 6').lower()
        
        if 'производство' in text or 'производ' in text:
            return 'production'
        elif 'списание' in text or 'списан' in text:
            return 'writeoff'
        elif 'приход' in text or 'прих' in text:
            return 'income'
        else:
            return 'unknown'
    except Exception:
        return 'unknown'
