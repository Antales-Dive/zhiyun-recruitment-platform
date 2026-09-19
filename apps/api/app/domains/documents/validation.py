"""文件校验：扩展名、MIME、magic bytes、大小、压缩炸弹与安全文件名。"""
import io
import re
import zipfile
from dataclasses import dataclass

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg"}

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._\- ]")


class FileValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidationResult:
    suffix: str
    detected_type: str  # pdf | docx | txt | png | jpeg | unknown


def detect_type(content: bytes, suffix: str) -> str:
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        return "docx"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if suffix == ".txt" or suffix == ".md":
        return "txt"
    return "unknown"


def validate_file(
    *, filename: str, content: bytes, max_bytes: int, content_type: str | None = None
) -> ValidationResult:
    if not content:
        raise FileValidationError("EMPTY_FILE", "文件为空")
    if len(content) > max_bytes:
        raise FileValidationError("FILE_TOO_LARGE", "超过最大文件大小限制")

    suffix = _safe_suffix(filename)
    if suffix not in SUPPORTED_EXTENSIONS:
        raise FileValidationError("FILE_TYPE_UNSUPPORTED", f"不支持的文件类型：{suffix or '未知'}")

    detected = detect_type(content, suffix)
    if detected == "unknown":
        raise FileValidationError("INVALID_FILE_SIGNATURE", "文件签名与扩展名不匹配或签名无法识别")
    if detected == "docx" and suffix != ".docx":
        raise FileValidationError("INVALID_FILE_SIGNATURE", "文件内容为 DOCX 但扩展名不匹配")
    if detected in {"png", "jpeg"} and suffix not in {".png", ".jpg", ".jpeg"}:
        raise FileValidationError("INVALID_FILE_SIGNATURE", "图片签名与扩展名不匹配")
    if content_type and content_type != "application/octet-stream":
        allowed_mimes = {
            "pdf": {"application/pdf"},
            "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
            "txt": {"text/plain", "text/markdown"},
            "png": {"image/png"},
            "jpeg": {"image/jpeg"},
        }
        if content_type not in allowed_mimes.get(detected, set()):
            raise FileValidationError("INVALID_FILE_SIGNATURE", "MIME 类型与文件内容不匹配")

    _guard_zip_bomb(content, suffix)
    return ValidationResult(suffix=suffix, detected_type=detected)


def safe_filename(filename: str, fallback: str = "resume") -> str:
    """清洗文件名：去除路径与危险字符；存储键另行生成。"""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _SAFE_NAME_RE.sub("_", base)
    if not base or base in {".", ".."}:
        return fallback
    return base[:150]


def _safe_suffix(filename: str) -> str:
    base = safe_filename(filename, fallback="resume.txt")
    dot = base.rfind(".")
    return base[dot:].casefold() if dot > 0 else ""


def _guard_zip_bomb(content: bytes, suffix: str) -> None:
    """DOCX/PDF 解压后体积超过 50 倍或 200MB 视为压缩炸弹拒绝。"""
    if suffix != ".docx":
        return
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        raise FileValidationError("INVALID_FILE_SIGNATURE", "DOCX 压缩包损坏") from None
    if total > max(200 * 1024 * 1024, len(content) * 50):
        raise FileValidationError("FILE_TOO_LARGE", "疑似压缩炸弹：解压后体积异常")
