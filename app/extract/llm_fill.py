"""LLM добирает только те поля, которые не нашли правила. Каждое значение проверяется:
цитата обязана присутствовать в тексте документа, иначе значение отбрасывается (защита от галлюцинаций)."""
from __future__ import annotations

import logging
import re

from app.config import ROOT
from app.models import DATE_FIELDS, MONEY_FIELDS, Document, ExtractionResult, FieldStatus, FieldValue
from app.normalize.values import parse_date, parse_money
from app.validate.checks import PATTERNS

log = logging.getLogger(__name__)
FILLABLE = ["дело_номер", "дело_дата", "период_дз_начало", "период_дз_оконч", "дз_осн",
            "дз_пени", "дз_пошлина", "дата рождения", "паспорт", "инн", "улица", "дом", "кв"]


def _squash(s: str) -> str:
    return re.sub(r"[\s ]+", "", s).lower().replace("ё", "е")


def quote_in_text(quote: str, text: str) -> bool:
    return bool(quote) and _squash(quote) in _squash(text)


def _coerce(name: str, value):
    if value is None or value == "":
        return None
    if name in DATE_FIELDS:
        return parse_date(str(value)) or (str(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)) else None)
    if name in MONEY_FIELDS:
        return parse_money(str(value)) if not isinstance(value, (int, float)) else round(float(value), 2)
    if name in PATTERNS and not re.fullmatch(PATTERNS[name], str(value)):
        return None
    return str(value)


def fill_missing(doc: Document, res: ExtractionResult, llm) -> None:
    need = [f for f in FILLABLE if f not in res.fields or res.fields[f].status != FieldStatus.OK]
    # поля, которых в документе заведомо нет (напр. паспорт у части приказов), всё равно пробуем один раз
    if not need:
        return
    template = (ROOT / "prompts" / "extract_court.md").read_text(encoding="utf-8")
    text = doc.full_text
    prompt = template.format(fields=", ".join(need), text=text)
    try:
        answer = llm.complete_json("Ты аккуратный извлекатель данных. Отвечай только JSON.", prompt)
    except Exception as exc:  # LLM - вспомогательный канал, её сбой не должен ронять обработку
        res.errors.append(f"LLM недоступна: {exc}")
        return
    for name in need:
        item = answer.get(name) or {}
        value = _coerce(name, item.get("value"))
        quote = str(item.get("quote") or "")
        if value is None:
            continue
        if not quote_in_text(quote, text):
            log.info("LLM-значение %s отброшено: цитаты нет в тексте", name)
            continue
        res.fields[name] = FieldValue(value=value, status=FieldStatus.OK, confidence=0.65,
                                      quote=quote[:160], note="извлечено LLM, цитата подтверждена в тексте")
