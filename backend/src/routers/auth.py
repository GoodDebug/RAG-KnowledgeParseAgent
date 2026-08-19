# -*- coding: utf-8 -*-
"""
认证路由（Spec-A）：POST /api/auth/register、POST /api/auth/login。

端点声明为同步 def（FastAPI 线程池执行，不阻塞事件循环）。
DB 异常 OperationalError → 503（统一 JSON，全局异常体系之外按业务处理）。
"""
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from core.security import create_access_token, hash_password, verify_password
from db import get_db
from db.models import User

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"^1\d{10}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class RegisterRequest(BaseModel):
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=100)
    password: str = Field(min_length=6, max_length=64)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _PHONE_RE.match(v):
            raise ValueError("手机号格式不正确")
        return v

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _EMAIL_RE.match(v):
            raise ValueError("邮箱格式不正确")
        return v

    @model_validator(mode="after")
    def _check_at_least_one(self) -> "RegisterRequest":
        if not self.phone and not self.email:
            raise ValueError("手机号或邮箱至少提供一个")
        return self


class LoginRequest(BaseModel):
    account: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=64)


class UserOut(BaseModel):
    id: int
    phone: str | None
    email: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _db_unavailable(exc: OperationalError) -> None:
    logger.error("数据库连接失败: %s", exc)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="数据库连接失败，请稍后重试",
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserOut,
    summary="注册（手机号或邮箱 + 密码）",
)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> User:
    try:
        # 只按实际提供的身份字段查重：body.email=None 时若用 User.email == None 会生成
        # `email IS NULL` 匹配到任意未填邮箱的用户（Spec-A 潜在 bug，Spec-C 暴露并修复）
        conds = []
        if body.phone:
            conds.append(User.phone == body.phone)
        if body.email:
            conds.append(User.email == body.email)
        existing = db.scalar(select(User).where(or_(*conds))) if conds else None
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="手机号或邮箱已存在",
            )
        user = User(
            phone=body.phone,
            email=body.email,
            password_hash=hash_password(body.password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("注册成功 | user_id=%s", user.id)
        return user
    except OperationalError as exc:
        _db_unavailable(exc)


@router.post("/login", response_model=dict, summary="登录（返回 JWT）")
def login(body: LoginRequest, db: Session = Depends(get_db)) -> dict:
    try:
        user = db.scalar(
            select(User).where(
                or_(User.phone == body.account, User.email == body.account)
            )
        )
        if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="账号或密码错误",
            )
        token = create_access_token(user.id)
        logger.info("登录成功 | user_id=%s", user.id)
        return {"access_token": token, "token_type": "bearer"}
    except OperationalError as exc:
        _db_unavailable(exc)
