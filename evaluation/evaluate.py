"""Сравнение результата с эталоном.

python evaluation/evaluate.py pred.csv gold.csv [--errors] [--only-present]

Работает с обеими таблицами: ключ строки - «Файл» (ocr) или «FileName» (xml),
сравниваются все остальные столбцы эталона.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models import MONEY_FIELDS, XML_MONEY_FIELDS  # noqa: E402
from app.normalize.values import stem_key  # noqa: E402

KEYS = ("Файл", "FileName")
MONEY = MONEY_FIELDS | XML_MONEY_FIELDS


def load(path: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        key = next(k for k in KEYS if k in header)
        return header, {r[key]: r for r in reader}


def load_multi(path: str):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        out: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in reader:
            out[r["Файл"]].append(r)
        return list(reader.fieldnames or []), out


def norm(col: str, v: str | None) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    if col in MONEY:
        try:
            return f"{float(v.replace(',', '.').replace(' ', '')):.2f}"
        except ValueError:
            return v
    if col == "соответчики_фио":
        # падеж в перечне не нормируется (README), сравниваем множество лиц по основам слов
        # 3 буквы основы: «Вера»/«Веры» - один человек (эталон иногда в падеже документа)
        return " | ".join(sorted(" ".join(w[:3] for w in stem_key(x).split()) for x in v.split(",") if x.strip()))
    if col in ("улица", "дом", "кв"):
        return " ".join(v.replace(".", " ").replace(",", " ").split()).lower()
    return " ".join(v.split()).lower()


def main() -> None:
    _, pred = load(sys.argv[1])
    gold_header, gold = load(sys.argv[2])
    if "--debtors" in sys.argv:
        # акты с несколькими должниками: эталон ставит в строку одного из них, а какого - из текста не следует.
        # Для каждого файла берём строку того должника, который лучше всего совпал с эталоном (оптимистичная оценка).
        _, drows = load_multi(sys.argv[sys.argv.index("--debtors") + 1])
        for f, rows in drows.items():
            if f in gold:
                cols0 = [c for c in gold_header if c not in KEYS]
                best = max(rows, key=lambda r: sum(norm(c, r.get(c)) == norm(c, gold[f].get(c)) for c in cols0))
                pred[f] = best
    if "--only-present" in sys.argv:  # оценивать только файлы, которые реально обработаны
        gold = {f: g for f, g in gold.items() if f in pred}
    cols = [c for c in gold_header if c not in KEYS]
    hit: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    errors = []
    for f, g in gold.items():
        p = pred.get(f)
        for c in cols:
            total[c] += 1
            pv, gv = norm(c, (p or {}).get(c)), norm(c, g.get(c))
            if p is not None and pv == gv:
                hit[c] += 1
            else:
                errors.append((f, c, gv, pv if p is not None else "<нет строки>"))
    print(f"{'поле':<22}{'точность':>9}")
    for c in cols:
        print(f"{c:<22}{hit[c] / total[c]:>9.0%}")
    allhit, alltot = sum(hit.values()), sum(total.values())
    print(f"\nИТОГО: {allhit}/{alltot} = {allhit / alltot:.1%}")
    cats = {"пропуск (в эталоне есть, у нас пусто)": 0,
            "лишнее (у нас есть, в эталоне пусто: возможен пробел разметки)": 0,
            "расхождение значений": 0}
    for _, _, gv, pv in errors:
        cats["пропуск (в эталоне есть, у нас пусто)" if gv and not pv else
             "лишнее (у нас есть, в эталоне пусто: возможен пробел разметки)" if pv and not gv else
             "расхождение значений"] += 1
    print("\nРасхождения по причинам:")
    for k, v in cats.items():
        print(f"  {k}: {v}")
    if "--errors" in sys.argv:
        print("\nфайл | поле | ЭТАЛОН | РЕЗУЛЬТАТ")
        for e in errors:
            print(*e, sep=" | ")


if __name__ == "__main__":
    main()
