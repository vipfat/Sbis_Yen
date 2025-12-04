"""Функции подбора наиболее подходящего названия по строковому сходству."""

import re
from difflib import SequenceMatcher
from typing import Iterable, Optional, Tuple


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _token_overlap_score(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


def calc_similarity(query: str, candidate: str) -> float:
    """Возвращает оценку похожести (0..1), учитывая подстроку и пересечение токенов."""

    q = _normalize(query)
    c = _normalize(candidate)
    if not q or not c:
        return 0.0

    if q == c:
        return 1.0

    base = 0.0
    if q in c:
        base = 0.92
    elif c in q:
        base = 0.88

    ratio = SequenceMatcher(None, q, c).ratio()
    token_score = _token_overlap_score(q, c)

    # Итог: берём максимум из базового подстрочного и усреднённого ratio/token
    blended = ratio * 0.7 + token_score * 0.3
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

