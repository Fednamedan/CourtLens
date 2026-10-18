"""CLI: python -m app process <файл|папка> [--root ...] [--out out/] [--legal-keywords file] [--llm] [--notify]"""
from __future__ import annotations

import logging
from pathlib import Path

import typer

from app.service import collect_files, run_batch

app = typer.Typer(add_completion=False, help="ИИ-помощник для судебных документов и документов ФССП")


@app.callback()
def _root() -> None:
    """Команды: process - обработка документов."""


@app.command()
def process(
    input: Path = typer.Argument(..., help="PDF-файл или папка с документами"),
    root: Path = typer.Option(None, help="Корень набора: пути в столбце «Файл» считаются от него"),
    out: Path = typer.Option(Path("out"), help="Папка результатов"),
    legal_keywords: Path = typer.Option(None, help="TXT с фразами-маркерами для юриста"),
    llm: bool = typer.Option(False, "--llm", help="Добирать пропущенные поля через LLM из config.yaml"),
    notify: bool = typer.Option(False, "--notify", help="Отправлять письма (иначе dry-run: .eml в out/emails)"),
    to: str = typer.Option(None, "--to", help="Адрес ответственного сотрудника (иначе LEGAL_EMAIL из .env)"),
    smtp_host: str = typer.Option(None, help="SMTP-сервер (иначе SMTP_HOST из .env)"),
    smtp_port: int = typer.Option(None, help="Порт SMTP (иначе из config.yaml)"),
    smtp_user: str = typer.Option(None, help="Логин SMTP"),
    smtp_password: str = typer.Option(None, help="Пароль SMTP (лучше через SMTP_PASSWORD в .env)"),
    smtp_security: str = typer.Option(None, help="starttls | ssl | none"),
    workers: int = typer.Option(2, help="Параллельных процессов обработки"),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = root or (input if input.is_dir() else input.parent)
    files = collect_files(input)
    if not files:
        raise typer.BadParameter("PDF/XML-файлы не найдены")
    smtp_over = {"smtp_host": smtp_host, "smtp_port": smtp_port, "smtp_user": smtp_user,
                 "smtp_password": smtp_password, "smtp_security": smtp_security}
    res = run_batch(files, root, out, use_llm=llm, mail_mode="smtp" if notify else "save", to=to,
                    smtp=smtp_over, workers=workers, keywords=legal_keywords)
    for n in res.notes:
        typer.echo(f"Предупреждение: {n}")
    typer.echo(f"Судебные акты: {len(res.court)}, постановления ФССП (XML): {len(res.xml)}; файлы в {out}: "
               + ", ".join(res.outputs))
    for f, st in res.mail.items():
        typer.echo(f"  юристу: {f} -> {st}")
    for r in res.court + res.xml:
        if r.errors:
            typer.echo(f"  ! {r.file}: {'; '.join(r.errors)}")


@app.command()
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8000)) -> None:
    """Веб-клиент: загрузка файлов, настройка SMTP, обработка, скачивание таблиц."""
    import uvicorn

    uvicorn.run("app.web.server:app", host=host, port=port)


if __name__ == "__main__":
    app()
