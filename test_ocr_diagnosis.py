#!/usr/bin/env python3
"""
Тестовый скрипт для диагностики проблем OCR и сопоставления с каталогом
"""
import json
from pathlib import Path
from ocr_gpt import extract_doc_from_image_gpt
from catalog_lookup import resolve_purchase_name, DF_CAT
from name_matching import find_best_match, calc_similarity

def test_ocr_on_images():
    """Проверяем OCR на сохраненных изображениях"""
    print("="*60)
    print("ТЕСТ 1: Проверка OCR на сохраненных изображениях")
    print("="*60)
    
    tmp_dir = Path("tmp_images")
    if not tmp_dir.exists():
        print("Нет папки tmp_images")
        return
    
    images = list(tmp_dir.glob("*.jpg"))
    if not images:
        print("Нет сохраненных изображений для тестирования")
        return
    
    for img in images:
        print(f"\n--- Обработка: {img.name} ---")
        try:
            result = extract_doc_from_image_gpt(str(img))
            print(f"Тип документа: {result['doc_type']}")
            print(f"Найдено позиций: {len(result['items'])}")
            print("\nРаспознанные позиции:")
            for i, item in enumerate(result['items'], 1):
                print(f"  {i}. {item['name']:30} — {item['qty']}")
        except Exception as e:
            print(f"ОШИБКА: {e}")
    

def test_catalog_matching():
    """Проверяем сопоставление с каталогом"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Проверка сопоставления с каталогом")
    print("="*60)
    
    # Берем реальные названия из каталога
    catalog_names = DF_CAT["Наименование"].astype(str).tolist()
    catalog_names = [n for n in catalog_names if n.strip()]
    
    print(f"\nВсего товаров в каталоге: {len(catalog_names)}")
    print(f"Первые 10 товаров: {catalog_names[:10]}")
    
    # Тестовые запросы с типичными ошибками
    test_queries = [
        "тесто",
        "ТЕСТО",
        "Тесто ",
        "крутоны",
        "Крутоны",
        "помидор",
        "помидоры",
        "бекон",
        "БЕКОН",
        "капуста",
        "картофель",
        "сливки",
        "песто",  # похоже на "тесто" - проверим ложные срабатывания
    ]
    
    print("\n--- Тестирование запросов ---")
    for query in test_queries:
        best, score = find_best_match(query, catalog_names)
        print(f"Запрос: '{query:20}' -> '{best:30}' (score: {score:.3f})")
        
        # Если score < 0.7, покажем топ-5 похожих
        if score < 0.7:
            scores = [(name, calc_similarity(query, name)) for name in catalog_names[:50]]
            scores.sort(key=lambda x: x[1], reverse=True)
            print(f"  ⚠ Низкий score! Топ-5 похожих:")
            for name, s in scores[:5]:
                print(f"    - {name:30} : {s:.3f}")


def test_matching_with_typos():
    """Проверка устойчивости к опечаткам"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Проверка устойчивости к опечаткам")
    print("="*60)
    
    catalog_names = DF_CAT["Наименование"].astype(str).tolist()
    catalog_names = [n for n in catalog_names if n.strip()]
    
    # Типичные опечатки
    typo_tests = [
        ("тесто", "тесо"),  # пропущена буква
        ("крутоны", "крутны"),  # пропущена буква
        ("помидор", "помдор"),  # пропущена буква
        ("бекон", "бекн"),  # пропущена буква
        ("капуста", "капста"),  # пропущена буква
    ]
    
    print("\n--- Тестирование опечаток ---")
    for correct, typo in typo_tests:
        best_correct, score_correct = find_best_match(correct, catalog_names)
        best_typo, score_typo = find_best_match(typo, catalog_names)
        
        print(f"\nКорректное: '{correct}' -> '{best_correct}' (score: {score_correct:.3f})")
        print(f"С опечаткой: '{typo}' -> '{best_typo}' (score: {score_typo:.3f})")
        
        if best_correct != best_typo:
            print(f"  ⚠ ВНИМАНИЕ: опечатка привела к другому товару!")


def test_mixed_case_and_spaces():
    """Проверка обработки регистра и пробелов"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Проверка регистра и пробелов")
    print("="*60)
    
    catalog_names = DF_CAT["Наименование"].astype(str).tolist()
    catalog_names = [n for n in catalog_names if n.strip()]
    
    # Вариации одного названия
    variations = [
        "бекон",
        "БЕКОН",
        "Бекон",
        " бекон ",
        "  БЕКОН  ",
        "беКон",
    ]
    
    print("\n--- Тестирование вариаций ---")
    for variant in variations:
        best, score = find_best_match(variant, catalog_names)
        print(f"'{variant:20}' -> '{best:30}' (score: {score:.3f})")


if __name__ == "__main__":
    test_ocr_on_images()
    test_catalog_matching()
    test_matching_with_typos()
    test_mixed_case_and_spaces()
    
    print("\n" + "="*60)
    print("ДИАГНОСТИКА ЗАВЕРШЕНА")
    print("="*60)
