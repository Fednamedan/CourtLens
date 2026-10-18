"""Разбор XML-постановлений ФССП (формат fssp:OIp) в строку таблицы xml_template.csv.

Принцип: поля, лежащие в тегах, берутся как есть (детерминированно, уверенность 1.0);
street/dom/kv выводятся из DbtrAdr, а период и суммы — из свободного текста IdDebtText.
Любое поле, которое не удалось получить, остаётся пустым и помечается MISSING.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from app.models import DocType, ExtractionResult, FieldStatus, FieldValue

# Прямые соответствия «столбец таблицы -> тег XML»
TAG_FIELDS = {
    "DocType": "DocType", "DebtorType": "DebtorType", "DocDate": "DocDate",
    "IdDebtText": "IdDebtText", "IdDocNo": "IdDocNo", "IdDocDate": "IdDocDate",
    "IdDeloNo": "IdDeloNo", "IdDeloDate": "IdDeloDate", "IdDebtSum": "IdDebtSum",
    "IpNo": "IpNo", "IdType": "IdType", "DbtrAdr": "DbtrAdr", "DbtrName": "DbtrName",
}

# Человекочитаемое имя вида постановления (столбец DocType2). Коды из образцов;
# для неизвестного кода имя собирается из тега DocName по правилам сокращения.
DOCTYPE2 = {
    "O_IP_ACT_END_END": "Постановление об окончании ИП",
    "O_IP_ACT_END_STOP": "Постановление о прекращении ИП",
    "O_IP_ACT_REOPEN_CANCEL": "Постановление об отказе в возбуждении ИП",
    "O_IP_ACT_RETURN": "Постановление об окончании и возвращении ИД",
    "O_IP_RES_REOPEN": "Постановление о возбуждении",
}
_ABBR = [(r"исполнительного\s+производства", "ИП"), (r"исполнительного\s+документа", "ИД")]

STREET_TYPES = r"(?:ул|пер|пр-кт|пр|просп|проспект|б-р|бульвар|ш|шоссе|проезд|городок|наб|пл|тупик|аллея|мкр)"
_TYPE_NORM = {"проспект": "пр-кт", "просп": "пр-кт", "пр": "пр-кт", "бульвар": "б-р", "шоссе": "ш"}

MONEY = r"(\d[\d\s]*(?:[.,]\d{1,2})?)\s*(?:руб\w*|р\.)"
DATE = r"(\d{2})\.(\d{2})\.(\d{4})(?!\d)"


def _tags(root) -> dict[str, str]:
    """Первое вхождение каждого тега верхнего уровня (вложенные fssp:IP не затирают корневые)."""
    out: dict[str, str] = {}
    for el in root:
        name = etree.QName(el).localname
        if name not in out and el.text and not len(el):
            out[name] = el.text.strip()
    return out


def _money(s: str) -> str:
    v = float(re.sub(r"\s", "", s).replace(",", "."))
    return "" if v == 0 else repr(v)


def _iso(m) -> str:
    d, mo, y = m
    return f"{y}-{mo}-{d}"


# ---------- адрес -------------------------------------------------------------------------

def _house(h: str, corp: str = "") -> str:
    h = h.strip().strip(",")
    m = re.fullmatch(r"(\d+)\s*([А-Яа-яA-Za-z])", h)
    if m:
        h = f"{m.group(1)}/{m.group(2)}"
    m = re.fullmatch(r"(\d+)\s*к\s*(\d+)", h)
    if m:
        h = f"{m.group(1)} корп.{m.group(2)}"
    corp = corp.strip()
    if corp:
        h += f"/{corp}" if re.fullmatch(r"[А-Яа-яA-Za-z]", corp) else f" корп.{corp}"
    return h


def _ordinal(name: str) -> str:
    """«5 Образцовая» / «4-я Условная» -> «Образцовая 5-я» (номер до 15; крупные числа - часть названия)."""
    m = re.fullmatch(r"(\d{1,2})(?:-\S{1,2})?\s+(\S.*)", name)
    if not m or int(m.group(1)) > 15:
        return name
    n, rest = m.group(1), m.group(2)
    last = rest.split()[0].lower()
    suf = "я" if last.endswith(("ая", "яя")) else "й" if last.endswith(("ый", "ий", "ой")) else "е"
    return f"{rest} {n}-{suf}"


def _dot(t: str) -> str:
    return t if t == "пр-кт" else t + "."


def _street_type(name: str, text: str, own: str | None) -> str:
    """Тип улицы: из самого адреса, иначе из текста постановления, иначе «ул.»."""
    if own:
        return _dot(_TYPE_NORM.get(own, own))
    core = re.sub(r"^\d{1,2}(?:-\S{1,2})?\s+", "", name).strip()
    if core:
        c = re.escape(core)
        after = re.search(rf"{c}\s+({STREET_TYPES})\b", text, re.I)  # «Учебный городок», «Примерный пер.»
        if after:
            t = after.group(1).lower()
            return _dot(_TYPE_NORM.get(t, t))
        before = re.search(rf"\b({STREET_TYPES})\.?\s+(?:\d{{1,2}}(?:-\S{{1,2}})?\s+)?{c}", text, re.I)
        if before:
            t = before.group(1).lower()
            return _dot(_TYPE_NORM.get(t, t))
    return "ул."


def parse_dbtr_address(adr: str, debt_text: str = "") -> dict[str, str]:
    """DbtrAdr -> {street, dom, kv}. Два формата: КЛАДР «643,индекс,рег,р-н,город,нп,улица,дом,кв»
    и «Россия»-формат / свободный «ул., д., кв., г.»."""
    adr = (adr or "").strip()
    street = house = flat = city = ""
    own_type = None
    parts = [p.strip() for p in adr.split(",")]
    if re.match(r"^\d{3},", adr) and len(parts) >= 9:
        street, house, flat = parts[6], parts[7], parts[8]
        city = next((p for p in (parts[4], parts[5]) if p), "")
        corp = ""
    else:
        corp = ""
        for p in parts:
            m = re.match(rf"^(?:({STREET_TYPES})\.?\s+(.+)|(.+?)\s+({STREET_TYPES})\.?)$", p, re.I)
            if m and not street and not re.match(r"^\d{6}$", p):
                own_type = (m.group(1) or m.group(4)).lower()
                street = (m.group(2) or m.group(3)).strip()
            elif re.match(r"^(?:д|дом)\.?\s*", p, re.I) and not house:
                house = re.sub(r"^(?:д|дом)\.?\s*", "", p, flags=re.I)
            elif re.match(r"^корп(?:ус)?\.?\s*", p, re.I):
                corp = re.sub(r"^корп(?:ус)?\.?\s*", "", p, flags=re.I)
            elif re.match(r"^(?:кв|квартира)\.?\s*", p, re.I) and not flat:
                flat = re.sub(r"^(?:кв|квартира)\.?\s*", "", p, flags=re.I)
            elif re.match(r"^г\.?\s+\S", p) and not city:
                city = p
    street = street.strip()
    city = re.sub(r"^г\.?\s*", "", city).strip()
    out = {"street": "", "dom": "", "kv": ""}
    if street:
        typ = _street_type(street, debt_text, own_type)
        out["street"] = f"г {city}, {typ} {_ordinal(street)}" if city else f"{typ} {_ordinal(street)}"
    out["dom"] = _house(house, corp) if house else ""
    out["kv"] = flat.strip()
    return out


# ---------- период и суммы из IdDebtText ------------------------------------------------------

def parse_debt_text(text: str) -> dict[str, str]:
    t = re.sub(r"\s+", " ", text or "")
    out = {"date_start": "", "date_end": "", "rub_deb": "", "rub_peni": "", "rub_poshlina": "", "rub_post": ""}
    # первый период с корректными датами (в 013 первый период содержит опечатку «20217»)
    for m in re.finditer(rf"за\s+период\s+(?:с\s*)?{DATE}\s*(?:г\.?)?\s*(?:по|-)\s*{DATE}", t, re.I):
        g = m.groups()
        out["date_start"], out["date_end"] = _iso(g[:3]), _iso(g[3:])
        break

    def amount(head: str, stop: str) -> str:
        # первая сумма после «head» до слов-стопов: не позволяет перескочить на соседнюю статью расходов
        rx = rf"{head}(?:(?!{stop}|в\s+размере).)*?в\s+размере\s*{MONEY}"
        m = re.search(rx, t, re.I)
        return _money(m.group(1)) if m else ""

    out["rub_deb"] = amount(r"задолженност\w*", r"пени|пошлин|почтов|расход|неустойк")
    out["rub_peni"] = amount(r"\bпени\b", r"руб|пошлин|почтов|расход|задолженност")
    out["rub_poshlina"] = amount(r"(?:государственн\w+\s+пошлин\w*|госпошлин\w*)", r"пени|почтов|задолженност")
    out["rub_post"] = amount(r"почтов\w+\s+расход\w*", r"пени|пошлин|задолженност")
    return out


# ---------- документ целиком ----------------------------------------------------------------

def doc_type2(code: str, doc_name: str) -> str:
    if code in DOCTYPE2:
        return DOCTYPE2[code]
    name = doc_name
    for rx, rep in _ABBR:
        name = re.sub(rx, rep, name, flags=re.I)
    return name


def parse_xml(path: str, rel: str | None = None) -> ExtractionResult:
    res = ExtractionResult(file=rel or path, doc_type=DocType.FSSP)
    root = etree.parse(path, etree.XMLParser(recover=False, resolve_entities=False)).getroot()
    tg = _tags(root)

    def put(col: str, val: str, note: str | None = None, quote: str | None = None, conf: float = 1.0):
        val = (val or "").strip()
        if val:
            res.fields[col] = FieldValue(value=val, status=FieldStatus.OK, confidence=conf, quote=quote, note=note)
        else:
            res.fields[col] = FieldValue(status=FieldStatus.MISSING, note=note)

    for col, tag in TAG_FIELDS.items():
        v = tg.get(tag, "")
        if col in ("IdDebtSum",) and v:
            v = repr(float(v))
        put(col, v, quote=f"<{tag}>")
    put("DocType2", doc_type2(tg.get("DocType", ""), tg.get("DocName", "")),
        note="по коду DocType" if tg.get("DocType") in DOCTYPE2 else "по тексту DocName (код не в справочнике)")

    adr = parse_dbtr_address(tg.get("DbtrAdr", ""), tg.get("IdDebtText", ""))
    for k, v in adr.items():
        put(k, v, quote=tg.get("DbtrAdr"), conf=0.9)
    dt = parse_debt_text(tg.get("IdDebtText", ""))
    for k, v in dt.items():
        put(k, v, note=None if v else "в IdDebtText не найдено", conf=0.9)
    # В полном тексте приказа сумма долга не прочиталась (сумма прописью, прочерки) - берём общую IdDebtSum,
    # но помечаем как неуверенное значение: разбивка на пени/пошлину неизвестна.
    if not dt["rub_deb"] and "Взыскать" in tg.get("IdDebtText", "") \
            and "руб" in tg.get("IdDebtText", "") and res.fields["IdDebtSum"].value:
        res.fields["rub_deb"] = FieldValue(
            value=res.fields["IdDebtSum"].value, status=FieldStatus.LOW_CONFIDENCE, confidence=0.5,
            note="сумма в тексте не прочитана; взята общая IdDebtSum, разбивка неизвестна")
    return res


def find_xml(path: Path) -> list[Path]:
    return sorted(path.rglob("*.xml")) if path.is_dir() else [path]
