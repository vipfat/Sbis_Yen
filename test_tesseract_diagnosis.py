"""
Диагностический скрипт для тестирования Tesseract OCR на рукописных таблицах.
Показывает промежуточные результаты распознавания.

Использование:
    python test_tesseract_diagnosis.py <путь_к_фото>
    
Или укажите фото из tmp_images:
    python test_tesseract_diagnosis.py tmp_images/photo.jpg
"""
import sys
import cv2
import numpy as np
import pytesseract
from pathlib import Path
from ocr_tesseract import preprocess_image, extract_table_structure, parse_table_rows, assess_quality


def save_preprocessed_image(image: np.ndarray, output_path: str):
    """Сохраняет предобработанное изображение для визуального анализа."""
    cv2.imwrite(output_path, image)
    print(f"💾 Предобработанное изображение сохранено: {output_path}")


def show_raw_ocr_output(image: np.ndarray):
    """Показывает сырой вывод Tesseract с confidence."""
    print("\n" + "="*80)
    print("📝 СЫРОЙ ВЫВОД TESSERACT (с confidence)")
    print("="*80)
    
    custom_config = r'--oem 3 --psm 6 -l rus+eng'
    data = pytesseract.image_to_data(image, config=custom_config, output_type=pytesseract.Output.DICT)
    
    print(f"\n{'Line':<6} {'Block':<6} {'Conf':<6} {'Text':<40} {'Position (x,y,w,h)'}")
    print("-" * 80)
    
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if not text:
            continue
            
        line_num = data['line_num'][i]
        block_num = data['block_num'][i]
        conf = int(data['conf'][i])
        x, y = data['left'][i], data['top'][i]
        w, h = data['width'][i], data['height'][i]
        
        # Цветовая маркировка по confidence
        if conf < 30:
            marker = "🔴"  # Низкий
        elif conf < 60:
            marker = "🟡"  # Средний
        else:
            marker = "🟢"  # Высокий
        
        print(f"{line_num:<6} {block_num:<6} {conf:<6} {marker} {text:<38} ({x},{y},{w},{h})")
    
    print("\n" + "="*80)


def show_structured_data(lines_data: dict):
    """Показывает структурированные данные по строкам."""
    print("\n" + "="*80)
    print("📋 СТРУКТУРИРОВАННЫЕ ДАННЫЕ ПО СТРОКАМ")
    print("="*80)
    
    for idx, (line_key, words) in enumerate(lines_data.items(), 1):
        block, line = line_key
        print(f"\nСтрока #{idx} (block={block}, line={line}):")
        print(f"  Количество слов: {len(words)}")
        
        for word_data in words:
            text = word_data['text']
            conf = word_data['conf']
            x = word_data['x']
            
            # Пробуем определить, число это или текст
            is_number = False
            try:
                float(text.replace(',', '.').replace(' ', ''))
                is_number = True
            except ValueError:
                pass
            
            type_marker = "🔢" if is_number else "📝"
            print(f"    {type_marker} '{text}' (conf={conf}, x={x})")
    
    print("\n" + "="*80)


def show_parsed_items(items: list):
    """Показывает распознанные позиции (название-количество)."""
    print("\n" + "="*80)
    print("✅ РАСПОЗНАННЫЕ ПОЗИЦИИ (название → количество)")
    print("="*80)
    
    if not items:
        print("❌ Ни одной позиции не распознано!")
        return
    
    for idx, item in enumerate(items, 1):
        name = item['name']
        qty = item['qty']
        conf = item.get('confidence', 0)
        
        # Маркер качества
        if conf < 60:
            marker = "⚠️"
        else:
            marker = "✓"
        
        print(f"{idx}. {marker} {name} → {qty} (conf={conf:.1f}%)")
    
    print(f"\nВсего распознано: {len(items)} позиций")
    print("="*80)


def show_quality_assessment(quality: dict):
    """Показывает оценку качества распознавания."""
    print("\n" + "="*80)
    print("🎯 ОЦЕНКА КАЧЕСТВА РАСПОЗНАВАНИЯ")
    print("="*80)
    
    is_good = quality['is_good']
    avg_conf = quality['avg_confidence']
    items_count = quality['items_count']
    low_conf_count = quality['low_conf_count']
    
    status = "✅ ХОРОШО" if is_good else "❌ ПЛОХО (нужен GPT fallback)"
    
    print(f"Статус: {status}")
    print(f"Средний confidence: {avg_conf:.1f}%")
    print(f"Распознано позиций: {items_count}")
    print(f"Позиций с низким качеством (<60%): {low_conf_count}")
    
    if is_good:
        print("\n💡 Tesseract справился! Можно использовать этот результат.")
    else:
        print("\n💡 Tesseract не справился. Причины:")
        if avg_conf < 70:
            print("  - Средний confidence слишком низкий (нужен >70%)")
        if items_count < 3:
            print("  - Распознано слишком мало позиций (нужно ≥3)")
        if low_conf_count > items_count * 0.3:
            print(f"  - Слишком много позиций низкого качества ({low_conf_count}/{items_count})")
        print("\n  Рекомендация: использовать GPT-4o для этого фото.")
    
    print("="*80)


def diagnose_image(image_path: str):
    """Полная диагностика распознавания изображения."""
    path = Path(image_path)
    if not path.exists():
        print(f"❌ Файл не найден: {image_path}")
        return
    
    print("\n" + "="*80)
    print(f"🔍 ДИАГНОСТИКА TESSERACT OCR")
    print(f"Изображение: {path.name}")
    print("="*80)
    
    # Шаг 1: Предобработка
    print("\n1️⃣ Предобработка изображения...")
    preprocessed = preprocess_image(image_path)
    
    # Сохраняем предобработанное изображение
    output_dir = Path("tmp_images")
    output_dir.mkdir(exist_ok=True)
    preprocessed_path = output_dir / f"preprocessed_{path.name}"
    save_preprocessed_image(preprocessed, str(preprocessed_path))
    
    # Шаг 2: Сырой вывод OCR
    print("\n2️⃣ Запуск Tesseract OCR...")
    show_raw_ocr_output(preprocessed)
    
    # Шаг 3: Структурирование данных
    print("\n3️⃣ Структурирование по строкам...")
    lines_data = extract_table_structure(preprocessed)
    show_structured_data(lines_data)
    
    # Шаг 4: Парсинг позиций
    print("\n4️⃣ Парсинг позиций (название-количество)...")
    items_with_conf = parse_table_rows(lines_data)
    show_parsed_items(items_with_conf)
    
    # Шаг 5: Оценка качества
    print("\n5️⃣ Оценка качества...")
    quality = assess_quality(items_with_conf)
    show_quality_assessment(quality)
    
    print("\n" + "="*80)
    print("✨ ДИАГНОСТИКА ЗАВЕРШЕНА")
    print("="*80)
    print(f"\nПредобработанное изображение: {preprocessed_path}")
    print("Сравните его с оригиналом, чтобы понять, как Tesseract видит текст.")
    print("\nЕсли Tesseract не справился:")
    print("  - Проверьте качество фото (освещение, резкость)")
    print("  - Убедитесь, что текст не слишком рукописный")
    print("  - Попробуйте переснять фото с лучшим контрастом")
    print("  - Или используйте GPT-4o (force_gpt=True)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python test_tesseract_diagnosis.py <путь_к_фото>")
        print("\nПример:")
        print("  python test_tesseract_diagnosis.py tmp_images/photo.jpg")
        sys.exit(1)
    
    image_path = sys.argv[1]
    diagnose_image(image_path)
