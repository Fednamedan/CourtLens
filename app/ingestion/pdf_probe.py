"""Постраничное определение наличия текстового слоя в PDF."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

CYR = re.compile(r"[А-Яа-яЁё]")


@dataclass
class PageProbe:
    number: int
    chars: int
    cyrillic_share: float
    has_images: bool
    has_text_layer: bool


def probe_pdf(path: str, min_chars: int = 40, min_cyr: float = 0.3) -> list[PageProbe]:
    out: list[PageProbe] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            letters = [c for c in text if c.isalpha()]
            cyr = len(CYR.findall(text))
            share = cyr / len(letters) if letters else 0.0
            ok = len(text.strip()) >= min_chars and share >= min_cyr
            out.append(PageProbe(i, len(text.strip()), round(share, 2),
                                 bool(page.get_images()), ok))
    return out
