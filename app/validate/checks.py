"""Проверка извлечённых значений: формат, логика, понижение статуса сомнительных полей."""
from __future__ import annotations

import re
from datetime import date

from app.models import (
    DATE_FIELDS, MONEY_FIELDS, ExtractionResult, FieldStatus, FieldValue,
)

PATTERNS = {
    "паспорт": r"\d{4} \d{6}",
    "инн": r"\d{12}",
    "снилс": r"\d{11}",
    "дело_номер": r"\d+(?:-\d+)*/\d{4}",
}


def _invalid(fv: FieldValue, why: str) -> None:
    fv.status = FieldStatus.INVALID
    fv.note = (fv.note + "; " if fv.note else "") + why


def validate(res: ExtractionResult, low_conf: float = 0.6) -> ExtractionResult:
    f = res.fields
    for name, fv in f.items():
        if fv.status != FieldStatus.OK:
            continue
        if name in PATTERNS and not re.fullmatch(PATTERNS[name], str(fv.value)):
            _invalid(fv, f"формат не соответствует {PATTERNS[name]}")
        elif name in DATE_FIELDS:
            try:
                date.fromisoformat(str(fv.value))
            except ValueError:
                _invalid(fv, "некорректная дата")
        elif name in MONEY_FIELDS and (fv.value is None or float(fv.value) < 0):
            _invalid(fv, "некорректная сумма")

    def val(k):
        v = f.get(k)
        return v.value if v and v.status == FieldStatus.OK else None

    s, e = val("период_дз_начало"), val("период_дз_оконч")
    if s and e and s > e:
        for k in ("период_дз_начало", "период_дз_оконч"):
            _invalid(f[k], "начало периода позже окончания")
    b, c = val("дата рождения"), val("дело_дата")
    if b and c and b >= c:
        _invalid(f["дата рождения"], "дата рождения не раньше даты дела")

    for fv in f.values():
        if fv.status == FieldStatus.OK and fv.confidence < low_conf:
            fv.status = FieldStatus.LOW_CONFIDENCE
    return res


def issues(res: ExtractionResult, expected: list[str]) -> list[str]:
    """Человекочитаемые замечания для колонки «Замечания» и письма."""
    out = []
    for name in expected:
        fv = res.fields.get(name)
        if fv is None or fv.status == FieldStatus.MISSING:
            out.append(f"{name}: не извлечено")
        elif fv.status != FieldStatus.OK:
            out.append(f"{name}: {fv.status.value}" + (f" ({fv.note})" if fv.note else ""))
    out.extend(res.errors)
    return out


def validate_xml(res) -> None:
    """Проверки таблицы ФССП: форматы дат, сумм, согласованность периода и суммы долга."""
    import re
    from app.models import XML_DATE_FIELDS, XML_MONEY_FIELDS, FieldStatus

    for name, fv in res.fields.items():
        if fv.status == FieldStatus.MISSING or fv.value in (None, ""):
            continue
        if name in XML_DATE_FIELDS and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(fv.value)):
            fv.status, fv.note = FieldStatus.INVALID, "неверный формат даты"
        if name in XML_MONEY_FIELDS:
            try:
                float(fv.value)
            except ValueError:
                fv.status, fv.note = FieldStatus.INVALID, "сумма не число"
    f = res.fields
    ds, de = f.get("date_start"), f.get("date_end")
    if ds and de and ds.value and de.value and ds.value > de.value:
        for x in (ds, de):
            x.status, x.confidence = FieldStatus.LOW_CONFIDENCE, min(x.confidence, 0.5)
            x.note = "начало периода позже окончания (проверить документ)"
    try:
        parts = sum(float(f[k].value) for k in ("rub_deb", "rub_peni", "rub_poshlina", "rub_post")
                    if k in f and f[k].value)
        total = float(f["IdDebtSum"].value)
        if f["rub_peni"].value and abs(parts - total) > 0.01:
            f["IdDebtSum"].note = f"сумма составляющих {parts:.2f} не равна IdDebtSum (в тексте могут быть иные расходы)"
    except (KeyError, TypeError, ValueError):
        pass
