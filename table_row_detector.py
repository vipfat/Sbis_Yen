"""
Детекция и нарезка строк таблицы для точного распознавания.

Использует OpenCV для определения границ строк, затем нарезает таблицу
на отдельные изображения строк для независимой обработки через GPT.
"""
import cv2
import numpy as np
from pathlib import Path
from typing import List, Tuple
import sys


def detect_table_rows(image_path: str, min_row_height: int = 20) -> List[Tuple[int, int]]:
    """
    Детектирует строки таблицы по горизонтальной проекции плотности пикселей.
    
    Args:
        image_path: Путь к изображению таблицы
        min_row_height: Минимальная высота строки в пикселях
        
    Returns:
        List[(y_start, y_end)]: Список координат строк
    """
    # Читаем изображение в grayscale
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Не удалось прочитать изображение: {image_path}")
    
    h, w = img.shape
    print(f"[ROW_DETECT] Размер изображения: {w}x{h}", file=sys.stderr)
    
    # Увеличиваем контраст для лучшей детекции
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(img)
    
    # Бинаризация
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Горизонтальная проекция (сумма черных пикселей по каждой строке)
    horizontal_projection = np.sum(binary == 255, axis=1)
    
    # Сглаживание проекции для устранения шума
    kernel_size = max(5, h // 100)
    if kernel_size % 2 == 0:
        kernel_size += 1
    smoothed = cv2.GaussianBlur(horizontal_projection.astype(float), (1, kernel_size), 0).flatten()
    
    # Нормализация
    if smoothed.max() > 0:
        smoothed = smoothed / smoothed.max()
    
    # Находим локальные минимумы (разделители строк)
    threshold = np.percentile(smoothed, 25)  # 25-й перцентиль как порог
    
    # Детекция разделителей
    is_separator = smoothed < threshold
    
    # Объединяем соседние разделители
    rows = []
    in_row = False
    row_start = 0
    
    for y in range(h):
        if not is_separator[y] and not in_row:
            # Начало строки
            row_start = y
            in_row = True
        elif is_separator[y] and in_row:
            # Конец строки
            row_end = y
            if row_end - row_start >= min_row_height:
                rows.append((row_start, row_end))
            in_row = False
    
    # Последняя строка если не закрылась
    if in_row and h - row_start >= min_row_height:
        rows.append((row_start, h))
    
    print(f"[ROW_DETECT] Обнаружено строк: {len(rows)}", file=sys.stderr)
    for idx, (y1, y2) in enumerate(rows[:5]):  # Показываем первые 5
        print(f"  Строка #{idx+1}: y={y1}-{y2} (высота={y2-y1}px)", file=sys.stderr)
    if len(rows) > 5:
        print(f"  ... и еще {len(rows)-5} строк", file=sys.stderr)
    
    return rows


def split_table_into_rows(image_path: str, rows: List[Tuple[int, int]], 
                          output_dir: str = "tmp_images/rows") -> List[str]:
    """
    Нарезает изображение таблицы на отдельные строки.
    
    Args:
        image_path: Путь к исходному изображению
        rows: Список координат строк [(y_start, y_end), ...]
        output_dir: Директория для сохранения нарезанных строк
        
    Returns:
        List[str]: Пути к изображениям строк
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Не удалось прочитать изображение: {image_path}")
    
    # Создаем выходную директорию
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Базовое имя файла
    base_name = Path(image_path).stem
    
    row_images = []
    for idx, (y_start, y_end) in enumerate(rows):
        # Вырезаем строку с небольшим отступом
        padding = 2
        y1 = max(0, y_start - padding)
        y2 = min(img.shape[0], y_end + padding)
        
        row_img = img[y1:y2, :]
        
        # Сохраняем
        output_file = out_path / f"{base_name}_row_{idx:03d}.jpg"
        cv2.imwrite(str(output_file), row_img)
        row_images.append(str(output_file))
    
    print(f"[ROW_SPLIT] Сохранено {len(row_images)} строк в {output_dir}", file=sys.stderr)
    
    return row_images


def visualize_row_detection(image_path: str, rows: List[Tuple[int, int]], 
                           output_path: str = None) -> str:
    """
    Создает визуализацию детекции строк (для отладки).
    
    Args:
        image_path: Путь к исходному изображению
        rows: Список координат строк
        output_path: Путь для сохранения визуализации
        
    Returns:
        str: Путь к сохраненному изображению
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Не удалось прочитать изображение: {image_path}")
    
    # Рисуем линии между строками
    for idx, (y_start, y_end) in enumerate(rows):
        # Горизонтальная линия в начале строки
        cv2.line(img, (0, y_start), (img.shape[1], y_start), (0, 255, 0), 2)
        # Номер строки
        cv2.putText(img, f"#{idx+1}", (10, y_start + 20), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    
    # Линия в конце последней строки
    if rows:
        cv2.line(img, (0, rows[-1][1]), (img.shape[1], rows[-1][1]), (0, 255, 0), 2)
    
    # Сохраняем
    if output_path is None:
        output_path = str(Path(image_path).parent / f"detected_{Path(image_path).name}")
    
    cv2.imwrite(output_path, img)
    print(f"[VISUALIZE] Визуализация сохранена: {output_path}", file=sys.stderr)
    
    return output_path


if __name__ == "__main__":
    # Тестирование
    if len(sys.argv) < 2:
        print("Использование: python table_row_detector.py <путь_к_изображению>")
        sys.exit(1)
    
    image_path = sys.argv[1]
    
    print("=" * 80)
    print("ДЕТЕКЦИЯ СТРОК ТАБЛИЦЫ")
    print("=" * 80)
    
    # Детекция
    rows = detect_table_rows(image_path)
    
    # Визуализация
    vis_path = visualize_row_detection(image_path, rows)
    print(f"\n✅ Визуализация: {vis_path}")
    
    # Нарезка
    row_images = split_table_into_rows(image_path, rows)
    print(f"\n✅ Нарезано строк: {len(row_images)}")
    print("\nПримеры:")
    for path in row_images[:3]:
        print(f"  - {path}")
