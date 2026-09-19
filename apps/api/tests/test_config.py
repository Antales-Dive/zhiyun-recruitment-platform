"""TASK-001 配置基线测试：强类型 Settings 的解析与生产校验。"""
import pytest
from app.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "UPLOAD_DIR",
        "MAX_UPLOAD_BYTES",
        "LOG_LEVEL",
        "DEFAULT_ORG_ID",
        "ALLOW_HEADER_AUTH",
        "SESSION_TTL_SECONDS",
        "LOGIN_RATE_LIMIT_MAX",
        "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


class TestSettings:
    def test_development_defaults(self):
        settings = Settings(_env_file=None, app_env="development")

        assert settings.app_env == "development"
        assert settings.log_level == "INFO"
        assert settings.database_url == "sqlite:///./data/zhiyun.db"
        assert settings.max_upload_bytes == 10 * 1024 * 1024
        assert settings.default_org_id == "default"
        assert "HR" in settings.allowed_roles
        assert "ADMIN" in settings.allowed_roles

    def test_parses_values_from_environment(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "2097152")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("DEFAULT_ORG_ID", "org-x")

        settings = Settings(_env_file=None)

        assert settings.app_env == "test"
        assert settings.max_upload_bytes == 2 * 1024 * 1024
        assert settings.log_level == "DEBUG"
        assert settings.default_org_id == "org-x"

    def test_rejects_unknown_app_env(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, app_env="staging")

    def test_production_refuses_sqlite_database(self):
        with pytest.raises(ValueError, match="禁止使用 SQLite"):
            Settings(_env_file=None, app_env="production", database_url="sqlite:///./data/zhiyun.db")

    def test_production_refuses_header_auth_mode(self):
        with pytest.raises(ValueError, match="禁止启用请求头模拟身份"):
            Settings(
                _env_file=None,
                app_env="production",
                database_url="mysql+pymysql://zhiyun:secret@mysql:3306/zhiyun",
                allow_header_auth=True,
            )

    def test_production_accepts_mysql_database(self):
        settings = Settings(
            _env_file=None,
            app_env="production",
            database_url="mysql+pymysql://zhiyun:secret@mysql:3306/zhiyun",
        )

        assert settings.database_url.startswith("mysql+pymysql")

    def test_rejects_too_small_upload_limit(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, max_upload_bytes=512)

    def test_ensure_upload_dir_creates_directory(self, tmp_path):
        target = tmp_path / "uploads"
        settings = Settings(_env_file=None, app_env="development", upload_dir=target)

        settings.ensure_upload_dir()

        assert target.is_dir()
