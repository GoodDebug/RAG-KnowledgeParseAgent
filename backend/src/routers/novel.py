# -*- coding: utf-8 -*-
"""
小说解构 API（子任务 10）：jobs / job / chapters / deconstruct 重跑 / query / validation review + SSE 进度流。

对外暴露解构流水线（01-09 已落地）：客户端定位上传自动创建的 job、查看进度、查询解构结果、人工复核 validation_issue。
- SSE：`events.py` 进程内总线按 job_id 订阅实时事件 + 轮询 `deconstruct_job` 发 progress（00 §4.d）；
- review：只调用 08 服务层（confirm/ignore/fix/repersist），不重写状态机；
- retry/resume 在 11（本子任务不建）。
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTasks
from sqlalchemy import text

from core.deps import get_current_user
from db import SessionLocal
from db.models import Document, User
from novel import events
from novel.orchestrator import run_job
from novel.persistence import job_state, repositories
from novel.persistence.validation import (
    confirm_issue,
    fix_issue,
    ignore_issue,
    list_pending_issues,
    summary_pending_issues,
    update_issue_status,
)
from novel.pipeline.repersist import repersist_book

router = APIRouter(tags=["novel"])
logger = logging.getLogger("novel.api")

_PROGRESS_INTERVAL_S = 1.0


def _event(data: dict) -> str:
    """SSE 事件格式化（与 chat.py 一致）。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _require_book(db, user: User, book_id: str) -> None:
    """book 归属校验：用户只能查自己的书。"""
    row = db.query(Document).filter(Document.book_id == book_id, Document.user_id == user.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="book 不存在或不属于当前用户")


# ---------------- jobs / job / chapters ----------------

@router.get("/books/{book_id}/jobs")
def list_jobs(book_id: str, user: User = Depends(get_current_user)) -> list[dict]:
    """该书解构任务列表（最新在前）——客户端定位上传自动创建的 job。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        return repositories.list_jobs(db, book_id)
    finally:
        db.close()


@router.get("/jobs/{job_id}")
def get_job_detail(job_id: str, user: User = Depends(get_current_user)) -> dict:
    """job 详情（含章节状态）。"""
    db = SessionLocal()
    try:
        job = job_state.get_job(db, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job 不存在")
        _require_book(db, user, job["book_id"])
        job["chapters"] = repositories.get_job_chapters(db, job_id)
        return job
    finally:
        db.close()


@router.get("/books/{book_id}/chapters")
def list_book_chapters(book_id: str, user: User = Depends(get_current_user)) -> list[dict]:
    """章节列表（复用 repositories.list_chapters）。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        return repositories.list_chapters(db, book_id)
    finally:
        db.close()


# ---------------- deconstruct 重跑 ----------------

def _run_job_sync(job_id: str) -> None:
    """后台跑 run_job（BackgroundTasks 里 asyncio.run 包装）。"""
    try:
        asyncio.run(run_job(job_id))
    except Exception:
        logger.exception("后台 run_job 异常 | job=%s", job_id)


@router.post("/books/{book_id}/deconstruct")
def deconstruct_book(book_id: str, user: User = Depends(get_current_user),
                     background_tasks: BackgroundTasks = None) -> dict:
    """对已有 novel_chapter 的书重解构 → 202 新 job。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        chapters = repositories.list_chapters(db, book_id)
        if not chapters:
            raise HTTPException(status_code=404, detail="该书无 novel_chapter（需先上传并 deconstruct=1）")
        running = db.execute(
            text("SELECT COUNT(*) FROM deconstruct_job WHERE book_id=:b AND status='running'"),
            {"b": book_id},
        ).scalar()
        if running:
            raise HTTPException(status_code=409, detail="该书已有 running job")
        job_id = job_state.create_job(db, book_id=book_id, user_id=user.id,
                                      trigger_type="manual", total=len(chapters))
        job_state.add_chapter_states(db, job_id, chapters)
        events.publish({"type": "job_started", "job_id": job_id, "book_id": book_id,
                        "trigger_type": "manual", "total_chapters": len(chapters)})
        background_tasks.add_task(_run_job_sync, job_id)
        logger.info("deconstruct 重跑 | book=%s job=%s chapters=%d", book_id, job_id, len(chapters))
        return {"job_id": job_id, "book_id": book_id, "status": "pending",
                "total_chapters": len(chapters)}
    finally:
        db.close()


# ---------------- SSE 进度流 ----------------

@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, user: User = Depends(get_current_user),
              background_tasks: BackgroundTasks = None,
              body: dict = Body(default={"chapter_ids": None, "shrink": False})) -> dict:
    """重跑指定（或缺省=全部）failed 章 → 202 {job_id, retry_chapters:n}。

    404 job 不存在/归属错；409 已有 running（防并发双跑）。
    复用 `load_chapters` 自动挑 pending/failed 章（reset 后经 run_job 重跑）。
    """
    db = SessionLocal()
    try:
        job = job_state.get_job(db, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job 不存在")
        _require_book(db, user, job["book_id"])
        running = db.execute(
            text("SELECT COUNT(*) FROM deconstruct_job WHERE job_id=:j AND status='running'"),
            {"j": job_id},
        ).scalar()
        if running:
            raise HTTPException(status_code=409, detail="job 运行中")
        chapter_ids = (body or {}).get("chapter_ids")
        n = job_state.reset_chapters_to_pending(db, job_id, chapter_ids)
        events.publish({"type": "job_started", "job_id": job_id, "book_id": job["book_id"],
                        "trigger_type": "retry", "total_chapters": n})
        if n:
            background_tasks.add_task(_run_job_sync, job_id)
        logger.info("retry | job=%s chapters=%d", job_id, n)
        return {"job_id": job_id, "retry_chapters": n}
    finally:
        db.close()


@router.get("/jobs/{job_id}/stream")
def stream_job(job_id: str, user: User = Depends(get_current_user)) -> StreamingResponse:
    """SSE：订阅 events.py 按 job_id 过滤 + 轮询 deconstruct_job 发 progress。"""
    db = SessionLocal()
    try:
        job = job_state.get_job(db, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job 不存在")
        _require_book(db, user, job["book_id"])
    finally:
        db.close()

    async def _gen():
        queue: asyncio.Queue = asyncio.Queue()

        def _handler(event: dict) -> None:
            if event.get("job_id") == job_id:
                queue.put_nowait(event)

        events.subscribe(_handler)
        try:
            while True:
                # 1) 实时事件（events.py 总线，按 job_id 过滤）
                while not queue.empty():
                    ev = queue.get_nowait()
                    yield _event(ev)
                # 2) 轮询 deconstruct_job 发 progress / 终态
                d2 = SessionLocal()
                try:
                    cur = job_state.get_job(d2, job_id)
                    if cur is None:
                        break
                    yield _event({"type": "progress", "job_id": job_id,
                                  "done": cur["done_chapters"], "failed": cur["failed_chapters"],
                                  "total": cur["total_chapters"]})
                    if cur["status"] in ("done", "failed"):
                        yield _event({"type": "job_done" if cur["status"] == "done" else "job_failed",
                                      "job_id": job_id,
                                      "done_chapters": cur["done_chapters"],
                                      "failed_chapters": cur["failed_chapters"]})
                        break
                finally:
                    d2.close()
                await asyncio.sleep(_PROGRESS_INTERVAL_S)
        finally:
            events.unsubscribe(_handler)

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ---------------- 结构化查询 ----------------

@router.get("/books/{book_id}/browse/{type_}")
def browse_book(type_: str, book_id: str, request: Request,
                user: User = Depends(get_current_user)) -> dict:
    """知识库浏览（P1 补强）：按类型分页 + 字段筛选/模糊查询该书解构数据。

    type_ ∈ repositories.BROWSE_TYPES（entity/entity_snapshot/relation/timeline_event/
    location/foreshadowing/conflict/rule/alias/validation）；过滤参数随类型，均经参数绑定防注入。
    """
    if type_ not in repositories.BROWSE_TYPES:
        raise HTTPException(status_code=404, detail=f"未知浏览类型: {type_}")
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        return repositories.browse(db, book_id, type_, dict(request.query_params))
    finally:
        db.close()


# ====================== 大修002 · Knowledge API（共享查询层） ======================

def _max_chapter(db, book_id: str) -> int:
    """该书最大全局章节序号（chapter 缺省时的 as-of 当前态）。"""
    v = db.execute(text("SELECT MAX(chapter_index) FROM novel_chapter WHERE book_id=:b"), {"b": book_id}).scalar()
    return int(v) if v is not None else 0


@router.get("/books/{book_id}/knowledge/graph")
def knowledge_graph(book_id: str, entity_id: str, chapter: Optional[int] = Query(None),
                    user: User = Depends(get_current_user)) -> dict:
    """图谱 1-hop（大修002）：center 实体 + as-of N 有效关系 → nodes/edges（时态封装在后端）。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        ent = repositories.get_entity(db, entity_id)
        if not ent or ent.get("book_id") != book_id:
            raise HTTPException(status_code=404, detail="实体不存在或不属于当前用户")
        n = chapter if chapter is not None else _max_chapter(db, book_id)
        rels = repositories.get_valid_relations_at_chapter(db, book_id, entity_id, n)
        nodes = [{"entity_id": ent["entity_id"], "name": ent["entity_name"], "type": ent["entity_type"]}]
        seen = {ent["entity_id"]}
        edges = []
        for r in rels:
            if len(seen) >= 100 or len(edges) >= 100:
                break
            for eid, name in ((r["source_entity_id"], r["source_name"]), (r["target_entity_id"], r["target_name"])):
                if eid and eid not in seen:
                    seen.add(eid)
                    e = repositories.get_entity(db, eid)
                    nodes.append({"entity_id": eid, "name": name, "type": e["entity_type"] if e else "unknown"})
            edges.append({"from": r["source_entity_id"], "to": r["target_entity_id"],
                          "relation_type": r["relation_type"], "weight": r["relation_weight"]})
        return {"chapter": n,
                "center": {"entity_id": ent["entity_id"], "name": ent["entity_name"], "type": ent["entity_type"]},
                "nodes": nodes, "edges": edges}
    finally:
        db.close()


@router.get("/books/{book_id}/knowledge/entities/{entity_id}")
def knowledge_entity(entity_id: str, book_id: str, chapter: Optional[int] = Query(None),
                     user: User = Depends(get_current_user)) -> dict:
    """实体卡（大修002）：状态(as-of N) + 别名 + 关系(as-of N) + 事件 + 证据摘要。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        n = chapter if chapter is not None else _max_chapter(db, book_id)
        card = repositories.get_entity_card(db, book_id, entity_id, n)
        if card is None:
            raise HTTPException(status_code=404, detail="实体不存在或不属于当前用户")
        return card
    finally:
        db.close()


@router.get("/books/{book_id}/knowledge/timeline")
def knowledge_timeline(book_id: str, chapter_start: Optional[int] = Query(None),
                       chapter_end: Optional[int] = Query(None),
                       user: User = Depends(get_current_user)) -> dict:
    """时间线事件（章节区间，缺省=全部；含 involved_entities）。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        return {"events": repositories.get_timeline_events_by_range(db, book_id, chapter_start, chapter_end)}
    finally:
        db.close()


@router.get("/books/{book_id}/knowledge/entities/{entity_id}/evidence")
def knowledge_evidence(entity_id: str, book_id: str, chapter: Optional[int] = Query(None),
                       user: User = Depends(get_current_user)) -> dict:
    """实体原文证据（指定章含实体名/别名的窗口片段；未出现 → 200 null）。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        ent = repositories.get_entity(db, entity_id)
        if not ent or ent.get("book_id") != book_id:
            raise HTTPException(status_code=404, detail="实体不存在或不属于当前用户")
        n = chapter if chapter is not None else _max_chapter(db, book_id)
        return repositories.get_entity_evidence(db, book_id, entity_id, n) or {"evidence": None}
    finally:
        db.close()


@router.get("/books/{book_id}/knowledge/entities/{entity_id}/snapshots")
def knowledge_snapshots(entity_id: str, book_id: str,
                        chapter_start: Optional[int] = Query(None),
                        chapter_end: Optional[int] = Query(None),
                        user: User = Depends(get_current_user)) -> dict:
    """实体快照演化（章节区间，缺省=全部）。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        ent = repositories.get_entity(db, entity_id)
        if not ent or ent.get("book_id") != book_id:
            raise HTTPException(status_code=404, detail="实体不存在或不属于当前用户")
        sql = "SELECT chapter_index, status_desc, attributes, confidence, review_status FROM entity_snapshot WHERE book_id=:b AND entity_id=:e"
        params: dict = {"b": book_id, "e": entity_id}
        if chapter_start is not None:
            sql += " AND chapter_index>=:cs"; params["cs"] = chapter_start
        if chapter_end is not None:
            sql += " AND chapter_index<=:ce"; params["ce"] = chapter_end
        sql += " ORDER BY chapter_index"
        rows = db.execute(text(sql), params).mappings().all()
        return {"snapshots": [dict(r) for r in rows]}
    finally:
        db.close()


@router.get("/books/{book_id}/query")
def query_book(book_id: str, user: User = Depends(get_current_user),
               entity: Optional[str] = Query(None),
               chapter: Optional[int] = Query(None),
               chapter_start: Optional[int] = Query(None),
               chapter_end: Optional[int] = Query(None),
               events: bool = Query(False)) -> dict:
    """按实体/章节查询解构结果（SQL 装配 11 表）。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        result: dict = {}
        if entity:
            result["snapshots"] = repositories.get_entity_snapshots_by_name(
                db, book_id, entity, chapter)
            result["relations"] = repositories.get_relations_by_entity(
                db, book_id, entity, chapter_start, chapter_end)
        if events:
            result["events"] = repositories.get_timeline_events_by_chapter(
                db, book_id, chapter or 0)
        return result
    finally:
        db.close()


# ---------------- review（人工复核） ----------------

@router.get("/books/{book_id}/validation")
def list_validation(book_id: str, user: User = Depends(get_current_user)) -> dict:
    """pending 疑点列表 + 按 (issue_type, severity) 汇总待办。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        return {"pending": list_pending_issues(db, book_id),
                "summary": summary_pending_issues(db, book_id)}
    finally:
        db.close()


@router.post("/validation/{issue_id}/confirm")
def confirm_validation(issue_id: str, user: User = Depends(get_current_user)) -> dict:
    db = SessionLocal()
    try:
        ok = confirm_issue(db, issue_id, user.phone or "api")
        db.commit()
        if not ok:
            raise HTTPException(status_code=409, detail="非法迁移：仅 pending 可 confirm")
        return {"issue_id": issue_id, "status": "confirmed"}
    finally:
        db.close()


@router.post("/validation/{issue_id}/ignore")
def ignore_validation(issue_id: str, user: User = Depends(get_current_user)) -> dict:
    db = SessionLocal()
    try:
        ok = ignore_issue(db, issue_id, user.phone or "api")
        db.commit()
        if not ok:
            raise HTTPException(status_code=409, detail="非法迁移：仅 pending/confirmed 可 ignore")
        return {"issue_id": issue_id, "status": "ignored"}
    finally:
        db.close()


@router.post("/validation/{issue_id}/fix")
def fix_validation(issue_id: str, user: User = Depends(get_current_user),
                   body: dict = Body(default={"corrected_value": None})) -> dict:
    """按修正值 re-persist（08）→ fix_issue。"""
    from novel.pipeline.repersist import repersist_issue
    corrected = (body or {}).get("corrected_value")
    db = SessionLocal()
    try:
        ok = repersist_issue(db, issue_id, corrected)
        db.commit()
        if not ok:
            raise HTTPException(status_code=409, detail="re-persist 失败（pending 状态/非法修正值）")
        return {"issue_id": issue_id, "status": "fixed"}
    finally:
        db.close()


@router.post("/books/{book_id}/validation/repersist")
def repersist_validation(book_id: str, user: User = Depends(get_current_user),
                         body: dict = Body(default={"issue_ids": []})) -> dict:
    """批量 re-persist（人工勾选多条确认后触发）。"""
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        issue_ids = (body or {}).get("issue_ids") or []
        r = repersist_book(db, book_id, issue_ids)
        db.commit()
        return r
    finally:
        db.close()


# ====================== 大修002 P2-1：复核左右分屏（evidence + 批量确认） ======================

@router.get("/books/{book_id}/validation/{issue_id}/evidence")
def get_issue_evidence(issue_id: str, book_id: str,
                       user: User = Depends(get_current_user)) -> dict:
    """疑点原文证据（P2-1）：真实原文 ±200 窗口 + 命中关键词；无证据 → {evidence: null}。

    issue 不存在或不属于当前用户 → 404；其余交给 repositories.get_issue_evidence（无证据 → null）。
    """
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        row = db.execute(
            text("SELECT book_id FROM validation_issue WHERE issue_id=:i"),
            {"i": issue_id},
        ).mappings().one_or_none()
        if row is None or row["book_id"] != book_id:
            raise HTTPException(status_code=404, detail="疑点不存在或不属于当前用户")
        return repositories.get_issue_evidence(db, book_id, issue_id) or {"evidence": None}
    finally:
        db.close()


@router.post("/books/{book_id}/validation/confirm")
def confirm_validation_batch(book_id: str, user: User = Depends(get_current_user),
                             body: dict = Body(default={"issue_ids": []})) -> dict:
    """批量确认疑点（P2-1 一键确认低风险）：逐条 confirm_issue + 写回，单事务。

    先校验 issue.book_id==book_id（不存在/非本人 → failed 跳过，不越权）；非 pending（已裁决）→ failed；
    其余 succeeded。循环后统一 commit；结果计数返回 {total, succeeded, failed}。
    """
    db = SessionLocal()
    try:
        _require_book(db, user, book_id)
        issue_ids = (body or {}).get("issue_ids") or []
        succeeded: list[str] = []
        failed: list[str] = []
        for iid in issue_ids:
            owner = db.execute(
                text("SELECT book_id FROM validation_issue WHERE issue_id=:i"),
                {"i": iid},
            ).mappings().one_or_none()
            if owner is None or owner["book_id"] != book_id:
                failed.append(iid)                     # 不存在 / 非本人 → 跳过（不越权）
                continue
            if confirm_issue(db, iid, user.phone or "api"):
                succeeded.append(iid)                  # pending → confirmed + 同事务写回
            else:
                failed.append(iid)                     # 非 pending（已确认/忽略/修复）→ 拒绝
        db.commit()
        logging.getLogger("novel.validation").info(
            "批量确认 | book=%s total=%d succeeded=%d failed=%s",
            book_id, len(issue_ids), len(succeeded), failed,
        )
        return {"total": len(issue_ids), "succeeded": len(succeeded), "failed": failed}
    finally:
        db.close()
