"""可观测性：结构化日志与敏感信息脱敏（NFR-002）。"""
import logging
import re

_SENSITIVE_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(Bearer|Authorization)\s+[\w.-]+", re.IGNORECASE),
]


def redact_text(text: str) -> str:
    """默认脱敏：邮箱、手机号与令牌模式替换为占位。"""
    masked = text
    for pattern in _SENSITIVE_PATTERNS:
        masked = pattern.sub("[REDACTED]", masked)
    return masked


class RedactingFormatter(logging.Formatter):
    """统一日志格式：时间/级别/服务/trace/错误码；PII 默认脱敏。"""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s service=zhiyun trace=%(trace_id)s "
            "task=%(task_id)s event=%(event_id)s error_code=%(error_code)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    def format(self, record: logging.LogRecord) -> str:
        record.trace_id = getattr(record, "trace_id", "-")
        record.task_id = getattr(record, "task_id", "-")
        record.event_id = getattr(record, "event_id", "-")
        record.error_code = getattr(record, "error_code", "-")
        record.msg = redact_text(str(record.getMessage()))
        record.args = ()
        return super().format(record)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if any(isinstance(handler.formatter, RedactingFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter())
    root.handlers = [handler]
