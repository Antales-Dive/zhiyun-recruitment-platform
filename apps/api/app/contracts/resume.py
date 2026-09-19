"""简历域契约：解析块与结构化档案 Schema。

档案字段全部可空——绝不补造缺失的邮箱、经历或学历（FR-006）。
"""
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

PARSER_VERSION = "resume-parser-1"


@dataclass(frozen=True)
class ParsedBlock:
    text: str
    section: str | None = None
    page_no: int | None = None


@dataclass(frozen=True)
class ParseOutcome:
    parser_type: str  # txt | docx | pdf_text | ocr | needs_ocr
    blocks: list[ParsedBlock] = field(default_factory=list)
    needs_ocr: bool = False
    error: str | None = None


class EducationItem(BaseModel):
    school: str | None = None
    degree: str | None = None
    major: str | None = None
    period: str | None = None


class ExperienceItem(BaseModel):
    company: str | None = None
    title: str | None = None
    period: str | None = None
    description: str | None = None


class ParsedProfile(BaseModel):
    schema_version: int = 1
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    summary: str | None = None
    education: list[EducationItem] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class QualityReport(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
