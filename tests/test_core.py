from pathlib import Path

import pytest

from app.classify.rules import classify
from app.extract.llm_fill import fill_missing, quote_in_text
from app.extract.rules import extract_court_fields
from app.legal.detector import LegalDetector
from app.llm.client import extract_json
from app.models import Document, DocType, ExtractionResult, Page, TextSource
from app.normalize.values import (
    fix_digits, parse_date, parse_house_flat, parse_money, parse_street, to_nominative,
)
from app.validate.checks import validate

FX = Path(__file__).parent / "fixtures"


def doc(name: str, src=TextSource.OCR) -> Document:
    return Document(path=name, pages=[Page(number=1, text=(FX / f"{name}.txt").read_text(encoding="utf-8"),
                                           source=src, ocr_confidence=0.9)])


# ---------- нормализация
def test_dates_and_money():
    assert parse_date("6 января 2029") == "2029-01-06"
    assert parse_date("25.05.1969") == "1969-05-25"
    assert parse_date("31.02.2020") is None
    assert parse_money("52626", "94") == 52626.94
    assert parse_money("63 008,92") == 63008.92


def test_fix_digits_only_on_digit_like_tokens():
    assert fix_digits("56495]") == "564951"
    assert fix_digits("Привет") == "Привет"


@pytest.mark.parametrize("src,exp", [
    ("Макетновой Юлии Николаевны", "Макетнова Юлия Николаевна"),
    ("Пробникова Савелия Викторовича", "Пробников Савелий Викторович"),
    ("Моделнова Дениса Ивановича", "Моделнов Денис Иванович"),
])
def test_to_nominative(src, exp):
    assert to_nominative(src) == exp


def test_address_parsing():
    assert parse_street("ул. Пробная, д. 47/А, кв. 28,") == "Пробная ул."
    assert parse_house_flat("ул. Пробная, д. 47/А, кв. 28,") == ("47/А", "28")
    assert parse_street("адрес: Шаблонная проспект, д. 1, кв. 166, г.") == "Шаблонная проспект"
    assert parse_house_flat("ул. 2-й Проезд,д. 23-17К4, Текст") == ("23", "17К4")


# ---------- классификация
@pytest.mark.parametrize("name,exp", [("ocr_001", DocType.ORDER), ("ocr_006", DocType.ORDER_E),
                                      ("ocr_017", DocType.WRIT_E)])
def test_classify(name, exp):
    assert classify((FX / f"{name}.txt").read_text(encoding="utf-8"))[0] == exp


def test_unknown_when_no_header():
    assert classify("Квитанция об оплате услуг связи за март")[0] == DocType.UNKNOWN


# ---------- извлечение (эталон из labels/ocr.csv)
def test_order_scan_001():
    f = extract_court_fields(doc("ocr_001"))
    v = {k: x.value for k, x in f.items()}
    assert v["дело_номер"] == "2-8074/2029" and v["дело_дата"] == "2029-01-06"
    assert (v["дз_осн"], v["дз_пени"], v["дз_пошлина"]) == (63008.92, 59000.96, 2030.0)
    assert v["фио"] == "Макетнов Леонтий Степанович" and v["соответчики_кол-во"] == 4
    assert (v["улица"], v["дом"], v["кв"]) == ("Пробная ул.", "47/А", "28")
    assert "паспорт" not in f  # в документе нет - не выдумываем


def test_writ_electronic_017():
    f = extract_court_fields(doc("ocr_017", TextSource.TEXT_LAYER))
    v = {k: x.value for k, x in f.items()}
    assert v["паспорт"] == "8962 357599" and v["инн"] == "810801625884"
    assert v["дз_осн"] == 52626.94 and v["дз_пени"] == 2185.1 and v["дз_пошлина"] == 8000.0
    assert v["период_дз_начало"] == "2028-07-03" and v["дата рождения"] == "1989-10-30"


def test_ocr_damaged_date_is_flagged_not_guessed():
    f = extract_court_fields(doc("ocr_006"))
    assert f["дело_номер"].value == "2-8416/2033"
    assert f["дело_дата"].value is None and "частично" in f["дело_дата"].note


# ---------- валидация
def test_validation_flags_bad_values():
    res = ExtractionResult(file="x", fields=extract_court_fields(doc("ocr_001")))
    res.fields["инн"] = res.fields["дз_осн"].model_copy(update={"value": "123"})
    validate(res)
    assert res.fields["инн"].status.value == "invalid"


# ---------- LLM: защита от галлюцинаций
class FakeLLM:
    def __init__(self, answer): self.answer = answer
    def complete_json(self, system, user): return self.answer


def test_llm_value_needs_quote_in_text():
    d = doc("ocr_006")
    res = ExtractionResult(file="x", fields=extract_court_fields(d))
    fill_missing(d, res, FakeLLM({
        "дело_дата": {"value": "2033-02-14", "quote": "от 14.02.2033"},      # цитаты нет в тексте OCR
        "инн": {"value": "819611645856", "quote": "ИНН 819611645856"},       # цитата есть
    }))
    assert res.fields["дело_дата"].value is None
    assert res.fields["инн"].value == "819611645856"
    assert quote_in_text("инн  8196 11645856", "ИНН 819611645856")


def test_extract_json_from_noisy_answer():
    assert extract_json('Рассуждаю...\n```json\n{"a": {"value": 1}}\n```') == {"a": {"value": 1}}


# ---------- детектор маркеров (фразы синтетические, не из задания)
def test_legal_detector_exact_lemma_fuzzy():
    d = Document(path="x", pages=[Page(number=1, source=TextSource.OCR,
                 text="Заявитель просит признать долг\nнадлежащим образом оформленным и вынесенным.")])
    det = LegalDetector(["надлежащим образом оформленный", "долг признан", "иск отклонён полностью"], 88)
    got = {f.phrase: f.score for f in det.scan(d)}
    assert "надлежащим образом оформленный" in got      # форма слова -> по леммам
    assert "иск отклонён полностью" not in got


def test_legal_detector_ocr_noise():
    d = Document(path="x", pages=[Page(number=1, source=TextSource.OCR, text="решение не подлежит иcполнению")])  # латинская c
    assert LegalDetector(["не подлежит исполнению"], 90).scan(d)
