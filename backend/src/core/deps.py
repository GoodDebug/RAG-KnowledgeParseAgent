# -*- coding: utf-8 -*-
"""
认证依赖（Spec-A）：get_current_user。

统一 401（缺头 / 前缀错 / 解码失败 / 过期 / 用户不存在），防枚举。
"""
import logging

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError
from sqlalchemy.orm import Session

from core.security import decode_token
from db import get_db
from db.models import User

logger = logging.getLogger(__name__)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未认证或认证已过期",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise _CREDENTIALS_EXC
    token = authorization[len("Bearer "):].strip()
    try:
        user_id = decode_token(token)
    except JWTError:
        logger.info("JWT 校验失败")
        raise _CREDENTIALS_EXC
    user = db.get(User, user_id)
    if user is None:
        raise _CREDENTIALS_EXC
    return user
