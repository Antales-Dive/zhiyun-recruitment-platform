import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 无论从仓库根目录还是 apps/api 执行 alembic，都保证 app 包可导入
APPS_API = Path(__file__).resolve().parents[1]
if str(APPS_API) not in sys.path:
    sys.path.insert(0, str(APPS_API))

from app.config import settings  # noqa: E402
from app.infrastructure import models  # noqa: F401, E402
from app.infrastructure.db import Base  # noqa: E402

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移；SQLite 使用 batch 模式支持 ALTER。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
