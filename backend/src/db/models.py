# -*- coding: utf-8 -*-
"""
4 表 ORM 模型（Spec-A），与 scripts/init_db.sql DDL 严格一致。

命名注意：本模块的 Session 模型与 sqlalchemy.orm.Session 同名——外部引用统一用
`db.models.Session` 全名，或对 sqlalchemy.orm.Session 使用别名导入。
"""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uk_sessions_user_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(backref="sessions")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(Enum("user", "assistant", "system"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_refs: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)  # 引用 JSON；MEDIUMTEXT 防数百引用溢出
    feedback: Mapped[str | None] = mapped_column(Enum("up", "down"), nullable=True)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    book_name: Mapped[str] = mapped_column(String(200), nullable=False)  # 书目标题分组键（Spec-C）
    book_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 组 book_id 稳定锚点（顶层计划外：书分组与单文件删除）
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("processing", "ready", "failed"), nullable=False
    )
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    milvus_collection: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
