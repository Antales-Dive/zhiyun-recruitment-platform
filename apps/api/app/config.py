"""应用配置：强类型环境变量解析，生产环境缺少关键配置时启动失败。"""
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VALID_ENVIRONMENTS = {"development", "test", "production"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/zhiyun.db"
    upload_dir: Path = Path("./data/uploads")
    max_upload_bytes: int = 10 * 1024 * 1024
    default_org_id: str = "default"
    allowed_roles: frozenset[str] = frozenset({"ADMIN", "HR", "INTERVIEWER", "AUDITOR"})
    # 仅测试环境可显式启用请求头模拟身份；生产环境启用会导致启动失败
    allow_header_auth: bool = False
    session_ttl_seconds: int = 8 * 3600
    login_rate_limit_max: int = 5
    login_rate_limit_window_seconds: int = 900
    # RabbitMQ 可靠消息（TASK-003）
    rabbitmq_url: str = "amqp://guest:guest@127.0.0.1:5672/"
    rabbitmq_exchange: str = "zhiyun.events"
    rabbitmq_main_queue: str = "zhiyun.tasks"
    rabbitmq_retry_queue: str = "zhiyun.tasks.retry"
    rabbitmq_dlq: str = "zhiyun.tasks.dlq"
    outbox_batch_size: int = 50
    outbox_lease_seconds: int = 60
    consumer_lease_seconds: int = 120
    message_max_attempts: int = 5
    message_retry_delay_ms: int = 5000
    stream_event_retention: int = 10_000
    sse_heartbeat_seconds: int = 20
    # AI Provider（PD-003）：生产缺失时按 MODEL_NOT_CONFIGURED 处理
    model_base_url: str = ""
    model_api_key: str = ""
    model_chat_name: str = ""
    model_embedding_name: str = ""

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in VALID_ENVIRONMENTS:
            raise ValueError(f"APP_ENV 必须是 {'/'.join(sorted(VALID_ENVIRONMENTS))}，当前为 {value!r}")
        return normalized

    @field_validator("max_upload_bytes")
    @classmethod
    def validate_max_upload_bytes(cls, value: int) -> int:
        if value < 1024:
            raise ValueError("MAX_UPLOAD_BYTES 不能小于 1024")
        return value

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if self.app_env == "production" and self.database_url.startswith("sqlite"):
            raise ValueError("生产环境必须配置 MySQL DATABASE_URL，禁止使用 SQLite")
        if self.app_env == "production" and self.allow_header_auth:
            raise ValueError("生产环境禁止启用请求头模拟身份（ALLOW_HEADER_AUTH）")
        return self

    def ensure_upload_dir(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_upload_dir()
