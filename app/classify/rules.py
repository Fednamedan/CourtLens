"""Классификация типа документа по заголовку и служебным маркерам."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.models import DocType

_ORDER = "судебный приказ"
_WRIT = "исполнительный лист"
_FSSP = "постановление"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е"))


def is_electronic(text: str) -> bool:
    """Электронные ИД содержат служебные строки «Формат: …fssp…» и «Идентификатор …#N»."""
    t = _norm(text)
    return "идентификатор" in t and ("формат:" in t or "namespace" in t)


def classify(text: str) -> tuple[DocType, float]:
    """-> (тип, уверенность). Смотрим первые ~900 символов; нечётко, т.к. возможен OCR-шум."""
    head = _norm(text)[:900]
    scores = {
        "order": fuzz.partial_ratio(_ORDER, head),
        "writ": fuzz.partial_ratio(_WRIT, head),
        "fssp": fuzz.partial_ratio(_FSSP, head),
    }
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    conf = scores[best] / 100
    if scores[best] < 85 or (ranked[0] - ranked[1]) < 8:
        return DocType.UNKNOWN, round(conf * 0.5, 2)
    elec = is_electronic(text)
    if best == "order":
        return (DocType.ORDER_E if elec else DocType.ORDER), conf
    if best == "writ":
        return (DocType.WRIT_E if elec else DocType.WRIT), conf
    return DocType.FSSP, conf
