"""Письма ответственному сотруднику: dry-run (сохранение .eml) или отправка по SMTP."""
from __future__ import annotations

import hashlib
import json
import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.models import ExtractionResult


def build_message(res: ExtractionResult, pdf_path: str, to: str, sender: str) -> EmailMessage:
    case = res.fields.get("дело_номер")
    case_no = case.value if case and case.value else "номер дела не извлечён"
    msg = EmailMessage()
    msg["Subject"] = f"[Юрист] {res.doc_type.value}, дело {case_no}: требуется проверка"
    msg["From"], msg["To"] = sender, to
    lines = [
        f"Документ: {res.file}",
        f"Тип: {res.doc_type.value}",
        f"Дело: {case_no}",
    ]
    fio = res.fields.get("фио")
    if fio and fio.value:
        lines.append(f"Должник: {fio.value}")
    lines += ["", "Найденные фразы-маркеры:"]
    for fl in res.legal_flags:
        lines.append(f"- «{fl.phrase}» (стр. {fl.page}, сходство {fl.score:.0%})")
        lines.append(f"    …{fl.context}…")
    problems = [n for n, f in res.fields.items() if f.status.value != "ok"]
    if problems:
        lines += ["", "Поля, требующие проверки: " + ", ".join(problems)]
    lines += ["", "Исходный документ во вложении."]
    msg.set_content("\n".join(lines))
    data = Path(pdf_path).read_bytes()
    msg.add_attachment(data, maintype="application", subtype="pdf", filename=Path(pdf_path).name)
    return msg


def _connect(cfg: dict):
    """Открывает SMTP-соединение по настройкам (starttls | ssl | none) и авторизуется."""
    host, port = cfg.get("smtp_host"), int(cfg.get("smtp_port") or 587)
    if not host or "${" in str(host):
        raise ValueError("не задан SMTP-сервер")
    mode = str(cfg.get("smtp_security", "starttls")).lower()
    s = smtplib.SMTP_SSL(host, port, timeout=30) if mode == "ssl" else smtplib.SMTP(host, port, timeout=30)
    try:
        if mode == "starttls":
            s.starttls()
        if cfg.get("smtp_user"):
            s.login(cfg["smtp_user"], cfg.get("smtp_password") or "")
    except Exception:
        s.close()
        raise
    return s


def check_smtp(cfg: dict, test_to: str | None = None) -> str:
    """Проверка настроек: соединение и вход; при test_to - ещё и тестовое письмо."""
    with _connect(cfg) as s:
        if test_to:
            m = EmailMessage()
            m["Subject"], m["From"], m["To"] = "court-ai: тест настроек SMTP", (
                cfg.get("sender") or cfg.get("smtp_user") or "court-ai@localhost"), test_to
            m.set_content("Настройки SMTP работают. Это тестовое письмо от court-ai.")
            s.send_message(m)
            return f"соединение установлено, тестовое письмо отправлено на {test_to}"
    return "соединение и авторизация успешны"


class Notifier:
    """Идемпотентность: письмо по одному и тому же файлу (sha256) не уходит повторно."""

    def __init__(self, cfg: dict, out_dir: str, send: bool = False, to: str | None = None):
        # получатель: параметр запуска (--to / поле формы клиентской части) важнее config.yaml
        self.cfg, self.send, self.out = dict(cfg["notify"]), send, Path(out_dir)
        if to:
            self.cfg["to"] = to
        self.ledger_path = self.out / "sent.json"
        self.ledger: dict[str, str] = (json.loads(self.ledger_path.read_text())
                                       if self.ledger_path.exists() else {})

    def notify(self, res: ExtractionResult, pdf_path: str) -> str:
        digest = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
        if digest in self.ledger:
            return "уже отправлялось"
        to = self.cfg.get("to")
        if not to or "${" in str(to):
            if self.send:
                return "не отправлено: адрес получателя не задан (--to, поле в клиенте или LEGAL_EMAIL)"
            to = "(получатель не задан)"
        msg = build_message(res, pdf_path, to, self.cfg.get("sender") or self.cfg.get("smtp_user") or "court-ai@localhost")
        if self.send:
            with _connect(self.cfg) as s:
                s.send_message(msg)
            status = f"отправлено на {to}"
        else:
            d = Path(self.cfg.get("dry_run_dir", "out/emails"))
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{Path(pdf_path).stem}.eml").write_bytes(bytes(msg))
            status = f"письмо сохранено в файл {d / (Path(pdf_path).stem + '.eml')}"
        self.ledger[digest] = status
        self.out.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(self.ledger, ensure_ascii=False, indent=1))
        return status
