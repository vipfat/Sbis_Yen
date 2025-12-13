"""Функции подбора наиболее подходящего названия по строковому сходству."""

import re
from difflib import SequenceMatcher
from typing import Iterable, Optional, Tuple


def _normalize(text: str) -> str:
    """Нормализация текста: убираем лишние пробелы, приводим к нижнему регистру."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _remove_common_typos(text: str) -> str:
    """Убираем типичные опечатки при распознавании."""
    # Замена похожих букв кириллицы/латиницы
    replacements = {
        'o': 'о', 'O': 'О', 'a': 'а', 'A': 'А',
        'e': 'е', 'E': 'Е', 'p': 'р', 'P': 'Р',
        'c': 'с', 'C': 'С', 'x': 'х', 'X': 'Х',
        'y': 'у', 'Y': 'У', 'k': 'к', 'K': 'К',
    }
    result = text
    for lat, cyr in replacements.items():
        result = result.replace(lat, cyr)
    return result


def _token_overlap_score(a: str, b: str) -> float:
    """Оценка пересечения слов в двух строках."""
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Вычисляет расстояние Левенштейна между двумя строками."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Стоимость вставки, удаления, замены
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def calc_similarity(query: str, candidate: str) -> float:
    """Возвращает оценку похожести (0..1), учитывая подстроку, пересечение токенов и опечатки."""

    # Убираем опечатки перед сравнением
    query_clean = _remove_common_typos(query)
    candidate_clean = _remove_common_typos(candidate)
    
    q = _normalize(query_clean)
    c = _normalize(candidate_clean)
    
    if not q or not c:
        return 0.0

    # Точное совпадение
    if q == c:
        return 1.0

    # Проверка на подстроку
    base = 0.0
    if q in c:
        # Чем больше совпадение, тем выше оценка
        base = 0.92 + (len(q) / len(c)) * 0.05
    elif c in q:
        base = 0.88 + (len(c) / len(q)) * 0.05

    # SequenceMatcher для общей похожести
    ratio = SequenceMatcher(None, q, c).ratio()
    
    # Пересечение токенов (слов)
    token_score = _token_overlap_score(q, c)
    
    # Расстояние Левенштейна (нормализованное)
    max_len = max(len(q), len(c))
    if max_len > 0:
        lev_dist = _levenshtein_distance(q, c)
        lev_score = 1.0 - (lev_dist / max_len)
    else:
        lev_score = 0.0
    
    # Взвешенная комбинация всех метрик
    blended = (
        ratio * 0.4 +          # Общая похожесть
        token_score * 0.25 +   # Пересечение слов
        lev_score * 0.35       # Устойчивость к опечаткам
    )
    
    # Возвращаем максимум из базовой оценки и комбинированной
    return max(base, blended)


def find_best_match(query: str, candidates: Iterable[str]) -> Tuple[Optional[str], float]:
    """Ищет максимально похожее название среди candidates."""

    best_name = None
    best_score = 0.0
    for cand in candidates:
        score = calc_similarity(query, str(cand))
        if score > best_score:
            best_name = str(cand)
            best_score = score

    return best_name, best_score

