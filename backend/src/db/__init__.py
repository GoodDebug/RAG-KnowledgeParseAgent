# -*- coding: utf-8 -*-
"""
数据库访问层（Spec-A：基础设施与认证）。

模块级单例：engine / SessionLocal / Base / get_db。
设计说明（DB 为何不挂 app_state.state）：
  ① FastAPI + SQLAlchemy 官方惯例即「模块级 engine + get_db 依赖」——engine 是懒连接连接池、
     自管理，无需 lifespan 创建/销毁；
  ② app_state 现有成员（MCP/Milvus/LLM 客户端）均为 lifespan 创建的长驻服务资源，语义不同；
  ③ get_db 与 state 解耦 → 测试可 dependency_overrides[get_db] 注入、可用 MYSQL_DB 切测试库；
  ④ app_state.py 在禁止修改范围内，不挂它零改动。
详见 docs/spec/01-子任务-A-基础设施与认证.md §4.5。
"""
import logging
import os
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

# main_fastapi 的 load_dotenv 位于 import 之后，这里必须先自行加载 .env 再读 MYSQL_*。
# 注意：本文件在 db/ 子包内，.env 在其父目录 backend/src/ 下，故用 parent.parent。
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _build_db_url() -> str:
    user: str = os.getenv("MYSQL_USER", "ai_customer")
    pwd: str = os.getenv("MYSQL_PASSWORD", "")
    host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    port: str = os.getenv("MYSQL_PORT", "3306")
    db: str = os.getenv("MYSQL_DB", "ai_customer_service")
    return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}?charset=utf8mb4"


engine = create_engine(
    _build_db_url(),
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=5,
    max_overflow=10,
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类，所有 ORM 模型继承。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个短生命周期 session，finally 关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session_factory():
    """返回会话工厂（测试用）。engine 在模块导入时已按 MYSQL_DB 绑定。"""
    return SessionLocal
