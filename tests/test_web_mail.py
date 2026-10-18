"""Веб-клиент: настройки, режимы писем (файлы .eml / SMTP / выкл.) на локальном тестовом SMTP-сервере."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.web.server as S

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(S, "JOBS_DIR", tmp_path / "jobs")
    return TestClient(S.app)


@pytest.fixture()
def smtp_server():
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")
    got = []

    class H:
        async def handle_DATA(self, server, session, envelope):
            got.append((envelope.mail_from, envelope.rcpt_tos))
            return "250 OK"

    c = aiosmtpd.Controller(H(), hostname="127.0.0.1", port=8026)
    c.start()
    yield got
    c.stop()


def _run(client, mode, extra=None):
    cfg = {"mail_mode": mode, "to": "jurist@example.org", "smtp_host": "127.0.0.1", "smtp_port": 8026,
           "smtp_security": "none", **(extra or {})}
    assert client.post("/api/settings", json=cfg).json() == {"ok": True}
    files = [("files", ("lawyer_sample.pdf", (FIX / "lawyer_sample.pdf").read_bytes(), "application/pdf")),
             ("files", ("fssp_005.xml", (FIX / "fssp_005.xml").read_bytes(), "text/xml"))]
    job = client.post("/api/process", files=files, data={"use_llm": "false"}).json()["job"]
    for _ in range(60):
        r = client.get(f"/api/jobs/{job}").json()
        if r["status"] != "running":
            return job, r
        time.sleep(0.5)
    raise AssertionError("задача не завершилась")


def test_password_is_not_returned(client):
    client.post("/api/settings", json={"smtp_password": "secret"})
    s = client.get("/api/settings").json()
    assert s["smtp_password"] == "" and s["has_password"] is True
    client.post("/api/settings", json={"smtp_host": "h"})   # пустой пароль не затирает сохранённый
    assert client.get("/api/settings").json()["has_password"] is True


def test_save_mode_writes_eml_and_zip(client):
    job, r = _run(client, "save")
    assert r["status"] == "done" and "emails.zip" in r["outputs"] and "result_xml.csv" in r["outputs"]
    assert "сохранено" in r["mail"]["lawyer_sample.pdf"]
    assert client.get(f"/api/jobs/{job}/files/emails.zip").status_code == 200


def test_smtp_mode_sends(client, smtp_server):
    _, r = _run(client, "smtp")
    assert r["mail"]["lawyer_sample.pdf"] == "отправлено на jurist@example.org"
    assert smtp_server == [("court-ai@localhost", ["jurist@example.org"])]


def test_off_mode_no_mail(client):
    _, r = _run(client, "off")
    assert r["mail"] == {} and "emails.zip" not in r["outputs"]
    assert any(x["needs_lawyer"] for x in r["court"])   # акт всё равно помечен для юриста


def test_smtp_check_reports_failure(client):
    r = client.post("/api/smtp/test", json={"smtp_host": "127.0.0.1", "smtp_port": 1, "smtp_security": "none"}).json()
    assert r["ok"] is False
