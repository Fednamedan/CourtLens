"""Единая точка получения текста: текстовый слой, где он есть, иначе OCR."""
from __future__ import annotations

import logging
from pathlib import Path

import pymupdf

from app.config import load_config
from app.ingestion.pdf_probe import probe_pdf
from app.models import Document, Page, TextSource

log = logging.getLogger(__name__)


def load_document(path: str, cfg: dict | None = None, cache_dir: str | None = None) -> Document:
    cfg = cfg or load_config()
    ing = cfg["ingestion"]
    probes = probe_pdf(path, ing["min_chars_per_page"], ing["min_cyrillic_share"])
    pages: list[Page] = []
    with pymupdf.open(path) as doc:
        for pr in probes:
            if pr.has_text_layer:
                pages.append(Page(number=pr.number, text=doc[pr.number - 1].get_text("text"),
                                  source=TextSource.TEXT_LAYER))
                continue
            text, conf = _ocr_cached(path, pr.number - 1, cfg, cache_dir)
            if conf < ing.get("blank_page_conf", 0.45):
                # пустая оборотная сторона / фон сканера: Tesseract выдаёт мусор с низкой уверенностью
                pages.append(Page(number=pr.number, text="", source=TextSource.OCR, ocr_confidence=None))
                continue
            pages.append(Page(number=pr.number, text=text, source=TextSource.OCR,
                              ocr_confidence=conf))
    return Document(path=path, pages=pages)


def _ocr_cached(path: str, idx: int, cfg: dict, cache_dir: str | None):
    from app.text.ocr import ocr_page

    if not cache_dir:
        return ocr_page(path, idx, cfg)
    key = Path(cache_dir) / f"{Path(path).stem}_p{idx + 1}.txt"
    conf_key = key.with_suffix(".conf")
    if key.exists() and conf_key.exists():
        return key.read_text(encoding="utf-8"), float(conf_key.read_text())
    text, conf = ocr_page(path, idx, cfg)
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(text, encoding="utf-8")
    conf_key.write_text(str(conf))
    return text, conf
