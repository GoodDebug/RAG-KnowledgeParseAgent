# -*- coding: utf-8 -*-
"""
会话/消息路由（Spec-D）：POST /api/messages/{id}/feedback。

00 §6.2 指定 feedback 端点落点 sessions.py；`GET /api/sessions` 列表/详情留 Spec-E。
挂载前缀：`/api/messages`（main_fastapi.py include_router）。
校验顺序（spec §4.1）：401 → 404（消息不存在）→ 403（非本人）→ 400（feedback 非法 / 文字超长）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from core.deps import get_current_user
from db import get_db
from db.models import Message, Session as DBSession, User

router = APIRouter(tags=["sessions"])
logger = logging.getLogger(__name__)

MAX_FEEDBACK_TEXT = 500  # 可选文字反馈长度上限（常量，与 spec §4.1 一致）


class FeedbackRequest(BaseModel):
    feedback: str = Field(min_length=1, max_length=16)
    feedback_text: str | None = Field(default=None, max_length=MAX_FEEDBACK_TEXT)


def _db_unavailable(exc: OperationalError) -> None:
    """DB 连接失败 → 503（对齐 auth.py 惯例）。"""
    logger.error("数据库连接失败: %s", exc)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="数据库连接失败，请稍后重试",
    )


@router.post("/{msg_id}/feedback")
def submit_feedback(
    msg_id: int,
    body: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """对消息点赞/踩（可选文字），按 messages.session_id → sessions.user_id 校验归属。"""
    try:
        # 1. 消息存在性
        msg = db.query(Message).filter(Message.id == msg_id).first()
        if msg is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在"
            )

        # 2. 归属校验：消息所属会话必须属于当前用户
        sess = db.get(DBSession, msg.session_id)
        if sess is None or sess.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="无权操作该消息"
            )

        # 3. 取值校验（spec §4.1：404/403 先于 400）
        if body.feedback not in ("up", "down"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="feedback 非法，仅支持 up/down",
            )
        if body.feedback_text is not None and len(body.feedback_text) > MAX_FEEDBACK_TEXT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"feedback_text 过长，最多 {MAX_FEEDBACK_TEXT} 字",
            )

        # 4. 落库（重复提交覆盖旧值）
        msg.feedback = body.feedback
        msg.feedback_text = body.feedback_text or None
        db.commit()
        db.refresh(msg)
    except OperationalError as exc:
        _db_unavailable(exc)

    logger.info("feedback | msg_id=%s feedback=%s", msg_id, body.feedback)
    return {"id": msg.id, "feedback": msg.feedback, "feedback_text": msg.feedback_text}


# ======================= 会话列表/详情（Spec-E 加分项与前端整合） =======================

sessions_list_router = APIRouter(tags=["sessions"])


@sessions_list_router.get("/sessions")
def list_sessions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list:
    """当前用户会话列表（Spec-E §4.1；按 created_at desc, id desc）。"""
    try:
        rows = (
            db.query(DBSession)
            .filter(DBSession.user_id == user.id)
            .order_by(DBSession.created_at.desc(), DBSession.id.desc())
            .all()
        )
        return [
            {"id": r.id, "title": r.title, "created_at": r.created_at, "key": r.key}
            for r in rows
        ]
    except OperationalError as exc:
        _db_unavailable(exc)


@sessions_list_router.get("/sessions/{session_id}/messages")
def get_session_messages(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list:
    """会话完整历史（Spec-E §4.1；归属校验 404/403；复用 routers.chat.read_messages_for_session 单源）。"""
    try:
        sess = db.get(DBSession, session_id)
        if sess is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if sess.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
    except OperationalError as exc:
        _db_unavailable(exc)
    from routers.chat import read_messages_for_session  # 局部 import，规避路由包循环导入
    return read_messages_for_session(session_id)
