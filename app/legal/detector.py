"""Поиск фраз-маркеров, по которым акт направляется юристу.

Три уровня, от строгого к нестрогому:
1) точное вхождение после нормализации (регистр, ё, пробелы, пунктуация) - score 1.0;
2) совпадение по леммам (склонения и формы слов) - score 0.95;
3) нечёткое совпадение (искажения OCR) - score = сходство, порог из config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymorphy3
from rapidfuzz import fuzz

from app.models import Document, LegalFlag

_MORPH = pymorphy3.MorphAnalyzer()
_CACHE: dict[str, str] = {}


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def lemma(word: str) -> str:
    if word not in _CACHE:
        _CACHE[word] = _MORPH.parse(word)[0].normal_form
    return _CACHE[word]


def lemmas(s: str) -> list[str]:
    return [lemma(w) for w in norm(s).split()]


def load_phrases(path: str) -> list[str]:
    """Одна фраза на строку; нумерация вида «1.» или «1)» и пустые строки игнорируются."""
    out = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = re.sub(r"^\s*\d+[.)]\s*", "", line).strip(" \t-•\"«»")
        if line and not line.startswith("#") and not line.lower().startswith("перечень"):
            out.append(line)
    return out


@dataclass
class LegalDetector:
    phrases: list[str]
    fuzzy_threshold: int = 90

    def scan(self, doc: Document) -> list[LegalFlag]:
        flags: list[LegalFlag] = []
        # весь текст одной строкой: фраза может переноситься между строками и страницами
        pages = [(p.number, norm(p.text)) for p in doc.pages]
        full = " ".join(t for _, t in pages)
        full_l = lemmas(full)
        for phrase in self.phrases:
            pn = norm(phrase)
            if not pn:
                continue
            hit = self._find(pn, phrase, full, full_l)
            pn = norm(re.split(r"…|\.{3}", phrase)[0]) or pn
            if hit:
                score, ctx = hit
                flags.append(LegalFlag(phrase=phrase, page=self._page_of(pn, pages),
                                       context=ctx, score=score))
        return flags

    GAP = 200  # символов между частями фразы с «…»

    def _find(self, pn: str, raw: str, full: str, full_l: list[str]):
        # «…» в фразе - произвольный текст между частями (напр. «решение…отменить»)
        parts = [norm(x) for x in re.split(r"…|\.{3}", raw)]
        parts = [x for x in parts if x]
        if not parts:
            return None
        joiner = rf".{{0,{self.GAP}}}?"
        m = re.search(joiner.join(re.escape(x) for x in parts), full, re.S)
        if m:
            return 1.0, self._ctx(full, m.start(), m.end() - m.start())
        lem_text = " ".join(full_l)
        lparts = [" ".join(lemmas(x)) for x in parts]
        m = re.search(joiner.join(re.escape(x) for x in lparts), lem_text, re.S)
        if m:
            return 0.95, lem_text[max(0, m.start() - 40): m.end() + 40]
        # нечёткий поиск: каждая часть найдена с порогом, части идут по порядку и рядом
        pos, first, scores = 0, None, []
        for x in parts:
            al = fuzz.partial_ratio_alignment(x, full[pos:])
            if not al or al.score < self.fuzzy_threshold:
                return None
            st = pos + al.dest_start
            if first is not None and st - pos > self.GAP + len(x):
                return None
            first = st if first is None else first
            pos += al.dest_end
            scores.append(al.score)
        return min(scores) / 100, self._ctx(full, first, pos - first)

    @staticmethod
    def _ctx(text: str, start: int, length: int, pad: int = 80) -> str:
        return text[max(0, start - pad): start + length + pad]

    @staticmethod
    def _page_of(pn: str, pages: list[tuple[int, str]]) -> int | None:
        best, best_score = None, 0.0
        for num, t in pages:
            s = fuzz.partial_ratio(pn, t)
            if s > best_score:
                best, best_score = num, s
        return best
