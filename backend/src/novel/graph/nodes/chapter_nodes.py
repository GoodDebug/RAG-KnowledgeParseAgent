# -*- coding: utf-8 -*-
"""
Chapter 级节点：chapter_prepare / validate_chapter / merge_chapter / persist_chapter。

这是 ChapterGraph（单章解构子图）的"骨架节点"，负责一章内从"读原文"到"落终态"的流转：

  chapter_prepare（读原文+切场景） → [8 个 agent 并行抽取] → validate（校验）
  → merge（归并）→ persist（标记 done/failed + 产出归约结果）

DB 会话约定：每个节点**短生命周期**开 SessionLocal、finally 关闭（不跨节点持有连接）。
子任务 02 语义：persist 只做任务状态机；11 张解构表的幂等入库 + resolver 由 06-07 填充。
"""
import logging

from db import SessionLocal
from novel import events
from novel.persistence.job_state import (
    set_chapter_done,
    set_chapter_failed,
    set_chapter_processing,
)
from novel.persistence.repositories import get_prev_snapshots
from novel.pipeline.chapters import _split_scenes
from novel.pipeline.merge import merge_chapter_results
from novel.pipeline.persist import persist_chapter_tables
from novel.pipeline.resolver import build_hint_entities
from sqlalchemy import text

logger = logging.getLogger("novel.graph.chapters")


def chapter_prepare(state: dict) -> dict:
    """节点：读 novel_chapter.chapter_text → 场景切分 → 把章节状态标为 processing。

    设计要点：章节原文**不进 Send payload**（避免把大文本在分支间复制），
    而是本节点从 MySQL 按 chapter_id 现读 → 只有这里持有 chapter_text，state 保持 lean。

    :return: 对 ChapterState 的更新 {chapter_text, scenes, scene_count, shrink_level}
    """
    chapter_id = state["chapter_id"]
    job_id = state["job_id"]
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT chapter_text FROM novel_chapter WHERE chapter_id = :c"),
            {"c": chapter_id},
        ).mappings().one_or_none()
        if row is None:
            raise ValueError(f"novel_chapter 不存在：{chapter_id}")
        chapter_text = row["chapter_text"]
        # 超长章节按段落贪心切成场景（scenes 只在本 State 中，不落库）
        scenes = _split_scenes(chapter_text)
        # ★ 乐观锁认领（P0-1）：False = 另一进程已认领本章 → 本进程跳过（不扇出 Agent、不写结果、不发事件）
        claimed = set_chapter_processing(db, job_id, chapter_id)
        if not claimed:
            logger.info("chapter skipped（已被其他 worker 认领）| job=%s chapter=%s", job_id, chapter_id)
            return {
                "chapter_status": "skipped",
                "chapter_text": chapter_text,
                "scenes": scenes,
                "scene_count": len(scenes),
            }
        events.publish({                                  # 发布事件（供 10 SSE / 日志）；仅认领成功才发
            "type": "chapter_started",
            "job_id": job_id,
            "chapter_id": chapter_id,
            "chapter_index": state.get("chapter_index", 0),
            "chapter_title": state.get("chapter_title", ""),
            "scene_count": len(scenes),
        })
        # 跨章命名全量名单（06 build_hint_entities）：注入 ChapterState.hint_entities，
        # 由 fan_out_agents Send payload 带给各 agent → run_agent 透传（003 P1-1 已落地）。
        hint_entities = build_hint_entities(db, state["book_id"])
        # 二阶段 02 增量提取：历史已入库快照摘要（每实体最新一条 chapter < 当前；
        # 因章节并行解构，可能是更早章的最新已入库状态，非紧邻上一章）——
        # 注入 ChapterState.prev_snapshot_context，经 Send payload → run_agent（仅 entity_snapshot）
        # → build_prompt 追加历史块（仅作背景参考；本章原文明确描述的必须完整输出，防漏）。
        prev_snaps = get_prev_snapshots(db, state["book_id"], state.get("chapter_index", 0))
        prev_ctx = (
            "\n".join(
                f"{s['entity_name']}：{s['status_desc']}（第 {s['chapter_index']} 章）"
                for s in prev_snaps
            )
            if prev_snaps else ""
        )
        return {
            "chapter_text": chapter_text,
            "scenes": scenes,
            "scene_count": len(scenes),
            "shrink_level": int(state.get("shrink_level", 0)),
            "hint_entities": hint_entities,
            "prev_snapshot_context": prev_ctx,
        }
    finally:
        db.close()


def validate_chapter(state: dict) -> dict:
    """节点：02 最小校验 —— errors 里有致命错误则整章判 failed，否则 ok。

    子任务 03-07 会补充 schema 完整性、字段合法性等更强校验。
    :return: {chapter_status: "ok" | "failed"}
    """
    errors: list[dict] = state.get("errors") or []
    if errors:
        logger.warning("validate failed | chapter=%s errors=%d", state.get("chapter_id"), len(errors))
        return {"chapter_status": "failed"}
    return {"chapter_status": "ok"}


def merge_chapter(state: dict) -> dict:
    """节点：章内归并（子任务 06 填实）——实体名归并 + 跨 Agent 引用对齐 + 事实去重。

    merge 是确定性纯逻辑（不调 LLM）：validate 判 ok 才归并；failed/skipped 直通。
    **返回写入标量键 `merged`**（非各 reducer key——那些带 `operator.add` 归约会 append
    而非覆盖，写回会造成"原始+归并"重复）；persist_chapter 读 `state["merged"]`。
    """
    if state.get("chapter_status") != "ok":
        return {}
    merged = merge_chapter_results(
        state.get("entities") or [],
        state.get("entity_snapshots") or [],
        state.get("relations") or [],
        state.get("timeline_events") or [],
        state.get("locations") or [],
        state.get("foreshadowings") or [],
        state.get("conflicts") or [],
        state.get("rule_checks") or [],
    )
    return {"merged": merged}


def persist_chapter(state: dict) -> dict:
    """节点：标记章节终态（done/failed）+ 产出 chapter_results（供父图归约）。

    LangGraph 学习点：这里的返回会同时
      ① 更新**子图自身**的 chapter_status（单写标量）；
      ② 追加 **chapter_results**（共享 reducer key）→ 子图完成后这条会合并回**父图**。

    TODO 06-07：在 done 分支内补 11 张解构表（entity/relation/timeline/...）的幂等入库
    （resolver 跨章解析 name→entity_id + 单事务 upsert）。
    """
    chapter_id = state["chapter_id"]
    job_id = state["job_id"]
    chapter_status: str = state.get("chapter_status", "failed")
    # 防御性短路（P0-1）：skipped 正常已由路由绕开（_fan_out_agents→END），此处兜底——不落库、不发事件、不产结果
    if chapter_status == "skipped":
        logger.info("persist skip（skipped）| job=%s chapter=%s", job_id, chapter_id)
        return {"chapter_status": "skipped"}
    db = SessionLocal()
    try:
        if chapter_status != "ok":
            # ---- failed 分支：把失败原因落库 + 发布事件 ----
            errors: list[dict] = state.get("errors") or []
            error = "; ".join(f"{e.get('agent')}:{e.get('msg', '')}" for e in errors[:5]) or "validate failed"
            ok = set_chapter_failed(db, job_id, chapter_id, error)
            if not ok:
                # 所有权已丢失（他人重设/并行收尾，P0-1）→ 不发误导事件、不产出 chapter_results
                logger.warning("chapter failed 写入未命中（失去所有权）| job=%s chapter=%s", job_id, chapter_id)
                return {"chapter_status": "failed"}
            events.publish({"type": "chapter_failed", "job_id": job_id, "chapter_id": chapter_id, "error": error})
            logger.warning("chapter failed | %s err=%s", chapter_id, error)
            return {
                "chapter_status": "failed",
                "chapter_results": [{"chapter_id": chapter_id, "status": "failed", "error": error}],
            }
        # ---- done 分支：11 张解构表入库（07，单事务全成或全滚）----
        try:
            merged = state.get("merged") or {
                "entities": state.get("entities") or [],
                "entity_snapshots": state.get("entity_snapshots") or [],
                "relations": state.get("relations") or [],
                "timeline_events": state.get("timeline_events") or [],
                "locations": state.get("locations") or [],
                "foreshadowings": state.get("foreshadowings") or [],
                "conflicts": state.get("conflicts") or [],
                "rule_checks": state.get("rule_checks") or [],
            }
            prev_snapshots = get_prev_snapshots(db, state["book_id"], state.get("chapter_index", 0))
            persist_chapter_tables(
                db,
                book_id=state["book_id"], job_id=job_id, chapter_id=chapter_id,
                chapter_index=state.get("chapter_index", 0),
                chapter_text=state.get("chapter_text") or "",
                merged=merged,
                location_snapshots=state.get("location_snapshots") or [],
                timeline_event_entities=state.get("timeline_event_entities") or [],
                prev_snapshots=prev_snapshots,
                scene_count=state.get("scene_count", 1),
            )
            db.commit()                                   # ★ 单事务：全成或全滚
        except Exception as e:
            db.rollback()
            logger.error("persist 11 表入库失败，回滚 | chapter=%s err=%s", chapter_id, e, exc_info=True)
            error = str(e)[:200]
            set_chapter_failed(db, job_id, chapter_id, error)
            return {"chapter_status": "failed",
                    "chapter_results": [{"chapter_id": chapter_id, "status": "failed", "error": error}]}
        ok = set_chapter_done(db, job_id, chapter_id)     # 状态机：processing → done（乐观锁 P0-1）
        if not ok:
            logger.warning("chapter done 写入未命中（失去所有权）| job=%s chapter=%s", job_id, chapter_id)
            return {"chapter_status": "done"}
        events.publish({"type": "chapter_done", "job_id": job_id, "chapter_id": chapter_id, "status": "done"})
        logger.info("chapter done | %s", chapter_id)
        return {"chapter_status": "done", "chapter_results": [{"chapter_id": chapter_id, "status": "done"}]}
    finally:
        db.close()
