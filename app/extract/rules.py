"""Извлечение полей судебного приказа / исполнительного листа правилами (без LLM).

Принципы:
- акт может содержать несколько частей «Взыскать …» с разными должниками и суммами: суммы берутся
  из той части, где назван основной должник;
- данные должника (паспорт, ИНН, дата рождения, адрес) берутся из его карточки либо из его фрагмента
  в части «Взыскать», а не «первое найденное в документе»;
- устойчивость к OCR: слова-связки допускают искажения, цифровые токены проходят fix_digits,
  утраченное значение помечается как не извлечённое (а не угадывается).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Document, FieldStatus, FieldValue, TextSource
from app.normalize.values import (
    fix_digits, parse_address, parse_date, parse_money, stem_key, to_nominative,
)

D = r"(\d{2}\.\d{2}\.\d{4})"
YR = r"(?:\s*(?:года|г\.?))?"
FAM = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
NAME = rf"{FAM}(?:\s*\([А-ЯЁ][а-яё]+\))?\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+"
# валюта: «руб.», «рублей», «Рублей» и искажения OCR («ров.»); «!» вместо «1» в разряде
RUB = r"(?i:руб\w*|р[оуа][вб]\w*|р\.)"
MONEY = rf"([\d!][\d!\s]*(?:[.,]\d{{1,2}})?)\s*{RUB}\.?(?:\s*(\d{{1,2}})\s*коп\w*\.?)?"
PERIOD = rf"период\s*(?:с\s*)?{D}{YR}\s*по\s*{D}{YR}"
NUM = r"(\d-\d[\d\- ]{0,12}?/\d{4})"
DIGITISH = r"[\d\]\[|lIO]"
BIRTH = rf"(?:дата\s+рождения:?\s*{D}|{D}\s*(?:года\s+рождения|г\.\s*р\.|года\s+р\.))"


@dataclass
class Ctx:
    flat: str
    doc: Document
    conf_base: float

    def page_of(self, snippet: str) -> int | None:
        key = re.sub(r"\s+", " ", snippet)[:40]
        for p in self.doc.pages:
            if key in re.sub(r"\s+", " ", p.text):
                return p.number
        return None


def _fv(ctx: Ctx, value, quote: str, conf: float | None = None, note: str | None = None) -> FieldValue:
    return FieldValue(value=value, status=FieldStatus.OK,
                      confidence=round(conf if conf is not None else ctx.conf_base, 2),
                      quote=quote.strip()[:160], page=ctx.page_of(quote), note=note)


def _missing(note: str = "не найдено") -> FieldValue:
    return FieldValue(status=FieldStatus.MISSING, note=note)


def _name_key(n: str) -> str:
    """Имя+отчество (фамилию OCR искажает чаще, а падежные окончания различаются)."""
    toks = re.sub(r"\([^)]*\)", " ", n).split()
    return " ".join(t.lower().replace("ё", "е")[:4] for t in toks[1:])


# ------------------------------------------------------------------ дело
def _case(ctx: Ctx) -> tuple[FieldValue, FieldValue]:
    t = ctx.flat
    m = re.search(rf"по\s+делу[^№]{{0,60}}№\s*{NUM}\s*\S{{1,3}}\s*{D}", t)
    if m:
        return (_fv(ctx, re.sub(r"\s", "", m.group(1)), m.group(0)),
                _fv(ctx, parse_date(m.group(2)), m.group(0)))
    part = re.search(rf"по\s+делу[^№]{{0,60}}№\s*{NUM}\s*\S{{1,3}}\s*(\S{{0,3}}\.\d{{2}}\.\d{{4}})", t)
    if part:
        return (_fv(ctx, re.sub(r"\s", "", part.group(1)), part.group(0)),
                _missing(f"дата распознана частично ({part.group(2)!r}), нужна проверка по скану"))
    lost = re.search(rf"по\s+делу[^№]{{0,60}}№\s*{NUM}\s*\S{{1,3}}\s+(?=[А-ЯЁ])", t)
    if lost:
        return (_fv(ctx, re.sub(r"\s", "", lost.group(1)), lost.group(0)),
                _missing("дата дела не распознана OCR, нужна проверка по скану"))
    num = re.search(rf"(?:Производство|Дело)\s*№\s*{NUM}", t) or re.search(rf"\b{NUM}", t)
    number = _fv(ctx, re.sub(r"\s", "", num.group(1)), num.group(0)) if num else _missing()
    # бланк ИЛ: «Дело № 2-86322/2020 09.12.2020» (дата сразу за номером) - раньше даты выдачи листа
    bd = re.search(rf"Дело\s*№[\d\-/ _]{{0,24}}?{D}", t)
    if bd and (parse_date(bd.group(1)) or ""):
        return number, _fv(ctx, parse_date(bd.group(1)), bd.group(0))
    dm = re.search(r"(\d{1,2}\s+[а-яё]+\s+\d{4})\s*г", t[:900], re.I)
    date_ = parse_date(dm.group(1)) if dm else None
    return number, (_fv(ctx, date_, dm.group(0)) if date_ else _missing())


# ------------------------------------------------------------------ части «Взыскать» и должники
def _clauses(flat: str) -> list[str]:
    idx = [m.start() for m in re.finditer(r"Взыскать\s", flat)]
    end = re.search(r"Приложения|Формат:", flat[idx[-1]:]) if idx else None
    out = []
    for i, s in enumerate(idx):
        e = idx[i + 1] if i + 1 < len(idx) else (idx[-1] + end.start() if end else len(flat))
        out.append(flat[s:e])
    return out


def _debtor_chunk(clause: str) -> str:
    m = re.match(r"Взыскать\s+(?:солидарно\s+)?с\s+(?:должник\w*[\s:()ов]*)?(.+?)\s+в\s+пользу", clause)
    return m.group(1) if m else ""


def _surname_like(n: str) -> int:
    w = n.split()[0].lower()
    return int(bool(re.search(r"((ов|ев|ёв|ин|ын)(а|ой|ым|у|е|ы|ого|ому|ом)?|(ск|цк)(ий|ая|ой|ого|ому|им|ом|ую))$", w)))


def find_debtors(flat: str) -> tuple[list[str], list[str]]:
    """-> (все должники в падеже документа, фрагменты «кому взыскать» по частям)."""
    found: list[tuple[int, str, bool]] = []
    chunks: list[str] = []
    for cl_start, clause in ((flat.find(c), c) for c in _clauses(flat)):
        chunk = _debtor_chunk(clause)
        chunks.append(chunk)
        off = cl_start + clause.find(chunk) if chunk else 0
        for nm in re.finditer(rf"({NAME})\s*,?\s*(?:{BIRTH}|\(?[^,]{{0,40}}{D})", chunk):
            found.append((off + nm.start(1), nm.group(1), True))
    for nm in re.finditer(rf"(?:с\s+должник\w*|с\s+ответчик\w*|\sк)\s+((?:{NAME}\s*(?:,|\sи)?\s*)+)", flat):
        for one in re.finditer(NAME, nm.group(1)):
            found.append((nm.start(1) + one.start(), one.group(0), False))

    def freq(n: str) -> int:
        return len(re.findall(re.escape(n.split()[0][:5]), flat, re.I))

    best: dict[str, tuple[int, str, bool]] = {}
    for pos, n, primary in sorted(found):
        k = _name_key(n)
        if k not in best:
            best[k] = (pos, n, primary)
        elif (_surname_like(n), freq(n), primary) > (_surname_like(best[k][1]), freq(best[k][1]), best[k][2]):
            best[k] = (best[k][0], n, primary)
    return [n for _, n, _ in sorted(best.values())], chunks


def _main_debtor(ctx: Ctx, names: list[str]):
    """-> (ФИО, позиция карточки, есть_карточка, имя_из_карточки)."""
    a = re.search(r"[Дд]олжник\w*\s+по\s+исполнительному\s+документу\s+является", ctx.flat)
    if a:
        m = re.match(rf":?\s*(?:физическое\s+лицо\s+)?({NAME})", ctx.flat[a.end(): a.end() + 120])
        if m:
            return m.group(1), a.start(), True, True
        if names:
            return to_nominative(names[0]), a.start(), True, False
    # запасной якорь: заголовок карточки изуродован OCR, но «идентификатор плательщика» читается
    b = re.search(r"идентификатор\s+плательщика", ctx.flat)
    if b and names:
        return to_nominative(names[0]), max(0, b.start() - 120), True, False
    if names:
        return to_nominative(names[0]), max(ctx.flat.find(names[0].split()[0]), 0), False, False
    return None, 0, False, False


def _segment_by_name(chunk: str, key: str) -> str:
    """Фрагмент от имени искомого должника до имени следующего (граница - ФИО, а не дата)."""
    ms = list(re.finditer(NAME, chunk))
    for i, m in enumerate(ms):
        if _name_key(m.group(0)) == key:
            return chunk[m.start(): ms[i + 1].start() if i + 1 < len(ms) else None]
    return ""


def _segment(chunk: str, idx: int) -> str:
    """Фрагмент части «Взыскать», относящийся к idx-му должнику (границы - даты рождения)."""
    marks = [m for m in re.finditer(BIRTH, chunk)]
    if not marks:
        return chunk[:300]
    idx = min(idx, len(marks) - 1)
    start = marks[idx].start()
    return chunk[start: marks[idx + 1].start()] if idx + 1 < len(marks) else chunk[start:]


# ------------------------------------------------------------------ суммы
def _sum_fields(ctx: Ctx, text: str) -> dict[str, FieldValue]:
    out: dict[str, FieldValue] = {}
    main = None
    for m in re.finditer(PERIOD, text):
        if re.search(r"пен", text[max(0, m.start() - 22): m.start()], re.I):
            continue
        mm = re.match(rf"[^\d]{{0,35}}?{MONEY}", text[m.end(): m.end() + 80])
        main = (m, mm)
        break
    if main:
        m, mm = main
        out["период_дз_начало"] = _fv(ctx, parse_date(m.group(1)), m.group(0))
        out["период_дз_оконч"] = _fv(ctx, parse_date(m.group(2)), m.group(0))
        if mm:
            out["дз_осн"] = _fv(ctx, parse_money(mm.group(1), mm.group(2)), m.group(0) + mm.group(0))

    for pm in re.finditer(r"пен[ией]\w*", text):
        rest = text[pm.end(): pm.end() + 160]
        per = re.match(rf"\s*(?:за\s+(?:указанный\s+)?)?{PERIOD}", rest)
        rest2 = rest[per.end():] if per else rest
        mm = re.match(rf"[^\d]{{0,40}}?{MONEY}", rest2)
        if mm:
            out["дз_пени"] = _fv(ctx, parse_money(mm.group(1), mm.group(2)), text[pm.start(): pm.end() + (per.end() if per else 0) + mm.end()])
            break

    fee = re.search(rf"пошлин\w*[^\d]{{0,30}}?{MONEY}", text)
    if fee:
        out["дз_пошлина"] = _fv(ctx, parse_money(fee.group(1), fee.group(2)), fee.group(0))
    return out


# ------------------------------------------------------------------ данные должника
def _person_fields(ctx: Ctx, names: list[str], chunks: list[str]) -> tuple[dict[str, FieldValue], str]:
    out: dict[str, FieldValue] = {}
    fio, pos, from_card, name_in_card = _main_debtor(ctx, names)
    if not fio:
        return out, ""
    parts = fio.split()
    conf = ctx.conf_base if name_in_card else ctx.conf_base * 0.85
    out["фамилия"], out["имя"], out["отчетство"] = (_fv(ctx, p, fio, conf) for p in parts[:3])
    out["фио"] = _fv(ctx, fio, fio, conf, None if name_in_card else "склонение в именительный падеж восстановлено автоматически")

    # часть «Взыскать», где назван основной должник, и его фрагмент
    key = _name_key(fio)
    stem = parts[0][:5].lower()
    chunk, idx, seg = "", 0, ""
    for ch in chunks:
        if not ch:
            continue
        sg = _segment_by_name(ch, key)
        if sg:
            chunk, seg = ch, sg
            break
        if stem in ch.lower() and not chunk:
            chunk = ch
    if not chunk:
        chunk = next((c for c in chunks if c), "")
    if chunk and not seg:  # имя в части искажено OCR - берём фрагмент по порядковому номеру должника
        idx = next((i for i, n in enumerate(names) if _name_key(n) == key), 0)
        seg = _segment(chunk, idx)
    card = ""
    if from_card:
        card = ctx.flat[pos: pos + 1200]
        card = re.split(r"Орган \(должностное лицо\), РЕШИЛ", card)[0]

    # дата рождения
    bm = re.search(BIRTH, seg) if seg else None
    if from_card and (cm := (re.search(rf"(?<![\wА-я])[А-Яа-яA-Za-z]{{1,4}}\.\s*{D}", card) or re.search(D, card))):
        out["дата рождения"] = _fv(ctx, parse_date(cm.group(1)), cm.group(0))
    elif bm:
        out["дата рождения"] = _fv(ctx, parse_date(bm.group(1) or bm.group(2)), bm.group(0))

    for scope in (card, seg):
        if not scope:
            continue
        if "паспорт" not in out:
            pm = re.search(rf"паспорт\w*[^\d]{{0,70}}?(\d{{4}})\s*(?:№\s*)?({DIGITISH}{{6}})", scope)
            if pm:
                num = fix_digits(pm.group(2))
                changed = num != pm.group(2)
                out["паспорт"] = _fv(ctx, f"{fix_digits(pm.group(1))} {num}", pm.group(0),
                                     0.5 if changed else None,
                                     "исправлены искажения OCR в цифрах" if changed else None)
        if "инн" not in out:
            im = re.search(rf"ИНН\s*[:№]?\s*({DIGITISH}{{12}})(?!\d)", scope)
            if im:
                out["инн"] = _fv(ctx, fix_digits(im.group(1)), im.group(0))
        if "снилс" not in out:
            sm = re.search(r"СНИЛС\s*[:№]?\s*(\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2})", scope)
            if sm:
                out["снилс"] = _fv(ctx, re.sub(r"\D", "", sm.group(1)), sm.group(0))

    # адрес: текстовый стиль предпочтительнее структурного (kladr)
    cands: list[str] = []
    am = re.search(r"адрес:\s*(.+?)(?:,\s*адрес\s+фактический|\s+пол\s+|$)", card) if card else None
    if am:
        cands.append(am.group(1))
    # «адрес:» в карточке может быть испорчен OCR - запасной вариант «адрес фактический:» (обычно совпадает)
    af = re.search(r"адрес\s+фактический:\s*(.+?)(?:,?\s+пол\s+|$)", card) if card else None
    if af:
        cands.append(af.group(1))
    marker = r"(?:место\s+жительства|адрес(?:у)?(?:\s+(?:регистрации|проживания))?|регистрац\w+)\s*:?\s*(.+)"
    for src in (seg, chunk):
        mm = re.search(marker, src)
        if mm:
            cands.append(mm.group(1)[:200])
    parsed = [parse_address(c) for c in cands]
    pick = next((p for p in parsed if p[0] and p[3] == "text"), None) or next((p for p in parsed if p[0]), None)
    if pick:
        street, house, flat_no, style = pick
        note = "структурный адрес, тип улицы не указан" if style == "kladr" else None
        out["улица"] = _fv(ctx, street, street, None, note)
        if house:
            out["дом"] = _fv(ctx, house, f"{street}, {house}")
        if flat_no:
            out["кв"] = _fv(ctx, flat_no, f"{street}, {house}, {flat_no}")
    return out, chunk


def _form_person(ctx: Ctx, fd) -> dict[str, FieldValue]:
    """Поля основного должника по бланку с блоками: берётся первый блок."""
    return person_from_form(ctx, fd[0], len(fd))


def person_from_form(ctx: Ctx, d, total: int) -> dict[str, FieldValue]:
    out: dict[str, FieldValue] = {}
    conf = ctx.conf_base * (0.85 if d.repaired else 1.0)
    note = (f"в акте {total} должников, выбран первый; в таблице «по должникам» - все" if total > 1 else None)
    parts = d.fio.split()
    for key, val in zip(("фамилия", "имя", "отчетство"), parts[:3]):
        out[key] = _fv(ctx, val, d.quote, conf, note if key == "фамилия" else None)
    out["фио"] = _fv(ctx, d.fio, d.quote, conf, note)
    if d.birth:
        out["дата рождения"] = _fv(ctx, d.birth, d.quote)
    else:
        out["дата рождения"] = _missing("дата рождения не указана или неразборчива в документе")
    if d.street:
        out["улица"] = _fv(ctx, d.street, d.quote)
    if d.house:
        out["дом"] = _fv(ctx, d.house, d.quote)
    if d.flat:
        out["кв"] = _fv(ctx, d.flat, d.quote)
    return out


# ------------------------------------------------------------------ точка входа
def extract_court_fields(doc: Document) -> dict[str, FieldValue]:
    flat = re.sub(r"\s+", " ", doc.full_text)
    ocr_conf = [p.ocr_confidence for p in doc.pages if p.source == TextSource.OCR and p.ocr_confidence]
    ctx = Ctx(flat=flat, doc=doc, conf_base=min(ocr_conf) if ocr_conf else 0.95)

    fields: dict[str, FieldValue] = {}
    fields["дело_номер"], fields["дело_дата"] = _case(ctx)
    names, chunks = find_debtors(flat)
    from app.extract.form_writ import form_debtors

    has_card = "Должником по исполнительному документу" in flat
    fd = [] if has_card else form_debtors(flat, names)
    if fd:
        names = [d.fio for d in fd]  # блоки точнее, чем искажённый OCR перечень в решении
        person = _form_person(ctx, fd)
    else:
        person, _ = _person_fields(ctx, names, chunks)
    fields.update(person)

    # суммы - из части «Взыскать», где назван основной должник (иначе из всего текста)
    clauses = _clauses(flat)
    stem = (fields["фамилия"].value[:5].lower() if "фамилия" in fields else "")
    clause = next((c for c in clauses if stem and stem in c.lower()), "")
    sums = _sum_fields(ctx, clause) if clause else {}
    whole = _sum_fields(ctx, flat)
    for k, v in whole.items():
        sums.setdefault(k, v)
    fields.update(sums)

    if names:
        fields["соответчики_кол-во"] = _fv(ctx, len(names), "; ".join(names)[:160])
        fields["соответчики_фио"] = _fv(ctx, ", ".join(names), "; ".join(names)[:160])
    return fields


def extract_debtor_rows(doc: Document, base: dict[str, FieldValue]) -> list[dict[str, FieldValue]]:
    """Для бланков ИЛ с несколькими должниками - полная строка на каждого (общие поля дела и суммы те же)."""
    from app.extract.form_writ import form_debtors

    flat = re.sub(r"\s+", " ", doc.full_text)
    if "Должником по исполнительному документу" in flat:
        return []
    fd = form_debtors(flat, find_debtors(flat)[0])
    if len(fd) < 2:
        return []
    ocr_conf = [p.ocr_confidence for p in doc.pages if p.source == TextSource.OCR and p.ocr_confidence]
    ctx = Ctx(flat=flat, doc=doc, conf_base=min(ocr_conf) if ocr_conf else 0.95)
    rows = []
    for d in fd:
        row = {k: v for k, v in base.items() if k not in ("фамилия", "имя", "отчетство", "фио", "дата рождения",
                                                          "улица", "дом", "кв", "паспорт", "инн", "снилс")}
        row.update(person_from_form(ctx, d, len(fd)))
        rows.append(row)
    return rows
