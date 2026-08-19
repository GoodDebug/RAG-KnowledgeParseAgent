# -*- coding: utf-8 -*-
"""
断点续传编排（子任务 11）：校验 job + 复用租约回收复位僵死章 → 返回待续章计数。

不启动执行：续传执行由 `orchestrator.run_job` 负责（`load_chapters` 只挑 pending/failed 章，
kill 重启跑同一 job 即自动续传）。本模块提供"续传前的编排/可观测"，供 retry 端点与运维调用。
"""
from __future__ import annotations

import logging

from novel.persistence import job_state

logger = logging.getLogger("novel.resume")


def resume_job(db, job_id: str) -> dict:
    """续传编排：校验 job 存在 + 复用租约回收复位僵死 processing → 返回待续章计数。

    :param db: SQLAlchemy Session
    :param job_id: djob_{snowflake}
    :return: {job_id, pending, failed, total, status, reaped}
    :raises ValueError: job 不存在
    """
    job = job_state.get_job(db, job_id)
    if job is None:
        raise ValueError(f"job 不存在：{job_id}")
    # 001 P0-3：僵死 processing（超租约）→ 复位 pending，供重新认领（乐观锁闭环）
    reaped = job_state.reap_stale_processing(db, job_id)
    counts = job_state.count_chapter_states(db, job_id)
    total = (counts.get("done", 0) + counts.get("failed", 0)
             + counts.get("pending", 0) + counts.get("processing", 0))
    logger.info("resume_job | job=%s pending=%d failed=%d total=%d reaped=%d",
                job_id, counts.get("pending", 0), counts.get("failed", 0), total, reaped)
    return {
        "job_id": job_id,
        "pending": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "total": total,
        "status": job["status"],
        "reaped": reaped,
    }
