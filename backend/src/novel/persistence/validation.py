# -*- coding: utf-8 -*-
"""
validation_issue 读写（子任务 07 建表 + 写入；review 状态机在 08）。

职责：Layer 0/1 拦截项 / 关键记录冲突 / 无原文锚点疑似幻觉 统一进 `validation_issue` 队列（pending），
人工复核（08 review 状态机）最终裁决。本子任务提供**写入基元**与 pending 读取基元。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text

from UTILS.snowflake import snowflake

logger = logging.getLogger("novel.validation")


def _as_text(v) -> str | None:
    """把写入 validation_issue 的值规整为可存文本（dict/list → JSON 字符串）。"""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def create_validation_issue(db, issue: dict) -> str:
    """写一条 validation_issue（pending）。issue_id = vis_{snowflake}。

    :param db: SQLAlchemy Session（调用方负责事务/关闭）
    :param issue: {book_id, job_id?, chapter_id?, record_type, issue_type,
                   severity?, description?, original_value?, suggested_value?, target_id?}
                   （original_value/suggested_value 可为 dict/list，自动 JSON 序列化）
    :return: issue_id
    """
    issue_id = f"vis_{snowflake.generate()}"
    db.execute(
        text(
            "INSERT INTO validation_issue "
            "(issue_id, book_id, job_id, chapter_id, record_type, issue_type, severity, status, "
            "description, original_value, suggested_value, target_id) "
            "VALUES (:issue_id, :book_id, :job_id, :chapter_id, :record_type, :issue_type, "
            ":severity, 'pending', :description, :original_value, :suggested_value, :target_id)"
        ),
        {
            "issue_id": issue_id,
            "book_id": issue["book_id"],
            "job_id": issue.get("job_id"),
            "chapter_id": issue.get("chapter_id"),
            "record_type": issue.get("record_type", ""),
            "issue_type": issue.get("issue_type", ""),
            "severity": issue.get("severity", "warning"),
            "description": _as_text(issue.get("description", "")),
            "original_value": _as_text(issue.get("original_value")),
            "suggested_value": _as_text(issue.get("suggested_value")),
            "target_id": issue.get("target_id"),   # 目标行业务键（Sub-5 §4.2 回写定位）
        },
    )
    return issue_id


def list_pending_issues(db, book_id: str, limit: int = 100) -> list[dict]:
    """按 book_id 列出 pending 疑点（供 08 review 工作流；本子任务只提供读基元）。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param limit: 上限
    :return: pending issue dict 列表
    """
    rows = db.execute(
        text(
            "SELECT v.issue_id, v.book_id, v.chapter_id, v.record_type, v.issue_type, v.severity, "
            "v.status, v.description, v.original_value, v.suggested_value, v.target_id, "
            "nc.chapter_title AS chapter_title "
            "FROM validation_issue v "
            "LEFT JOIN novel_chapter nc ON nc.chapter_id = v.chapter_id "
            "WHERE v.book_id = :b AND v.status = 'pending' "
            "ORDER BY v.id DESC LIMIT :lim"
        ),
        {"b": book_id, "lim": limit},
    ).mappings().all()
    pending = [dict(r) for r in rows]
    # Sub-5 §4.4：附上目标行 confidence（供 ReviewView 排序/展示）；不可映射 → None。
    for iss in pending:
        target = _REVIEW_TARGETS.get(iss["record_type"])
        target_id = iss.pop("target_id", None)          # target_id 仅内部定位用，不进返回结构
        if not target or not target_id:
            iss["confidence"] = None
            continue
        table, id_col = target
        row = db.execute(
            text(f"SELECT confidence FROM {table} WHERE {id_col} = :t AND book_id = :b"),
            {"t": target_id, "b": book_id},
        ).mappings().one_or_none()
        iss["confidence"] = float(row["confidence"]) if row and row["confidence"] is not None else None
    return pending


# ====================== review 状态机（子任务 08） ======================
# 合法迁移：pending → confirmed / fixed / ignored；非 pending 拒绝（防误操作）。
# 关键纪律：LLM-judge 输出只作 flags，人工确认后才 re-persist（关键记录不未验证覆写）。

_VALID_STATUSES = {"confirmed", "fixed", "ignored"}

# 合法迁移表：from_status -> 允许的目标状态集合
#   pending   → confirmed（人工确认）/ fixed（直接修）/ ignored（误报）
#   confirmed → fixed（re-persist 成功后）/ ignored（复评误报）
_TRANSITIONS = {
    "pending": {"confirmed", "fixed", "ignored"},
    "confirmed": {"fixed", "ignored"},
}

# 复核写回映射（大修002 Sub-5 §1.1/§4.2）：record_type -> (目标表, 业务键列)。
# 只覆盖 9 张内容表；entity_alias / timeline_event_entity 为纯映射表，不写回。
_REVIEW_TARGETS = {
    "entity": ("entity", "entity_id"),
    "entity_snapshot": ("entity_snapshot", "snapshot_id"),
    "entity_relation": ("entity_relation", "relation_id"),
    "timeline_event": ("timeline_event", "event_id"),
    "location": ("location", "location_id"),
    "location_snapshot": ("location_snapshot", "snapshot_id"),
    "foreshadowing": ("foreshadowing", "foreshadowing_id"),
    "story_conflict": ("story_conflict", "conflict_id"),
    "rule_check": ("rule_check", "rule_id"),
}


def write_back_review(db, issue_id: str, decision: str) -> None:
    """复核裁决写回目标知识行（Sub-5 §4.2）：review_status + confidence=1.0。

    人工裁决 = 最高置信信号；与状态迁移同一事务（调用方负责 commit）。
    定位不清（issue 不存在 / record_type 未映射 / target_id 空）→ log + skip，禁止模糊 UPDATE。
    :param db: SQLAlchemy Session
    :param issue_id: vis_{snowflake}
    :param decision: confirmed / ignored / fixed
    """
    row = db.execute(
        text("SELECT target_id, record_type, book_id FROM validation_issue WHERE issue_id = :i"),
        {"i": issue_id},
    ).mappings().one_or_none()
    if not row:
        logger.warning("复核写回跳过：issue 不存在 | issue=%s", issue_id)
        return
    target = _REVIEW_TARGETS.get(row["record_type"])
    if not target or not row["target_id"]:
        logger.warning("复核写回跳过：record_type=%s 未映射 或 target_id 为空 | issue=%s",
                       row["record_type"], issue_id)
        return
    table, id_col = target               # 白名单映射（非用户输入），可安全插表名/列名
    db.execute(
        text(
            f"UPDATE {table} SET review_status = :d, confidence = 1.0 "
            f"WHERE {id_col} = :t AND book_id = :b"
        ),
        {"d": decision, "t": row["target_id"], "b": row["book_id"]},
    )
    logger.info("复核写回 | issue=%s record_type=%s %s=%s decision=%s",
                issue_id, row["record_type"], id_col, row["target_id"], decision)


def update_issue_status(db, issue_id: str, status: str, resolved_by: str | None = None) -> bool:
    """review 状态迁移：按合法迁移表（pending→confirmed/fixed/ignored；confirmed→fixed/ignored）。

    :param db: SQLAlchemy Session（调用方负责 commit）
    :param issue_id: vis_{snowflake}
    :param status: 目标状态（confirmed/fixed/ignored）
    :param resolved_by: 人工处理人/方式
    :return: 是否迁移成功（非法迁移 / issue 不存在 → False）
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"非法目标状态 {status!r}，可选 {sorted(_VALID_STATUSES)}")
    row = db.execute(
        text("SELECT status FROM validation_issue WHERE issue_id = :i"),
        {"i": issue_id},
    ).mappings().one_or_none()
    if not row or row["status"] not in _TRANSITIONS or status not in _TRANSITIONS[row["status"]]:
        return False                                  # 非法迁移 → 拒绝（防误操作）
    db.execute(
        text(
            "UPDATE validation_issue SET status = :s, resolved_by = :by, "
            "resolved_at = CURRENT_TIMESTAMP WHERE issue_id = :i"
        ),
        {"s": status, "by": resolved_by, "i": issue_id},
    )
    return True


def confirm_issue(db, issue_id: str, resolved_by: str) -> bool:
    """人工确认疑点为真 → confirmed（re-persist 前置）。"""
    ok = update_issue_status(db, issue_id, "confirmed", resolved_by)
    if ok:
        write_back_review(db, issue_id, "confirmed")   # 同一事务内写回目标行
    return ok


def ignore_issue(db, issue_id: str, resolved_by: str) -> bool:
    """人工忽略（误报）→ ignored。"""
    ok = update_issue_status(db, issue_id, "ignored", resolved_by)
    if ok:
        write_back_review(db, issue_id, "ignored")     # 同一事务内写回目标行
    return ok


def fix_issue(db, issue_id: str, corrected_value: str, resolved_by: str) -> bool:
    """人工修正 → 记录 corrected_value（审计）→ fixed（re-persist 成功后调用）。"""
    ok = update_issue_status(db, issue_id, "fixed", resolved_by)
    if ok:
        db.execute(
            text("UPDATE validation_issue SET suggested_value = :v WHERE issue_id = :i"),
            {"v": corrected_value, "i": issue_id},
        )
        write_back_review(db, issue_id, "fixed")       # 同一事务内写回目标行
    return ok


def summary_pending_issues(db, book_id: str) -> dict:
    """疑点汇总（00 §2.2 加分项 3）：按 (issue_type, severity) 分组计数 → 人工复核待办。"""
    rows = db.execute(
        text(
            "SELECT issue_type, severity, COUNT(*) AS n FROM validation_issue "
            "WHERE book_id = :b AND status = 'pending' GROUP BY issue_type, severity"
        ),
        {"b": book_id},
    ).mappings().all()
    return {"pending_total": sum(int(r["n"]) for r in rows),
            "by_type_severity": [dict(r) for r in rows]}
