from app.extract.form_writ import form_debtors
from app.legal.detector import LegalDetector, load_phrases
from app.models import Document, Page, TextSource


def _doc(t):
    return Document(path="x", pages=[Page(number=1, text=t, source=TextSource.OCR)])


def test_marker_phrases_with_gaps():
    det = LegalDetector(load_phrases("data/keywords.txt"), 90)
    assert len(det.phrases) == 10 and not any(p.lower().startswith("перечень") for p in det.phrases)
    hit = lambda t: [f.phrase for f in det.scan(_doc(t))]  # noqa: E731
    assert hit("Решение мирового судьи по делу отменить.") == ["решение…отменить"]
    assert hit("В удовлетворении остальной части исковых требований отказать") == ["в удовлетворении остальной части…требований отказать"]
    assert hit("Производство по делу прекратить") == ["производство по делу прекратить."]
    assert hit("Взыскать с должника задолженность за тепловую энергию") == []


def test_form_writ_debtor_blocks():
    t = ("Шаблонникова Вера Андреевна, 17.02.1931 г.р. Урож.: г. Теста Прож.: г. Тест, ул. Примерная, 24-119 "
         "Место работы не установлено. Примернов Марк Леонтьевич, дата рождения неразборчива "
         "Урож.: г. Теста Прож.: г. Тест, ул. Примерная, 24-119 Место работы не установлено.")
    d = form_debtors(t)
    assert [x.fio for x in d] == ["Шаблонникова Вера Андреевна", "Примернов Марк Леонтьевич"]
    assert d[0].birth == "1931-02-17" and d[1].birth is None
    assert (d[0].street, d[0].house, d[0].flat) == ("Примерная ул.", "24", "119")
