"""Веб-клиент court-ai: загрузка актов, настройка писем (SMTP или сохранение .eml), запуск, скачивание таблиц.

Запуск: python -m app serve  ->  http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.config import ROOT, load_config
from app.models import OCR_COLUMNS, XML_COLUMNS, ExtractionResult
from app.notify.mailer import check_smtp
from app.service import run_batch

app = FastAPI(title="court-ai")
SETTINGS = ROOT / "data" / "settings.json"
JOBS_DIR = ROOT / "web_jobs"
JOBS: dict[str, dict] = {}
DEFAULTS = {"mail_mode": "save", "to": "", "sender": "", "smtp_host": "", "smtp_port": 587,
            "smtp_security": "starttls", "smtp_user": "", "smtp_password": ""}


# ---------------------------------------------------------------- настройки
def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS.exists():
        s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    return s


class Settings(BaseModel):
    mail_mode: str = "save"          # save | smtp | off
    to: str = ""
    sender: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"  # starttls | ssl | none
    smtp_user: str = ""
    smtp_password: str = ""          # пусто = оставить сохранённый


def merged(new: Settings) -> dict:
    cur = load_settings()
    data = new.model_dump()
    if not data["smtp_password"]:
        data["smtp_password"] = cur.get("smtp_password", "")
    return data


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    has_pw = bool(s.pop("smtp_password", ""))
    return {**s, "smtp_password": "", "has_password": has_pw}


@app.post("/api/settings")
def save_settings(s: Settings):
    if s.mail_mode not in ("save", "smtp", "off") or s.smtp_security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "недопустимый режим")
    data = merged(s)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True}


class SmtpTest(Settings):
    send_test: bool = False


@app.post("/api/smtp/test")
def smtp_test(t: SmtpTest):
    cfg = merged(t)
    try:
        msg = check_smtp(cfg, cfg["to"] if t.send_test else None)
        if t.send_test and not cfg["to"]:
            raise ValueError("для тестового письма укажите адрес получателя")
        return {"ok": True, "message": msg}
    except Exception as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- обработка
def _serialize(r: ExtractionResult, columns: list[str]) -> dict:
    return {
        "file": r.file, "doc_type": r.doc_type.value, "errors": r.errors,
        "needs_lawyer": r.needs_lawyer,
        "legal": [{"phrase": f.phrase, "page": f.page, "score": round(f.score, 2)} for f in r.legal_flags],
        "fields": {c: {"value": r.fields[c].value, "status": r.fields[c].status.value, "note": r.fields[c].note}
                   for c in columns if c in r.fields},
    }


def _work(job_id: str, files: list[Path], use_llm: bool) -> None:
    job = JOBS[job_id]
    s = load_settings()
    out = JOBS_DIR / job_id / "out"
    try:
        smtp = {k: s[k] for k in ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password", "sender")}
        res = run_batch(files, JOBS_DIR / job_id / "in", out, cfg=load_config(), use_llm=use_llm,
                        mail_mode=s["mail_mode"], to=s["to"] or None, smtp=smtp, workers=2,
                        progress=lambda d, n, name: job.update(done=d, total=n, current=name))
        if (out / "emails").exists():
            with zipfile.ZipFile(out / "emails.zip", "w") as z:
                for f in (out / "emails").glob("*.eml"):
                    z.write(f, f.name)
            res.outputs.append("emails.zip")
        job.update(status="done", court_cols=OCR_COLUMNS[2:], xml_cols=XML_COLUMNS[1:], notes=res.notes, mail=res.mail, outputs=res.outputs,
                   court=[_serialize(r, OCR_COLUMNS[2:]) for r in res.court],
                   xml=[_serialize(r, XML_COLUMNS[1:]) for r in res.xml])
    except Exception as exc:
        job.update(status="error", error=f"{type(exc).__name__}: {exc}")


@app.post("/api/process")
async def process(files: list[UploadFile] = File(...), use_llm: bool = Form(False)):
    job_id = uuid.uuid4().hex[:10]
    d = JOBS_DIR / job_id / "in"
    d.mkdir(parents=True)
    saved: list[Path] = []
    for f in files:
        name = Path(f.filename or "file").name
        if not name.lower().endswith((".pdf", ".xml")):
            continue
        p = d / name
        with open(p, "wb") as fh:
            shutil.copyfileobj(f.file, fh)
        saved.append(p)
    if not saved:
        raise HTTPException(400, "нужны файлы PDF или XML")
    JOBS[job_id] = {"status": "running", "done": 0, "total": len(saved), "current": ""}
    threading.Thread(target=_work, args=(job_id, saved, use_llm), daemon=True).start()
    return {"job": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "задача не найдена")
    return JOBS[job_id]


@app.get("/api/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str):
    p = JOBS_DIR / job_id / "out" / Path(name).name
    if job_id not in JOBS or not p.is_file():
        raise HTTPException(404, "файл не найден")
    return FileResponse(p, filename=p.name)


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
