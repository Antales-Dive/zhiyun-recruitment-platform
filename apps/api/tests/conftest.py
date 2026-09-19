"""测试环境基线：在收集任何测试模块前固定环境变量并重建测试数据库。

app.config 的 Settings 在首次导入时实例化并冻结环境变量，因此所有测试
必须共享同一套环境；这里在 pytest 收集阶段就位，保证任何导入顺序下
DATABASE_URL / UPLOAD_DIR / 认证模式都一致。
"""
import os
from pathlib import Path

TEST_DB = Path("./data/test.db")
TEST_UPLOADS = Path("./data/test-uploads")

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["UPLOAD_DIR"] = TEST_UPLOADS.as_posix()
os.environ["ALLOW_HEADER_AUTH"] = "true"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["SESSION_TTL_SECONDS"] = "7200"
os.environ["LOGIN_RATE_LIMIT_MAX"] = "3"
os.environ["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "900"
os.environ["SSE_HEARTBEAT_SECONDS"] = "1"
os.environ["MESSAGE_RETRY_DELAY_MS"] = "500"

# 每次测试运行都从空 schema 开始，避免陈旧表结构/数据干扰
if TEST_DB.exists():
    TEST_DB.unlink()
