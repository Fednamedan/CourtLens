from pathlib import Path

from app.xmlfssp.parser import parse_dbtr_address, parse_debt_text, parse_xml

FIX = Path(__file__).parent / "fixtures" / "fssp_005.xml"


def test_tag_fields_and_type():
    r = parse_xml(str(FIX))
    f = {k: v.value for k, v in r.fields.items()}
    assert f["IpNo"] == "825393/33/55003-ИП"
    assert f["DocType2"] == "Постановление об окончании ИП"
    assert f["DbtrName"] == "Регистрова Анна Арсеньевна"


def test_address_formatting():
    a = parse_dbtr_address("643,644117,55,,Тест,,Примерный,38 Б,71/72", "ул. Примерный пер., д. 38Б")
    assert a == {"street": "г Тест, пер. Примерный", "dom": "38/Б", "kv": "71/72"}
    a = parse_dbtr_address("643,644023,55,,Тест,,5 Образцовая,50,138")
    assert a["street"] == "г Тест, ул. Образцовая 5-я"
    a = parse_dbtr_address("644043, Россия, Пробная обл., , г. Тест, , ул. Учебный Путь, д. 26 А, , кв. 65")
    assert (a["street"], a["dom"], a["kv"]) == ("г Тест, ул. Учебный Путь", "26/А", "65")


def test_debt_text_amounts_and_period():
    t = ("за период с 03.01.2031 по 02.12.2032 в размере 88300,94 руб., пени в размере 41622,76 руб. "
         "за период с 03.01.2031 по 02.12.2032, расходы по уплате государственной пошлины в размере 4000,00 руб., "
         "почтовые расходы в размере 364,80 руб.")
    t = "задолженность " + t
    d = parse_debt_text(t)
    assert d["date_start"] == "2031-01-03" and d["date_end"] == "2032-12-02"
    assert (d["rub_deb"], d["rub_peni"], d["rub_poshlina"], d["rub_post"]) == ("88300.94", "41622.76", "4000.0", "364.8")


def test_placeholder_amounts_not_borrowed():
    d = parse_debt_text("задолженность ... неустойку в размере _, пени в размере _, расходы по уплате государственной пошлины в размере 0 (ноль рублей)")
    assert d["rub_peni"] == "" and d["rub_poshlina"] == ""
