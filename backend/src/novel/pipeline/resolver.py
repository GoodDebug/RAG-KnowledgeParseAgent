# -*- coding: utf-8 -*-
"""
跨章实体/事件解析 + 跨章命名全量名单（子任务 06，C3/C4/C5）。

职责：把"按名引用"的章内抽取结果解析为主键，打通跨章实体连续性——
  1. `resolve_entity_name` / `register_and_resolve` / `resolve_entity_names`：
     实体名 → entity_id（命中复用 / 未命中注册新建，复用 03 `register_entity` 基元，重复率 0）；
  2. `resolve_event_title` / `resolve_event_titles`：
     事件标题 → event_id（同书已入库行复用 / 未命中注册新行，stage 跨章复用 parent_event_id）；
  3. `build_hint_entities`：查 `entity` 表全部规范名 → 跨章名单（003 P1-1 的 hint_entities 来源）。

位置：resolver 只做"基元 + 契约"，图内 `persist_chapter` 11 表入库接线在 07 消费本契约；
各方法接受调用方传入的 `db`（短生命周期 session，事务边界由调用方管理）。
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from novel.persistence.upsert import register_entity
from UTILS.snowflake import snowflake

logger = logging.getLogger("novel.resolver")


# ---------- 实体名 → entity_id ----------

def resolve_entity_name(db, book_id: str, name: str) -> str | None:
    """按实体名查同书 entity_alias → entity_id（已入库行；无则 None）。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param name: 实体名（规范名或别名均可命中）
    :return: entity_id 或 None
    """
    row = db.execute(
        text("SELECT entity_id FROM entity_alias WHERE book_id = :b AND alias_name = :a"),
        {"b": book_id, "a": name},
    ).scalar()
    return row


def register_and_resolve(db, book_id: str, entity: dict, *, commit: bool = True) -> str:
    """事务内注册/解析：复用 03 `register_entity`（命中复用 / 未命中新建 + 写别名）→ 返回 entity_id。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param entity: 实体 dict（name / aliases[] / type / description）
    :param commit: 是否立即 commit（07 persist 批量入库时传 False，统一提交保单事务）
    :return: entity_id（复用或新建，ent_{snowflake}）
    """
    return register_entity(db, book_id, entity, commit=commit)


def resolve_entity_names(db, book_id: str, entities: list[dict], *, commit: bool = True) -> dict[str, str]:
    """批处理：对每条实体 register_and_resolve，返回 {entity_name: entity_id}。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param entities: 章内归并后的实体列表
    :param commit: 是否逐条立即 commit（07 persist 传 False 统一提交）
    :return: {实体名: entity_id}（供 07 写 FK 列）
    """
    out: dict[str, str] = {}
    for e in entities:
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        out[name] = register_and_resolve(db, book_id, e, commit=commit)
    return out


# ---------- 事件标题 → event_id ----------

def resolve_event_title(db, book_id: str, title: str) -> str | None:
    """按同书 timeline_event.event_title 查已入库 event_id（无则 None）。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param title: 事件/阶段标题
    :return: event_id 或 None
    """
    row = db.execute(
        text("SELECT event_id FROM timeline_event WHERE book_id = :b AND event_title = :t"),
        {"b": book_id, "t": title},
    ).scalar()
    return row


def resolve_event_titles(db, book_id: str, events: list[dict]) -> dict[str, str]:
    """批处理：已入库标题 → 复用 event_id；未命中 → 注册 ev_{snowflake}（本事务新行）。

    stage 首次出现即注册行，后续章按 event_title 复用（`parent_event_id` 跨章连续性的关键）。
    新行仅写 event_id/event_level/event_title/book_id（其余字段由 07 upsert 全量覆盖）。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param events: 章内 timeline 事件列表（含 stage 与 event）
    :return: {事件标题: event_id}
    """
    out: dict[str, str] = {}
    for ev in events:
        title = str(ev.get("event_title", "")).strip()
        if not title:
            continue
        existing = out.get(title) or resolve_event_title(db, book_id, title)
        if existing:
            out[title] = existing
            continue
        # 未命中 → 注册新行（ev_{snowflake}；本事务内后续同一标题复用 out 缓存）
        event_id = f"ev_{snowflake.generate()}"
        db.execute(
            text(
                "INSERT INTO timeline_event (event_id, event_level, event_title, book_id, "
                "global_sort, start_chapter, end_chapter) "
                "VALUES (:eid, :level, :title, :bid, 0, 0, 0)"
            ),
            {
                "eid": event_id,
                "level": ev.get("event_level", "event"),
                "title": title,
                "bid": book_id,
            },
        )
        out[title] = event_id
        logger.info("注册新事件行 | book=%s title=%s event_id=%s", book_id, title, event_id)
    return out


# ---------- 跨章命名全量名单（003 P1-1 hint 注入来源） ----------

def build_hint_entities(db, book_id: str) -> list[str]:
    """跨章命名全量名单：查 `entity` 表全部规范名（entity_name）→ 名单。

    供 003 P1-1 的 `hint_entities` 注入（只放规范名，不放全部别名，控 token）；
    chapter_prepare 调它并把名单放进 ChapterState.hint_entities。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :return: 该书已入库实体规范名列表
    """
    rows = db.execute(
        text("SELECT entity_name FROM entity WHERE book_id = :b ORDER BY entity_name"),
        {"b": book_id},
    ).scalars().all()
    return [r for r in rows if r]
