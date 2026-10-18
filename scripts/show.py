"""Показать плоский текст документа из кэша/слоя: python scripts/show.py <корень> <ocr_NNN> [regex]"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.text.loader import load_document
root, name = sys.argv[1], sys.argv[2]
pdf = next(Path(root).rglob(f"{name}.pdf"))
flat = re.sub(r"\s+", " ", load_document(str(pdf), cache_dir="out17/cache").full_text)
if len(sys.argv) > 3:
    for m in re.finditer(sys.argv[3], flat):
        print("…", flat[max(0, m.start()-60): m.end()+90])
else:
    print(flat)
