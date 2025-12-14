#!/usr/bin/env python3
"""
Тест валидации товаров перед отправкой в СБИС.
Проверяет, что validate_and_normalize_items корректно работает.
"""

from bot_simple import validate_and_normalize_items, format_items

def test_validation():
    """Тестируем валидацию и нормализацию товаров."""
    
    # Тестовые данные - сырые позиции из OCR
    raw_items = [
        {"name": "тесто", "qty": 2.5},
        {"name": "песто", "qty": 1.0},
        {"name": "крутоны", "qty": 0.5},
    ]
    
    print("=" * 60)
    print("ТЕСТ ВАЛИДАЦИИ ТОВАРОВ")
    print("=" * 60)
    
    print("\n1. Исходные позиции из OCR:")
    for i, item in enumerate(raw_items, 1):
        print(f"   {i}. {item['name']} — {item['qty']}")
    
    # Валидация для производства
    print("\n2. Валидация для типа 'production':")
    try:
        validated, warnings = validate_and_normalize_items(raw_items, "production")
        
        print(f"\n   Валидировано позиций: {len(validated)}")
        print("\n   Детали:")
        for item in validated:
            ocr_name = item['name']
            catalog_name = item.get('catalog_name', 'НЕ НАЙДЕНО')
            qty = item['qty']
            
            if catalog_name and catalog_name != ocr_name:
                print(f"   • {ocr_name} → {catalog_name} ({qty})")
            else:
                print(f"   • {ocr_name} ({qty})")
        
        if warnings:
            print("\n   ⚠️ Предупреждения:")
            for w in warnings:
                print(f"   {w}")
        
        print("\n3. Как это будет показано пользователю:")
        print("   " + format_items(validated).replace("\n", "\n   "))
        
    except Exception as e:
        print(f"\n   ❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("Тест завершён")
    print("=" * 60)


if __name__ == "__main__":
    test_validation()
