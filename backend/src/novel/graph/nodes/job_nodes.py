# -*- coding: utf-8 -*-
"""
Job 级节点：load_chapters / aggregate / finalize_job —— JobGraph 主图的三个骨架节点。

负责"整本书级"的流转：
  load_chapters（挑出待解构章节） → [Send×N 每章一个子图] → aggregate（归约计数）→ finalize（定终态）

DB 会话约定：每节点短生命周期 SessionLocal、finally 关闭。
状态机语义：
  - load：job pending → running，挑 pending/failed 章；
  - aggregate：把各章结果归约成 done/failed 计数，一次性写回 job 行（避免并行写）；
  - finalize：按 deconstruct_chapter_state 的实况定终态（对断点续传稳健）。
"""
import logging

from itertools import groupby

from db import SessionLocal
from novel import events
from novel.config import novel_chapter_lease_seconds
from novel.persistence.job_state import (
    bump_job_counts,
    count_chapter_states,
    finalize_job as finalize_job_db,   # 别名：避免与本模块的 finalize_job 节点函数重名
    reap_stale_processing,
    set_job_running,
)
from novel.persistence.validation import create_validation_issue
from sqlalchemy import text

logger = logging.getLogger("novel.graph.jobs")


class JobNotFoundError(Exception):
    """任务不存在。"""


def load_chapters(state: dict) -> dict:
    """节点：读该 job 的 pending/failed 章（联 novel_chapter 取元数据）→ chapters；job → running。

    断点续传语义：只挑 `pending`/`failed` 的章节 —— 已完成（done）的章节不重跑。

    空章节兜底：全新空 job 或全部章节已完成时，`_fan_out_chapters` 的条件边会返回**空 Send 列表**，
    而空 Send 会让图提前结束、走不到 finalize —— 所以在此直接调用 `finalize_job_db(..., "done")` 兜底。

    :return: {chapters, total_chapters, job_status}
    """
    job_id = state["job_id"]
    db = SessionLocal()
    try:
        # ★ P0-3：先回收本 job 的僵死章（processing 超租约 → pending），复位后自然落入下方查询被重新认领
        reaped = reap_stale_processing(db, job_id, novel_chapter_lease_seconds())
        if reaped:
            logger.info("load_chapters | job=%s 回收僵死章 %d 个 → 复位 pending 重认领", job_id, reaped)
        # JOIN novel_chapter 拿 book_name/chapter_title 等子图需要的元数据
        rows = db.execute(
            text(
                "SELECT cs.chapter_id, cs.book_id, cs.chapter_index, cs.status, "
                "       nc.book_name, nc.chapter_title, nc.scene_count "
                "FROM deconstruct_chapter_state cs "
                "JOIN novel_chapter nc ON nc.chapter_id = cs.chapter_id "
                "WHERE cs.job_id = :j AND cs.status IN ('pending', 'failed') "
                "ORDER BY cs.chapter_index"
            ),
            {"j": job_id},
        ).mappings().all()
        chapters = [dict(r) for r in rows]
        if not chapters:
            # ★ P0-1 多进程守卫：空清单时先看是否有人 in-flight——有则交由 finalize 按实况收尾，不越权置 done
            counts = count_chapter_states(db, job_id)
            if counts.get("processing", 0) == 0 and counts.get("pending", 0) == 0:
                finalize_job_db(db, job_id, "done")       # 真无待处理 → 直接终态 done
                logger.info("load_chapters | job=%s 无待处理章节，直接 done", job_id)
                return {"chapters": [], "total_chapters": 0, "job_status": "done"}
            logger.info("load_chapters | job=%s 本地无待处理但他人 in-flight，交由 finalize 收尾", job_id)
            return {"chapters": [], "total_chapters": 0, "job_status": "running"}
        set_job_running(db, job_id)                        # 状态机：pending → running
        logger.info("load_chapters | job=%s pending/failed=%d", job_id, len(chapters))
        return {"chapters": chapters, "total_chapters": len(chapters), "job_status": "running"}
    finally:
        db.close()


def aggregate(state: dict) -> dict:
    """节点：归约 chapter_results → 一次性递增 deconstruct_job.done/failed 计数。

    设计要点：不要在**每个 persist_chapter 里**各自累加 job 计数（并行写同一行会撞车），
    而是等所有章完成后由本节点**单次**按归约结果写回（aggregate 是 fan-in 后的单点）。
    :return: {}（本节点不更新 State，只做 DB 副作用）
    """
    results: list[dict] = state.get("chapter_results") or []
    done = sum(1 for r in results if r.get("status") == "done")
    failed = sum(1 for r in results if r.get("status") == "failed")
    job_id = state["job_id"]
    db = SessionLocal()
    try:
        bump_job_counts(db, job_id, done, failed)
        logger.info("aggregate | job=%s done=%d failed=%d", job_id, done, failed)
    finally:
        db.close()
    return {}


def validate_book(state: dict) -> dict:
    """节点（08，aggregate 之后、finalize 之前）：跨章 Layer 1 全局一致性检查（确定性）。

    读该书已入库 11 表，做三类跨章检查 → 冲突项写 `validation_issue(pending)`：
      1. 时间线 `timeline_event.global_sort` 全书全局有序（无倒退/重复）；
      2. 实体状态跨章连续：`entity_snapshot` 相对相邻章状态翻转，但翻转章无该实体的事件支撑；
      3. 战力/封印阶梯：`rule_check` 同 ability 跨章矛盾 cap 且无 balance_lock。

    可选 Layer 2（validator_agent）由 `NOVEL_VALIDATOR_ENABLED` 控制、另行按书批处理（不阻塞图）。

    :return: {validation_issues: [...]}（reducer，供父图归约）
    """
    job_id = state["job_id"]
    book_id = state["book_id"]
    db = SessionLocal()
    try:
        issues: list[dict] = []

        # 1) 时间线全局有序（07 合成公式保证不撞号，此处兜底校验倒退/重复）
        #    按插入序（id）严格递增：重复或倒退都算 paradox（合成碰撞/顺序错）
        rows = db.execute(
            text("SELECT event_id, global_sort FROM timeline_event WHERE book_id=:b ORDER BY id"),
            {"b": book_id},
        ).mappings().all()
        prev = -1
        for r in rows:
            gs = int(r["global_sort"])
            if gs <= prev:
                issues.append({
                    "record_type": "timeline_event", "issue_type": "timeline_paradox",
                    "severity": "warning", "description": f"时间线全局乱序/重复: {gs} 在 {prev} 后",
                    "original_value": prev, "suggested_value": gs,
                })
            prev = gs

        # 2) 实体状态跨章连续：状态翻转需事件支撑（复用 06 启发式的书级版）
        snaps = db.execute(
            text("SELECT entity_name, status_desc, chapter_index FROM entity_snapshot "
                 "WHERE book_id=:b ORDER BY entity_name, chapter_index"),
            {"b": book_id},
        ).mappings().all()
        for ent, grp in groupby(snaps, key=lambda r: r["entity_name"]):
            grp = list(grp)
            for p, c in zip(grp, grp[1:]):
                if not (p["status_desc"] and c["status_desc"] and p["status_desc"] != c["status_desc"]):
                    continue
                n = db.execute(
                    text("SELECT COUNT(*) FROM timeline_event_entity tee "
                         "JOIN entity e ON e.entity_id = tee.entity_id "
                         "WHERE e.book_id=:b AND e.entity_name=:en AND tee.chapter_index=:c"),
                    {"b": book_id, "en": ent, "c": c["chapter_index"]},
                ).scalar()
                if n == 0:
                    issues.append({
                        "record_type": "entity_snapshot", "issue_type": "state_jump",
                        "severity": "warning",
                        "description": f"跨章状态翻转无事件支撑: {ent} "
                                        f"{p['status_desc']}->{c['status_desc']} @ch{c['chapter_index']}",
                        "original_value": p["status_desc"], "suggested_value": c["status_desc"],
                    })

        # 3) 战力/封印阶梯：同 ability 跨章矛盾 cap 且无 balance_lock → 疑似跳级/倒退
        rules = db.execute(
            text("SELECT rule_type, subject_ability, rule_content FROM rule_check "
                 "WHERE book_id=:b AND rule_type IN ('cap','balance_lock')"),
            {"b": book_id},
        ).mappings().all()
        by_ability: dict[str, list[dict]] = {}
        for r in rules:
            by_ability.setdefault(r["subject_ability"] or "", []).append(r)
        for ability, rs in by_ability.items():
            caps = {r["rule_content"] for r in rs if r["rule_type"] == "cap"}
            has_bal = any(r["rule_type"] == "balance_lock" for r in rs)
            if len(caps) > 1 and not has_bal:
                issues.append({
                    "record_type": "rule_check", "issue_type": "rule_violation",
                    "severity": "warning",
                    "description": f"能力 {ability!r} 跨章矛盾 cap 且无 balance_lock: {sorted(caps)}",
                    "original_value": None, "suggested_value": None,
                })

        for iss in issues:
            create_validation_issue(db, {**iss, "book_id": book_id, "job_id": job_id})
        db.commit()
        logger.info("validate_book | book=%s issues=%d", book_id, len(issues))
        return {"validation_issues": issues}
    finally:
        db.close()


def finalize_job(state: dict) -> dict:
    """节点：按 deconstruct_chapter_state 的**实况**定终态。

    以 DB 为准（而非仅信内存 chapter_results）：直接 GROUP BY status 数一遍，
    全部 done → done；存在 failed → failed；无章节 → done（幂等空跑）。
    :return: {job_status[, error_msg]}
    """
    job_id = state["job_id"]
    db = SessionLocal()
    try:
        counts = count_chapter_states(db, job_id)
        total = state.get("total_chapters", 0) or sum(counts.values())
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)
        processing = counts.get("processing", 0)
        pending = counts.get("pending", 0)
        # ★ P0-1 多进程守卫：仍有 in-flight（processing）或未认领（pending）章节 → 不越权终态，
        #   交由最后一个完成自己章节的 worker 收尾（防非 owner 提前把 job 置 done）
        if processing > 0 or pending > 0:
            logger.info("job in-flight 延迟终态 | job=%s processing=%d pending=%d", job_id, processing, pending)
            return {"job_status": "running"}
        if total > 0 and done == total:
            finalize_job_db(db, job_id, "done")            # 状态机：running → done
            events.publish({"type": "job_done", "job_id": job_id, "done_chapters": done, "failed_chapters": failed})
            logger.info("job done | %s done=%d total=%d", job_id, done, total)
            return {"job_status": "done"}
        if failed > 0:
            finalize_job_db(db, job_id, "failed", error="存在失败章节")
            events.publish({"type": "job_failed", "job_id": job_id, "error": "存在失败章节"})
            logger.warning("job failed | %s failed=%d", job_id, failed)
            return {"job_status": "failed", "error_msg": "存在失败章节"}
        # total=0 或仍 pending（无章节）→ 直接 done（幂等空跑）
        finalize_job_db(db, job_id, "done")
        events.publish({"type": "job_done", "job_id": job_id, "done_chapters": done, "failed_chapters": failed})
        return {"job_status": "done"}
    finally:
        db.close()
