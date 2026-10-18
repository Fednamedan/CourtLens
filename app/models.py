"""Контракты данных пайплайна."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Столбцы образца ocr_template.csv (имена сохранены как в образце, включая «отчетство»)
OCR_COLUMNS = [
    "тип документа", "Файл", "улица", "дом", "кв", "фамилия", "имя", "отчетство",
    "фио", "дата рождения", "паспорт", "снилс", "инн", "дело_номер", "дело_дата",
    "период_дз_начало", "период_дз_оконч", "дз_осн", "дз_пени", "дз_пошлина",
    "соответчики_кол-во", "соответчики_фио",
]

# Поля, которые обязаны оставаться строками (ведущие нули, пробелы в серии паспорта)
STRING_FIELDS = {"паспорт", "снилс", "инн", "дело_номер", "дом", "кв"}
DATE_FIELDS = {"дата рождения", "дело_дата", "период_дз_начало", "период_дз_оконч"}
MONEY_FIELDS = {"дз_осн", "дз_пени", "дз_пошлина"}


# Столбцы образца xml_template.csv (постановления ФССП)
XML_COLUMNS = [
    "FileName", "DocType", "DocType2", "DebtorType", "DocDate", "IdDebtText",
    "IdDocNo", "IdDocDate", "IdDeloNo", "IdDeloDate", "IdDebtSum", "IpNo", "IdType",
    "DbtrAdr", "street", "dom", "kv", "DbtrName", "date_start", "date_end",
    "rub_deb", "rub_peni", "rub_poshlina", "rub_post",
]
XML_MONEY_FIELDS = {"IdDebtSum", "rub_deb", "rub_peni", "rub_poshlina", "rub_post"}
XML_DATE_FIELDS = {"DocDate", "IdDocDate", "IdDeloDate", "date_start", "date_end"}


class DocType(str, Enum):
    ORDER = "приказ"
    ORDER_E = "приказ эл"
    WRIT = "ИЛ"
    WRIT_E = "ИЛ эл"
    FSSP = "постановление ФССП"
    UNKNOWN = "unknown"


class TextSource(str, Enum):
    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    XML = "xml"


class FieldStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    LOW_CONFIDENCE = "low_confidence"
    INVALID = "invalid"


class Page(BaseModel):
    number: int
    text: str = ""
    source: TextSource
    ocr_confidence: float | None = None


class Document(BaseModel):
    path: str
    pages: list[Page] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages)


class FieldValue(BaseModel):
    value: Any = None
    status: FieldStatus = FieldStatus.MISSING
    confidence: float = 0.0
    quote: str | None = None      # цитата-источник из текста
    page: int | None = None
    note: str | None = None


class LegalFlag(BaseModel):
    phrase: str
    page: int | None = None
    context: str = ""
    score: float = 1.0


class ExtractionResult(BaseModel):
    file: str
    doc_type: DocType = DocType.UNKNOWN
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    legal_flags: list[LegalFlag] = Field(default_factory=list)
    debtor_rows: list[dict[str, FieldValue]] = Field(default_factory=list)  # строки по каждому должнику (бланки ИЛ)
    errors: list[str] = Field(default_factory=list)

    @property
    def needs_lawyer(self) -> bool:
        return bool(self.legal_flags)
