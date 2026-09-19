"""文件存储：存储键与原始文件名分离，杜绝路径拼接注入。"""
from pathlib import Path
from uuid import uuid4

from app.config import settings


class FileStore:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or settings.upload_dir

    def store(self, *, category: str, suffix: str, content: bytes) -> str:
        """写入文件并返回 storage_key；目录按类别隔离，文件名用随机 UUID。"""
        safe_suffix = _sanitize_suffix(suffix)
        key = f"{category}/{uuid4().hex}{safe_suffix}"
        target = self.base_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return key

    def path_for(self, storage_key: str) -> Path:
        """按 storage_key 解析本地路径；拒绝越界路径。"""
        relative = Path(storage_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"非法存储键：{storage_key}")
        target = self.base_dir / relative
        if not target.is_file():
            raise FileNotFoundError(f"存储文件不存在：{storage_key}")
        return target


def _sanitize_suffix(suffix: str) -> str:
    suffix = suffix.casefold().strip()
    allowed = {".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".md"}
    return suffix if suffix in allowed else ".bin"
