# -*- coding: utf-8 -*-
"""
deconstruct_job / deconstruct_chapter_state 状态机（子任务 02）。

任务状态机：pending → running → done / failed
章节状态机：pending → processing → done / failed    

这是一层"轻量 SQL 封装"（repositories 风格）：各函数接收 `db`（SQLAlchemy Session），
内部用原生 SQL + `:params` 绑定参数（防注入），每条语句后 `commit()`。
幂等约定：`uk_chapter_state(job_id, chapter_id)` + `ON DUPLICATE KEY UPDATE` 支持重跑。
"""
from sqlalchemy import text

from UTILS.snowflake import snowflake

# 任务表 deconstruct_job 操作函数

def create_job(db, *, book_id: str, user_id: int, trigger_type: str = "upload",
               total: int = 0) -> str:
    """创建解构任务（status=pending），返回 job_id。

    job_id 用雪花 ID（`UTILS.snowflake.snowflake.generate()`）—— 全局唯一、趋势递增。
    """
    job_id = f"djob_{snowflake.generate()}"
    db.execute(
        text(
            "INSERT INTO deconstruct_job (job_id, book_id, user_id, trigger_type, total_chapters, status) "
            "VALUES (:j, :b, :u, :t, :total, 'pending')"
        ),
        {"j": job_id, "b": book_id, "u": user_id, "t": trigger_type, "total": total},
    )
    db.commit()
    return job_id


def get_job(db, job_id: str) -> dict | None:
    """按 job_id 读任务行（返回 dict，无则 None）。"""
    row = db.execute(
        text("SELECT * FROM deconstruct_job WHERE job_id = :j"), {"j": job_id}
    ).mappings().one_or_none()
    return dict(row) if row else None


def set_job_running(db, job_id: str) -> None:
    """状态机：pending → running（load_chapters 调用）。"""
    db.execute(text("UPDATE deconstruct_job SET status='running' WHERE job_id=:j"), {"j": job_id})
    db.commit()


def bump_job_counts(db, job_id: str, done: int, failed: int) -> None:
    """归约递增 done/failed 计数（由 aggregate 节点调用，**单次**避免并行写同一行）。"""
    db.execute(
        text(
            "UPDATE deconstruct_job "
            "SET done_chapters = done_chapters + :d, failed_chapters = failed_chapters + :f "
            "WHERE job_id = :j"
        ),
        {"d": done, "f": failed, "j": job_id},
    )
    db.commit()


def finalize_job(db, job_id: str, status: str, error: str | None = None) -> None:
    """置任务终态（done/failed）+ finished_at + 可选 error_msg。"""
    db.execute(
        text(
            "UPDATE deconstruct_job SET status=:s, finished_at=NOW(), error_msg=:e "
            "WHERE job_id=:j"
        ),
        {"s": status, "e": error, "j": job_id},
    )
    db.commit()


# 章节状态表 deconstruct_chapter_state 操作函数

def add_chapter_states(db, job_id: str, chapters: list[dict]) -> None:
    """为该 job 建章节状态行（status=pending）。

    幂等：重复键（uk_job_chapter）命中时 `ON DUPLICATE KEY UPDATE status='pending'`，
    把已 done/failed 的行"重置回 pending"→ 支持整书重跑（子任务 10 续传语义）。
    """
    for ch in chapters:
        db.execute(
            text(
                "INSERT INTO deconstruct_chapter_state "
                "  (job_id, chapter_id, book_id, chapter_index, scene_count, status) "
                "VALUES (:j, :c, :b, :ci, :sc, 'pending') "
                "ON DUPLICATE KEY UPDATE status='pending'"
            ),
            {
                "j": job_id,
                "c": ch["chapter_id"],
                "b": ch.get("book_id", ""),
                "ci": ch.get("chapter_index", 0),
                "sc": ch.get("scene_count", 1),
            },
        )
    db.commit()


def set_chapter_processing(db, job_id: str, chapter_id: str) -> bool:
    """章节状态机：pending/failed → processing + 记录 started_at（chapter_prepare 调用）。

    乐观锁（P0-1）：`AND status IN ('pending','failed')` 原子判等——同一 (job, chapter) 的并发抢占
    只有一个 UPDATE 命中（rowcount=1），其余 WHERE 不命中（rowcount=0）→ 返回 False。
    返回 True=认领成功（本进程持有本章所有权）；False=已被其他进程认领，调用方应跳过本章。
    守卫含 'failed'：保留断点续传/重试"补 failed 章"的语义（顶层 Spec 已钉死 #3）。
    """
    result = db.execute(
        text(
            "UPDATE deconstruct_chapter_state SET status='processing', started_at=NOW() "
            "WHERE job_id=:j AND chapter_id=:c AND status IN ('pending','failed')"
        ),
        {"j": job_id, "c": chapter_id},
    )
    db.commit()
    return result.rowcount > 0


def set_chapter_done(db, job_id: str, chapter_id: str) -> bool:
    """章节状态机：processing → done + 清空 error_msg（persist_chapter 调用）。

    乐观锁（P0-1）：守卫 `AND status='processing'` —— 只有认领持有者能收尾。
    返回 True=成功落 done；False=当前非 processing（无所有权/已被重置），不应发事件/产结果。
    """
    result = db.execute(
        text(
            "UPDATE deconstruct_chapter_state SET status='done', finished_at=NOW(), error_msg=NULL "
            "WHERE job_id=:j AND chapter_id=:c AND status='processing'"
        ),
        {"j": job_id, "c": chapter_id},
    )
    db.commit()
    return result.rowcount > 0


def set_chapter_failed(db, job_id: str, chapter_id: str, error: str) -> bool:
    """章节状态机：processing → failed + 记录 error_msg（persist_chapter 调用）。

    乐观锁（P0-1）：守卫 `AND status='processing'` —— 只有认领持有者能标记失败。
    返回 bool（同上语义）。
    """
    result = db.execute(
        text(
            "UPDATE deconstruct_chapter_state SET status='failed', finished_at=NOW(), error_msg=:e "
            "WHERE job_id=:j AND chapter_id=:c AND status='processing'"
        ),
        {"j": job_id, "c": chapter_id, "e": error},
    )
    db.commit()
    return result.rowcount > 0


def count_chapter_states(db, job_id: str) -> dict[str, int]:
    """统计该 job 各章节状态的计数 {status: count}（finalize_job 定终态的依据）。"""
    rows = db.execute(
        text(
            "SELECT status, COUNT(*) AS c FROM deconstruct_chapter_state "
            "WHERE job_id=:j GROUP BY status"
        ),
        {"j": job_id},
    ).mappings().all()
    return {r["status"]: int(r["c"]) for r in rows}


def reap_stale_processing(db, job_id: str, lease_seconds: int = 1800) -> int:
    """租约超时回收（P0-3）：把 started_at 超过阈值、仍 processing 的章复位为 pending。

    背景：乐观锁保证"一章只被一个 worker 认领"，但认领它的 worker 若中途崩溃，
    章节会永远卡在 processing（load_chapters 只挑 pending/failed，finalize 见 processing 延迟终态）
    → job 永久挂死。本函数是乐观锁的闭环：复位后经 `IN ('pending','failed')` 被重新认领 = 自动重试。

    复用乐观锁纪律：仅 `status='processing'` 且超时才复位（幂等，多 worker 并发安全——
    两个 reap 同时跑复位同一行无害；随后经乐观锁只能一个 worker 重认领）。
    返回复位行数。
    """
    result = db.execute(
        text(
            "UPDATE deconstruct_chapter_state "
            "SET status='pending', error_msg='lease_expired' "
            "WHERE job_id=:j AND status='processing' "
            "  AND started_at < NOW() - INTERVAL :sec SECOND"
        ),
        {"j": job_id, "sec": lease_seconds},
    )
    db.commit()
    return result.rowcount


def reset_chapters_to_pending(db, job_id: str, chapter_ids: list[str] | None = None,
                              bump_retry: bool = True) -> int:
    """重试复位（子任务 11）：把指定（或缺省=全部）failed 章复位为 pending，retry_count+1、shrink_level=0。

    幂等纪律：仅 `status='failed'` 复位（不碰 pending/processing/done），多 worker 并发安全——
    重复 retry 已 pending 的章不重复计数。

    :param db: SQLAlchemy Session
    :param job_id: djob_{snowflake}
    :param chapter_ids: 要重试的章；None → 该 job 全部 failed 章
    :param bump_retry: 是否递增 retry_count（默认 True）
    :return: 被复位的章数
    """
    sql = ("UPDATE deconstruct_chapter_state SET status='pending', shrink_level=0, "
           "retry_count=retry_count+1 WHERE job_id=:j AND status='failed'")
    params: dict = {"j": job_id}
    if chapter_ids:
        sql += " AND chapter_id IN :ids"
        params["ids"] = tuple(chapter_ids)
    result = db.execute(text(sql), params)
    db.commit()
    return int(result.rowcount)
