# -*- coding: utf-8 -*-
"""
re-persist 修正流程（子任务 08，C4）。

职责：人工确认一条 validation_issue 后，按 suggested_value（或人工修正值）幂等重写 11 表对应记录。
复用 07 `persist_chapter_tables` / 03-05 upsert 基元 / 06 resolver 解析；**只接受人工确认后的修正值**。

纪律：LLM-judge 输出只作 flags，绝不 auto-fix；只有人工 confirm 后才走本流程。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text

from novel.persistence import upsert
from novel.persistence.validation import fix_issue
from novel.pipeline import resolver

logger = logging.getLogger("novel.repersist")


def _get_issue(db, issue_id: str) -> dict | None:
    row = db.execute(
        text(
            "SELECT issue_id, book_id, chapter_id, record_type, issue_type, status, "
            "original_value, suggested_value FROM validation_issue WHERE issue_id = :i"
        ),
        {"i": issue_id},
    ).mappings().one_or_none()
    return dict(row) if row else None


def _parse_record(raw: str) -> dict | None:
    """把修正值解析为记录 dict（suggested_value 或 corrected_value 存的是 JSON 字符串）。"""
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def repersist_issue(db, issue_id: str, corrected_value: str | None = None) -> bool:
    """按建议值/人工修正值幂等重写对应记录 → 成功后 fix_issue。

    :param db: SQLAlchemy Session（调用方负责 commit）
    :param issue_id: vis_{snowflake}
    :param corrected_value: 人工修正值（JSON 字符串）；None → 用 issue.suggested_value
    :return: 是否重写成功（并已置 fixed）
    """
    issue = _get_issue(db, issue_id)
    # 可处理状态：pending（直接按建议值重写）或 confirmed（人工确认后重写）；fixed/ignored 已处理 → 拒
    if not issue or issue["status"] not in ("pending", "confirmed"):
        return False
    value = corrected_value if corrected_value is not None else issue.get("suggested_value")
    record = _parse_record(value)
    if not record:
        logger.warning("re-persist 失败：修正值非合法 JSON | issue=%s", issue_id)
        return False

    book_id = issue["book_id"]
    rtype = issue["record_type"]
    try:
        if rtype == "entity_relation":
            eid = resolver.resolve_entity_names(db, book_id, [{"name": record.get("source", "")}])
            tid = resolver.resolve_entity_names(db, book_id, [{"name": record.get("target", "")}])
            record.setdefault("relation_id", f"rel_re_{issue_id[-8:]}")
            record["book_id"] = book_id
            record["source_entity_id"] = eid.get(record.get("source", ""))
            record["target_entity_id"] = tid.get(record.get("target", ""))
            upsert.upsert_entity_relation(db, record)
        elif rtype == "entity_snapshot":
            eid = resolver.resolve_entity_names(db, book_id, [{"name": record.get("entity_name", "")}])
            record.setdefault("snapshot_id", f"s_{eid.get(record.get('entity_name',''))}_{record.get('chapter_index',0)}")
            record["book_id"] = book_id
            record["entity_id"] = eid.get(record.get("entity_name", ""))
            upsert.upsert_entity_snapshot(db, record)
        elif rtype == "timeline_event":
            evid = resolver.resolve_event_titles(db, book_id, [{"event_title": record.get("event_title", ""),
                                                                "event_level": record.get("event_level", "event")}])
            record["event_id"] = evid.get(record.get("event_title", ""))
            record["book_id"] = book_id
            upsert.upsert_timeline_event(db, record)
        elif rtype == "rule_check":
            eid = resolver.resolve_entity_names(db, book_id, [{"name": record.get("subject_entity_name", "")}])
            record.setdefault("rule_id", f"rul_re_{issue_id[-8:]}")
            record["book_id"] = book_id
            record["subject_entity_id"] = eid.get(record.get("subject_entity_name", ""))
            upsert.upsert_rule_check(db, record)
        elif rtype == "location_snapshot":
            record["book_id"] = book_id
            upsert.upsert_location_snapshot(db, record)
        elif rtype == "foreshadowing":
            record.setdefault("foreshadowing_id", f"fs_re_{issue_id[-8:]}")
            record["book_id"] = book_id
            upsert.upsert_foreshadowing(db, record)
        elif rtype == "story_conflict":
            record.setdefault("conflict_id", f"cfl_re_{issue_id[-8:]}")
            record["book_id"] = book_id
            upsert.upsert_story_conflict(db, record)
        elif rtype == "entity":
            upsert.register_entity(db, book_id, record)
        else:
            logger.warning("re-persist 未知 record_type=%s | issue=%s", rtype, issue_id)
            return False
        fix_issue(db, issue_id, json.dumps(record, ensure_ascii=False), "repersist")
        logger.info("re-persist 成功 | issue=%s rtype=%s", issue_id, rtype)
        return True
    except Exception as e:                      # 失败保持 pending，不误标 fixed
        logger.error("re-persist 失败 | issue=%s err=%s", issue_id, e, exc_info=True)
        return False


def repersist_book(db, book_id: str, issue_ids: list[str]) -> dict:
    """批量 re-persist（人工在 review 界面勾选多条确认后触发）。"""
    ok = 0
    for iid in issue_ids:
        if repersist_issue(db, iid):
            ok += 1
    return {"total": len(issue_ids), "succeeded": ok}
