"""简历解析器：TXT / DOCX / 文本 PDF 专用解析；图片与扫描 PDF 走 OCR 端口。"""
import re
from pathlib import Path

from pypdf import PdfReader

from app.contracts.resume import ParsedBlock, ParseOutcome
from app.infrastructure.ocr.port import OcrPort

MIN_TEXT_PAGE_CHARS = 20
MIN_TOTAL_CHARS = 40

_SECTION_RE = re.compile(r"^(教育|工作|项目|技能|自我评价|个人总结|证书|语言)(背景|经历|经验|介绍|情况)?[:：]?$")

_ENCODINGS = ("utf-8", "gb18030", "latin-1")


class ResumeParseError(Exception):
    pass


class OcrRequiredError(ResumeParseError):
    pass


def parse_resume(path: Path, file_type: str, ocr: OcrPort | None = None) -> ParseOutcome:
    """按文件类型分发到专用解析器；图片/无文本 PDF 标记 NEEDS_OCR。"""
    if file_type == "txt":
        return _parse_txt(path)
    if file_type == "docx":
        return _parse_docx(path)
    if file_type == "pdf":
        outcome = _parse_pdf_text(path)
        if outcome.needs_ocr:
            if ocr is None:
                return outcome
            return _parse_with_ocr(path, ocr)
        return outcome
    if file_type in {"png", "jpeg"}:
        if ocr is None:
            return ParseOutcome(parser_type="needs_ocr", needs_ocr=True)
        return _parse_with_ocr(path, ocr)
    raise ResumeParseError(f"不支持的解析类型：{file_type}")


def _decode_text(data: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _parse_txt(path: Path) -> ParseOutcome:
    data = path.read_bytes()
    text = _decode_text(data)
    blocks = _split_into_sections(text)
    if len(text.strip()) < MIN_TOTAL_CHARS:
        return ParseOutcome(parser_type="txt", blocks=[], error="文本过短，质量不足")
    return ParseOutcome(parser_type="txt", blocks=blocks)


def _parse_docx(path: Path) -> ParseOutcome:
    from docx import Document

    document = Document(str(path))
    lines: list[str] = []
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            lines.append(paragraph.text.strip())
    text = "\n".join(lines)
    if len(text.strip()) < MIN_TOTAL_CHARS:
        return ParseOutcome(parser_type="docx", blocks=[], error="DOCX 文本过短，质量不足")
    return ParseOutcome(parser_type="docx", blocks=_split_into_sections(text))


def _parse_pdf_text(path: Path) -> ParseOutcome:
    reader = PdfReader(str(path))
    blocks: list[ParsedBlock] = []
    total_chars = 0
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        total_chars += len(text.strip())
        if len(text.strip()) < MIN_TEXT_PAGE_CHARS:
            continue
        blocks.extend(_split_into_sections(text, page_no=page_no))
    if total_chars < MIN_TOTAL_CHARS:
        return ParseOutcome(parser_type="pdf_text", blocks=[], needs_ocr=True, error="PDF 无文本层，需要 OCR")
    return ParseOutcome(parser_type="pdf_text", blocks=blocks)


def _parse_with_ocr(path: Path, ocr: OcrPort) -> ParseOutcome:
    text = ocr.recognize(str(path))
    if len(text.strip()) < MIN_TOTAL_CHARS:
        return ParseOutcome(parser_type="ocr", blocks=[], error="OCR 结果过短，置信度不足")
    return ParseOutcome(parser_type="ocr", blocks=_split_into_sections(text))


def _split_into_sections(text: str, page_no: int | None = None) -> list[ParsedBlock]:
    """按空行与常见章节标题切分；OCR 文本按低置信度数据处理。"""
    blocks: list[ParsedBlock] = []
    current_section: str | None = None
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if buffer:
                blocks.append(ParsedBlock(text="\n".join(buffer), section=current_section, page_no=page_no))
                buffer = []
            continue
        if _SECTION_RE.match(line):
            if buffer:
                blocks.append(ParsedBlock(text="\n".join(buffer), section=current_section, page_no=page_no))
                buffer = []
            current_section = line
            continue
        buffer.append(line)
    if buffer:
        blocks.append(ParsedBlock(text="\n".join(buffer), section=current_section, page_no=page_no))
    if not blocks and text.strip():
        blocks.append(ParsedBlock(text=text.strip()[:4000], section=None, page_no=page_no))
    return blocks
