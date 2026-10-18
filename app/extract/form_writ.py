"""Бланк исполнительного листа с блоками должников:

    Фамилия Имя Отчество, 17.02.1931 г.р.
    Урож.: г. Теста
    Прож.: г. Тест, ул. Примерная, 24-119

В таком акте нет карточки «Должником по исполнительному документу является», поэтому каждый должник
разбирается из своего блока. Какого именно должника эталон ставит в строку, из текста не следует
(в ocr_012 - второй, в ocr_015 - шестой), поэтому в основную таблицу идёт первый, а все остальные
выгружаются отдельной таблицей «по должникам».
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.normalize.values import parse_address, parse_date, to_nominative

_F = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
BLOCK = re.compile(
    rf"({_F}\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)\s*,\s*"
    r"(?:(\d{2}\.\d{2}\.\d{4})\s*г\.?\s*р|дата\s+рождения\s+неразб\w*)")
PROZH = re.compile(r"(?<![Уу])рож\s*\.?\s*:\s*(.+?)(?=\s*Место\s+работы|$)", re.I)


@dataclass
class FormDebtor:
    fio: str
    birth: str | None        # ISO или None (нет/неразборчива)
    street: str | None
    house: str | None
    flat: str | None
    quote: str
    repaired: bool = False   # имя восстановлено по тексту решения (OCR исказил блок)


def _repair(name: str, clause_names: list[str]) -> tuple[str, bool]:
    best, score = name, 0
    for c in (to_nominative(x) for x in clause_names):
        s = fuzz.ratio(name.lower(), c.lower())
        if s > score:
            best, score = c, s
    return (best, True) if 85 <= score < 100 else (name, False)


_NAME3 = re.compile(rf"{_F}\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+")


def form_debtors(flat: str, clause_names: list[str] | None = None) -> list[FormDebtor]:
    # пул чистых написаний: все ФИО из текста решения (в любом падеже) - ими чиним искажённые OCR блоки
    # (сами блоки в пул не входят: иначе искажённое имя «подтвердит» само себя)
    outside = BLOCK.sub(" ", flat)
    pool = list(dict.fromkeys(m.group(0) for m in _NAME3.finditer(outside))) + list(clause_names or [])
    clause_names = pool
    blocks = list(BLOCK.finditer(flat))
    out: list[FormDebtor] = []
    for i, m in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(flat)
        window = flat[m.end(): min(end, m.end() + 400)]
        window = re.split(r"Отметк", window)[0]
        pm = PROZH.search(window)
        street = house = flat_no = None
        if pm:
            street, house, flat_no, _ = parse_address(pm.group(1))
        fio, rep = _repair(m.group(1), clause_names or [])
        out.append(FormDebtor(fio=fio, birth=parse_date(m.group(2)) if m.group(2) else None,
                              street=street, house=house, flat=flat_no,
                              quote=(m.group(0) + " " + window)[:160], repaired=rep))
    return out
