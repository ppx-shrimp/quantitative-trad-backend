"""Alembic env.py — 集成 quant_system 的 SQLAlchemy engine 和 ORM models。

迁移脚本的数据库连接从 quant_system.core.config.settings.database_url 读取，
与应用共享同一个 DATABASE_URL 配置，不需要在 alembic.ini 中重复填写。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from quant_system.core.config import settings
from quant_system.db.database import Base
from quant_system.db.database import get_database_url

# 导入所有 ORM 模型，确保 Base.metadata 包含全部表定义
import quant_system.db.models  # noqa: F401

# Alembic Config 对象
config = context.config

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata —— Alembic autogenerate 会对比这个和数据库实际结构
target_metadata = Base.metadata


def get_url() -> str:
    """从应用配置读取数据库连接串，优先覆盖 alembic.ini 中的空值。"""
    url = get_database_url()
    # Alembic 对 SQLite URL 格式有要求，确保是三斜杠
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        return url
    return url


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。"""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直接连接数据库执行迁移。"""
    url = get_url()
    connectable_config = config.get_section(config.config_ini_section, {})
    connectable_config["sqlalchemy.url"] = url

    connectable = engine_from_config(
        connectable_config,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
