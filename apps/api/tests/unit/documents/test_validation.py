"""文件校验单元测试：签名、类型、大小、压缩炸弹与安全文件名。"""
import io
import zipfile

import pytest
from app.domains.documents.validation import (
    FileValidationError,
    detect_type,
    safe_filename,
    validate_file,
)

MAX_BYTES = 1024 * 1024


def make_docx_bytes(size: int = 1) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "x" * size)
    return buffer.getvalue()


class TestDetectType:
    def test_pdf_signature(self):
        assert detect_type(b"%PDF-1.7", ".pdf") == "pdf"

    def test_docx_signature(self):
        assert detect_type(b"PK\x03\x04rest", ".docx") == "docx"

    def test_png_and_jpeg_signatures(self):
        assert detect_type(b"\x89PNG\r\n\x1a\nrest", ".png") == "png"
        assert detect_type(b"\xff\xd8\xff\xe0rest", ".jpg") == "jpeg"

    def test_unknown_signature(self):
        assert detect_type(b"plain bytes", ".txt") == "txt"
        assert detect_type(b"plain bytes", ".pdf") == "unknown"


class TestValidateFile:
    def test_accepts_txt(self):
        result = validate_file(filename="resume.txt", content="张三\nPython".encode(), max_bytes=MAX_BYTES)
        assert result.suffix == ".txt"

    def test_rejects_unsupported_extension(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file(filename="resume.exe", content=b"MZ...", max_bytes=MAX_BYTES)
        assert exc.value.code == "FILE_TYPE_UNSUPPORTED"

    def test_rejects_signature_mismatch(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file(filename="fake.pdf", content=b"not a pdf", max_bytes=MAX_BYTES)
        assert exc.value.code == "INVALID_FILE_SIGNATURE"

    def test_rejects_empty_and_oversized_files(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file(filename="a.txt", content=b"", max_bytes=MAX_BYTES)
        assert exc.value.code == "EMPTY_FILE"

        with pytest.raises(FileValidationError) as exc:
            validate_file(filename="a.txt", content=b"x" * (MAX_BYTES + 1), max_bytes=MAX_BYTES)
        assert exc.value.code == "FILE_TOO_LARGE"

    def test_rejects_zip_bomb(self):
        bomb = make_docx_bytes(size=300 * 1024 * 1024)
        with pytest.raises(FileValidationError) as exc:
            validate_file(filename="bomb.docx", content=bomb, max_bytes=MAX_BYTES)
        assert exc.value.code == "FILE_TOO_LARGE"


class TestSafeFilename:
    def test_strips_path_components(self):
        assert safe_filename("..\\..\\etc/passwd.txt") == "passwd.txt"

    def test_sanitizes_dangerous_characters(self):
        assert safe_filename('a<b>c?.txt') == "a_b_c_.txt"

    def test_falls_back_for_empty(self):
        assert safe_filename("") == "resume"
        assert safe_filename("..") == "resume"

    def test_truncates_long_names(self):
        assert len(safe_filename("x" * 300 + ".txt")) <= 150
