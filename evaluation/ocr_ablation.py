"""Абляция предобработки OCR: сколько ключевых значений (из эталона) найдено в тексте.

python evaluation/ocr_ablation.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2  # noqa: E402
import pytesseract  # noqa: E402

from app.config import load_config  # noqa: E402
from app.text.ocr import _tess_config, deskew, render_page  # noqa: E402

PDF_DIR = "/mnt/user-data/uploads"
GOLD = {  # значения из labels/ocr.csv в том виде, как они печатаются в документе
    "ocr_001": ["2-8074/2029", "25.05.1969", "63008,92", "59000,96", "03.07.2025", "02.08.2028",
                "47/А", "6 января 2029", "2030,00"],
    "ocr_006": ["2-8416/2033", "14.02.2033", "15.05.1992", "8228 564951", "17К4",
                "85422,50", "47904,60", "04.01.2028", "02.01.2033"],
}
VARIANTS = {
    "без предобработки": lambda g: g,
    "deskew": deskew,
    "deskew+denoise": lambda g: cv2.fastNlMeansDenoising(deskew(g), None, h=8),
}


def main() -> None:
    cfg = load_config()
    conf = _tess_config(cfg)
    print(f"{'вариант':<22}{'найдено':>9}{'время, с':>11}")
    for name, fn in VARIANTS.items():
        hit = tot = 0
        t0 = time.time()
        for doc, golds in GOLD.items():
            text = re.sub(r"\s+", " ", pytesseract.image_to_string(
                fn(render_page(f"{PDF_DIR}/{doc}.pdf", 0, 300)), lang="rus", config=conf))
            for g in golds:
                tot += 1
                hit += g in text
        print(f"{name:<22}{hit:>5}/{tot:<3}{time.time() - t0:>11.1f}")


if __name__ == "__main__":
    main()
