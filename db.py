"""
数据访问层：SQLAlchemy 引擎 / Session
生产环境用 MySQL；开发用 SQLite。schema 见 db_schema.sql。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import DB_URL

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：请求级 Session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
