from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from quant_system.core.config import settings


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    if settings.database_url:
        return settings.database_url
    sqlite_path = Path(settings.database_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{sqlite_path.as_posix()}"


def create_database_engine() -> Engine:
    database_url = get_database_url()
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine_kwargs = {"echo": False, "connect_args": connect_args}
    if not database_url.startswith("sqlite"):
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_recycle"] = 3600
        connect_args["connect_timeout"] = 5
    return create_engine(database_url, **engine_kwargs)


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@lru_cache(maxsize=1)
def init_sqlalchemy_tables() -> None:
    import quant_system.db.models  # noqa: F401

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
        return

    # MySQL 等生产型数据库的结构变更由 Alembic 管理。
    # 这里保持幂等 no-op，避免每个 service 初始化时反复触发 schema 检查。
    return
