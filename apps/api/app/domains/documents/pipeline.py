"""简历解析流水线：校验 → 解析/OCR → 结构化档案 → 质量门。"""
import hashlib
import re

from app.contracts.resume import ParsedProfile, QualityReport

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9}|(\+?\d[\d -]{7,}\d))(?!\d)")
_NAME_HINT_RE = re.compile(r"^(姓\s*名|name)\s*[:：]\s*([^\n]+)", re.IGNORECASE)
_SKILL_HINT_RE = re.compile(r"技能|skills", re.IGNORECASE)

REQUIRED_FIELDS = ("name", "email", "phone")
QUALITY_PARSED_THRESHOLD = 0.6


def extract_profile(blocks) -> ParsedProfile:
    """规则化结构化抽取：只记录明确出现的内容，不补造任何字段（FR-006）。"""
    full_text = "\n".join(block.text for block in blocks)
    profile = ParsedProfile()

    email_match = _EMAIL_RE.search(full_text)
    if email_match:
        profile.email = email_match.group(0)

    phone_match = _PHONE_RE.search(full_text)
    if phone_match:
        profile.phone = phone_match.group(0).strip()

    for block in blocks:
        name_match = _NAME_HINT_RE.search(block.text)
        if name_match:
            profile.name = name_match.group(2).strip()[:200]
            break
    # 不补造姓名：仅接受明确的“姓名/name”标记，不做首行猜测

    skills: list[str] = []
    for block in blocks:
        if block.section and _SKILL_HINT_RE.search(block.section):
            for token in re.split(r"[,，、;；\s]+", block.text):
                token = token.strip()
                if 2 <= len(token) <= 40 and token not in skills:
                    skills.append(token)
    profile.skills = skills[:50]
    return profile


def assess_quality(profile: ParsedProfile, blocks, parser_type: str) -> QualityReport:
    """质量门：关键字段缺失或解析失败时置信度不足，进入人工复核。"""
    missing = [field for field in REQUIRED_FIELDS if getattr(profile, field) is None]
    warnings: list[str] = []
    if parser_type == "ocr":
        warnings.append("OCR 文本按低置信度数据处理")
    if len(blocks) == 0:
        warnings.append("未提取到有效内容块")
    if profile.name and profile.email is None and profile.phone is None:
        warnings.append("仅识别到姓名，联系方式缺失")

    confidence = max(
        0.0,
        1.0
        - 0.3 * len(missing)
        - (0.2 if parser_type == "ocr" else 0.0)
        - (0.3 if not blocks else 0.0),
    )
    return QualityReport(confidence=confidence, missing_fields=missing, warnings=warnings)


def block_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
