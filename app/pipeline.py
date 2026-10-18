"""Сквозная обработка одного файла: текст -> тип -> поля -> проверка -> маркеры."""
from __future__ import annotations

import logging
from pathlib import Path

from app.classify.rules import classify
from app.config import load_config
from app.extract.rules import extract_court_fields, extract_debtor_rows
from app.models import DocType, ExtractionResult, FieldStatus, FieldValue
from app.text.loader import load_document
from app.validate.checks import validate, validate_xml

log = logging.getLogger(__name__)


def process_file(path: str, root: str | None = None, cfg: dict | None = None,
                 cache_dir: str | None = "cache/ocr", detector=None, llm=None) -> ExtractionResult:
    cfg = cfg or load_config()
    rel = str(Path(path).relative_to(root)) if root else path
    res = ExtractionResult(file=rel)
    try:
        if path.lower().endswith(".xml"):
            from app.xmlfssp.parser import parse_xml

            res = parse_xml(path, rel)
            validate_xml(res)
            return res
        doc = load_document(path, cfg, cache_dir)
        res.doc_type, _ = classify(doc.full_text)
        if res.doc_type == DocType.UNKNOWN:
            res.errors.append("тип документа не определён - нужна ручная проверка")
        if res.doc_type == DocType.FSSP:
            res.errors.append("PDF-копия постановления ФССП: данные берутся из парного XML")
            return res
        res.fields = extract_court_fields(doc)
        res.debtor_rows = extract_debtor_rows(doc, res.fields)
        if llm is not None:
            from app.extract.llm_fill import fill_missing

            fill_missing(doc, res, llm)
        validate(res, cfg["validation"]["low_confidence_below"])
        if detector is not None:
            res.legal_flags = detector.scan(doc)
    except Exception as exc:  # один плохой файл не должен останавливать пакет
        log.exception("ошибка обработки %s", path)
        res.errors.append(f"ошибка обработки: {type(exc).__name__}: {exc}")
    return res
