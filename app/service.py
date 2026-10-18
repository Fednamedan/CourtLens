"""Пакетная обработка: общее ядро для CLI и веб-клиента."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config import load_config
from app.export.tables import write_csv, write_debtors_csv, write_xlsx, write_xml_csv, write_xml_xlsx
from app.legal.detector import LegalDetector, load_phrases
from app.llm.client import make_client
from app.models import ExtractionResult
from app.notify.mailer import Notifier
from app.pipeline import process_file

log = logging.getLogger(__name__)


@dataclass
class BatchOutcome:
    court: list[ExtractionResult] = field(default_factory=list)
    xml: list[ExtractionResult] = field(default_factory=list)
    mail: dict[str, str] = field(default_factory=dict)   # файл -> статус письма
    outputs: list[str] = field(default_factory=list)     # созданные файлы (относительно out)
    notes: list[str] = field(default_factory=list)


def collect_files(src: Path) -> list[Path]:
    if src.is_dir():
        return sorted(list(src.rglob("*.pdf")) + list(src.rglob("*.xml")))
    return [src]


def run_batch(files: list[Path], root: Path, out: Path, *, cfg: dict | None = None, use_llm: bool = False,
              mail_mode: str = "save", to: str | None = None, smtp: dict | None = None, workers: int = 2,
              keywords: Path | None = None, progress: Callable[[int, int, str], None] | None = None) -> BatchOutcome:
    """mail_mode: 'save' - письма пишутся как .eml в out/emails; 'smtp' - реальная отправка;
    'off' - письма не формируются. smtp - переопределение настроек SMTP (из веб-формы)."""
    cfg = load_config() if cfg is None else cfg
    cfg = {**cfg, "notify": {**cfg["notify"], **{k: v for k, v in (smtp or {}).items() if v not in (None, "")}}}
    cfg["notify"]["dry_run_dir"] = str(out / "emails")
    out.mkdir(parents=True, exist_ok=True)
    res = BatchOutcome()

    detector = None
    kw = keywords or (Path(cfg["legal"]["keywords_file"]) if Path(cfg["legal"]["keywords_file"]).exists() else None)
    if kw:
        detector = LegalDetector(load_phrases(str(kw)), cfg["legal"]["fuzzy_threshold"])
    else:
        res.notes.append("файл фраз-маркеров не задан: отбор для юриста отключён")
    client = make_client(cfg) if use_llm else None
    if use_llm and client is None:
        res.notes.append("LLM не настроена (LLM_BASE_URL пуст): работаю только на правилах")

    done = 0

    def run(p: Path):
        nonlocal done
        r = process_file(str(p), str(root), cfg, str(out / "cache"), detector, client)
        done += 1
        if progress:
            progress(done, len(files), p.name)
        return r

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(run, files))

    res.xml = [r for r in results if r.file.lower().endswith(".xml")]
    # PDF-копии постановлений ФССП в таблицу судебных актов не попадают: их данные берутся из XML
    res.court = [r for r in results if not r.file.lower().endswith(".xml") and r.doc_type.value != "постановление ФССП"]
    if res.court:
        write_csv(res.court, str(out / "result.csv"))
        write_xlsx(res.court, str(out / "result.xlsx"))
        res.outputs += ["result.csv", "result.xlsx"]
        if write_debtors_csv(res.court, str(out / "result_debtors.csv")):
            res.outputs.append("result_debtors.csv")
    if res.xml:
        write_xml_csv(res.xml, str(out / "result_xml.csv"))
        write_xml_xlsx(res.xml, str(out / "result_xml.xlsx"))
        res.outputs += ["result_xml.csv", "result_xml.xlsx"]

    flagged = [r for r in results if r.needs_lawyer]
    if flagged and mail_mode != "off":
        notifier = Notifier(cfg, str(out), send=(mail_mode == "smtp"), to=to)
        for r in flagged:
            try:
                res.mail[r.file] = notifier.notify(r, str(root / r.file))
            except Exception as exc:  # сбой SMTP не должен ронять обработку пакета
                res.mail[r.file] = f"ошибка отправки: {type(exc).__name__}: {exc}"
        if (out / "emails").exists():
            res.outputs.append("emails/")
    return res
