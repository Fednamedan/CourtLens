"""Нормализация значений к формату эталона."""
from __future__ import annotations

import re
from datetime import date

import pymorphy3

_MORPH = pymorphy3.MorphAnalyzer()

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# типичные OCR-путаницы внутри числовых токенов
_DIGIT_FIX = str.maketrans({"]": "1", "[": "1", "|": "1", "l": "1", "I": "1", "O": "0",
                            "О": "0", "o": "0", "о": "0", "S": "5", "З": "3", "B": "8", "В": "8"})


def fix_digits(token: str) -> str:
    """Исправляет похожие на цифры символы, только если токен почти целиком цифровой."""
    digits = sum(c.isdigit() for c in token)
    if len(token) and digits >= len(token) - 2:
        return token.translate(_DIGIT_FIX)
    return token


def parse_date(s: str) -> str | None:
    """'25.05.1969' или '6 января 2029' -> '1969-05-25'. None, если не дата."""
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})[.\s](\d{2})[.\s](\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", s.lower())
        if not m or m.group(2) not in MONTHS:
            return None
        d, mo, y = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_money(rub: str, kop: str | None = None) -> float | None:
    rub = re.sub(r"\s", "", rub).replace(",", ".").replace("!", "1")
    try:
        val = float(rub)
    except ValueError:
        return None
    if kop:
        val += int(kop) / 100
    return round(val, 2)


def clean_passport(series: str, number: str) -> str:
    return f"{fix_digits(series)} {fix_digits(number)}"


# ---------------------------------------------------------------- ФИО
def _gender_from_patronymic(tokens: list[str]) -> str | None:
    if len(tokens) >= 3:
        p = tokens[2].lower()
        if p.endswith(("ич", "ича", "ичу", "ичем", "иче")):
            return "m"
        if p.endswith(("вна", "вны", "вне", "вну", "вной", "чна", "чны", "чне", "чну", "чной")):
            return "f"
    return None


def _surname_nom(word: str, gender: str | None) -> str:
    """Правила для фамилий на -ов/-ев/-ин/-ский (словарь pymorphy их часто не знает)."""
    w = word
    low = w.lower()
    pairs_f = [("ской", "ская"), ("ую", "ая"), ("ова", "ова"), ("ева", "ева"), ("ина", "ина"),
               ("овой", "ова"), ("евой", "ева"), ("иной", "ина"),
               ("ову", "ова"), ("еву", "ева"), ("ину", "ина"),
               ("овою", "ова"), ("евою", "ева"), ("иною", "ина")]
    pairs_m = [("ского", "ский"), ("скому", "ский"), ("ским", "ский"), ("ском", "ский"),
               ("ова", "ов"), ("ева", "ев"), ("ина", "ин"),
               ("ову", "ов"), ("еву", "ев"), ("ину", "ин"),
               ("овым", "ов"), ("евым", "ев"), ("иным", "ин"),
               ("ове", "ов"), ("еве", "ев"), ("ине", "ин")]
    table = pairs_f if gender == "f" else pairs_m if gender == "m" else pairs_m + pairs_f
    for suf, rep in sorted(table, key=lambda x: -len(x[0])):
        if low.endswith(suf):
            return w[: len(w) - len(suf)] + rep
    return w


def to_nominative(full: str) -> str:
    """'Макетновой Юлии Николаевны' -> 'Макетнова Юлия Николаевна'."""
    tokens = full.split()
    if len(tokens) < 2:
        return full
    gender = _gender_from_patronymic(tokens)
    out = [_surname_nom(tokens[0], gender)]
    for t in tokens[1:]:
        parses = [p for p in _MORPH.parse(t) if {"Name", "Patr"} & set(p.tag.grammemes)]
        p = parses[0] if parses else None
        nom = p.inflect({"nomn"}) if p else None
        if nom is None:
            out.append(t)
            continue
        w = nom.word
        out.append(w[0].upper() + w[1:])
    return " ".join(out)


def stem_key(full: str) -> str:
    """Падежно-нечувствительный ключ ФИО для сравнения списков соответчиков."""
    return " ".join(sorted(t.lower().replace("ё", "е")[:4] for t in full.replace("(", " ").replace(")", " ").split()))


# ---------------------------------------------------------------- адрес
_TYPES = (r"(?:ул|улица|пр-кт|просп|проспект|пер|переулок|б-р|бульвар|шоссе|наб|набережная|"
          r"проезд|пл|площадь|пр(?=\.))")
_TYPE_MAP = {"ул": "ул.", "улица": "ул.", "пр-кт": "проспект", "просп": "проспект", "проспект": "проспект",
             "пр": "проспект", "пер": "пер.", "переулок": "пер."}
_PART = r"(?:сек|с|ком|к|корп)"


def _norm_type(t: str) -> str:
    t = t.lower().strip(".")
    return _TYPE_MAP.get(t, t)


def _fmt_house(num: str, letter: str | None) -> str:
    letter = re.sub(r"[\s/\\]", "", letter or "")
    return f"{num}/{letter.upper()}" if letter else num


def _flat_after(rest: str) -> str | None:
    m = re.match(r"\s*[,.]?\s*к[вь](?:артира)?\.?\s*([\w/]+)", rest, re.I)
    if m:
        return m.group(1).upper()
    m = re.match(r"\s*-\s*([\w/]+)", rest)
    if m:
        return m.group(1).upper()
    m = re.match(rf"\s*[,.]?\s*({_PART}\.?\s*\d+\w*(?:\s*[,.]?\s*{_PART}\.?\s*\d+\w*)*)", rest, re.I)
    if m:  # «сек. 8, ком. 10» -> «сек. 8 ком. 10» (формат эталона)
        return re.sub(r"\s*,\s*", " ", m.group(1)).strip()
    return None


def parse_address(seg: str) -> tuple[str | None, str | None, str | None, str]:
    """-> (улица, дом, квартира, стиль). Стиль: 'text' (ул. X, д. 1, кв. 2) или 'kladr' (643,,55,,Тест,X,1,2)."""
    s = re.sub(r"\.\s*\.", ".", seg.replace("‚", ",").replace("„", ","))
    street = None
    end = 0
    m = re.search(rf"(?<![А-Яа-яЁё])({_TYPES})\.?\s+([А-ЯЁ0-9][^,]*?)(?=\s*,|\s+[Дд]\.|\s*\.\s*[Дд]\b)", s)
    if m:
        street, end = f"{m.group(2).strip()} {_norm_type(m.group(1))}", m.end()
    else:
        m = re.search(rf"([А-ЯЁ0-9][^,.]*?)\s+({_TYPES})\.?\s*(?=[,.]|[Дд]\b)", s)
        if m:
            street, end = f"{m.group(1).strip()} {_norm_type(m.group(2))}", m.end()
    if street:
        hm = re.search(r"\b[Дд](?:ом)?\.?\s*(\d+)\s*([/\\]?\s*[А-Яа-яA-Za-z](?![\w.]))?", s[end:])
        if hm:
            rest = s[end:][hm.end():hm.end() + 60]
            return street, _fmt_house(hm.group(1), hm.group(2)), _flat_after(rest), "text"
        hm = re.match(r"[\s,._]*(\d+)\s*([/\\]?\s*[А-Яа-яA-Za-z](?![\w.]))?", s[end:])
        if hm:
            rest = s[end:][hm.end():hm.end() + 60]
            return street, _fmt_house(hm.group(1), hm.group(2)), _flat_after(rest), "text"
        return street, None, None, "text"
    km = re.search(r",\s*([^,]+),\s*(\d+[А-Яа-я/]*)\s*,\s*([^,]+?)\s*(?:,|$)", s)
    if km and not re.search(r"\d{3,}", km.group(1)):
        flat = re.sub(r"[\s.]", "", km.group(3)).upper()
        return f"{km.group(1).strip()} ул.", km.group(2).upper(), flat, "kladr"
    return None, None, None, "none"


def parse_street(addr: str) -> str | None:
    return parse_address(addr)[0]


def parse_house_flat(addr: str) -> tuple[str | None, str | None]:
    _, h, f, _ = parse_address(addr)
    return h, f
