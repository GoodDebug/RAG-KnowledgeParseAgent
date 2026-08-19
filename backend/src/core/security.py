# -*- coding: utf-8 -*-
"""
JWT + bcrypt（Spec-A）。

- hash_password / verify_password：passlib + bcrypt（bcrypt==4.0.1，兼容 <4.1）
- create_access_token / decode_token：python-jose HS256
- JWT_SECRET 惰性读取（函数调用时），缺失或 <32 字符则抛 ValueError（拒绝弱配置）
"""
import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _jwt_secret() -> str:
    secret: str = os.getenv("JWT_SECRET", "")
    if not secret or len(secret) < 32:
        raise ValueError("JWT_SECRET 缺失或长度 <32，请配置强随机密钥")
    return secret


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(user_id: int) -> str:
    expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> int:
    """返回 user_id；失败抛 JWTError（含 ExpiredSignatureError 子类）。"""
    payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    return int(payload["sub"])
