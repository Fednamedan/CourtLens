"""OCR одной страницы: растеризация, предобработка, Tesseract."""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytesseract

from app.config import ROOT, load_config


def render_page(path: str, page_index: int, dpi: int = 300) -> np.ndarray:
    with pymupdf.open(path) as doc:
        pix = doc[page_index].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def deskew(gray: np.ndarray, max_angle: float = 5.0) -> np.ndarray:
    """Выравнивание небольшого наклона по минимальному прямоугольнику текста."""
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    pts = cv2.findNonZero(inv)
    if pts is None or len(pts) < 500:
        return gray
    angle = cv2.minAreaRect(pts)[-1]
    angle = angle - 90 if angle > 45 else angle
    if abs(angle) < 0.2 or abs(angle) > max_angle:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def preprocess(gray: np.ndarray, mode: str = "none") -> np.ndarray:
    """none | deskew | denoise. По абляции на 2 сканах разница в пределах шума,
    шумоподавление дороже по времени (~+30%), поэтому по умолчанию отключено."""
    if mode == "none":
        return gray
    gray = deskew(gray)
    return cv2.fastNlMeansDenoising(gray, None, h=8) if mode == "denoise" else gray


def _tess_config(cfg: dict) -> str:
    tessdata = Path(cfg["ocr"].get("tessdata_dir") or "data/tessdata")
    if not tessdata.is_absolute():
        tessdata = ROOT / tessdata
    if tessdata.exists():
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
    return f"--psm {cfg['ocr'].get('psm', 6)}"


def ocr_page(path: str, page_index: int, cfg: dict | None = None) -> tuple[str, float]:
    """Возвращает (текст, средняя уверенность 0..1)."""
    cfg = cfg or load_config()
    img = render_page(path, page_index, cfg["ocr"].get("dpi", 300))
    img = preprocess(img, str(cfg["ocr"].get("preprocess", "none")))
    conf_cfg = _tess_config(cfg)
    lang = cfg["ocr"].get("lang", "rus")
    text = pytesseract.image_to_string(img, lang=lang, config=conf_cfg)
    data = pytesseract.image_to_data(img, lang=lang, config=conf_cfg,
                                     output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, t in zip(data["conf"], data["text"])
             if t.strip() and float(c) >= 0]
    return text, (sum(confs) / len(confs) / 100 if confs else 0.0)
