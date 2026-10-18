"""Инвентаризация входных файлов: python scripts/inventory.py <папка> [--csv out.csv]"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.ingestion.pdf_probe import probe_pdf  # noqa: E402


def main() -> None:
    root = Path(sys.argv[1])
    rows = []
    for f in sorted(root.rglob("*")):
        if f.suffix.lower() == ".pdf":
            pr = probe_pdf(str(f))
            layer = sum(p.has_text_layer for p in pr)
            kind = "text" if layer == len(pr) else "scan" if layer == 0 else "mixed"
            rows.append([str(f.relative_to(root)), "pdf", len(pr), kind, sum(p.chars for p in pr)])
        elif f.suffix.lower() == ".xml":
            rows.append([str(f.relative_to(root)), "xml", "", "xml", f.stat().st_size])
    for r in rows:
        print(*r, sep="\t")
    if "--csv" in sys.argv:
        out = sys.argv[sys.argv.index("--csv") + 1]
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "format", "pages", "kind", "chars"])
            w.writerows(rows)


if __name__ == "__main__":
    main()
