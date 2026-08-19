# -*- coding: utf-8 -*-
"""
上传解构入口（子任务 09）：切章建 job → 供 documents.py 并行编排。

职责：上传 `deconstruct=1` 时，把「切章写 novel_chapter + 建 deconstruct_job/chapter_state」组合成一步，
返回 `(job_id, chapter_meta)` —— 之后 `_process_document_upload` 用 `asyncio.gather` 与 Milvus 入库并行跑 `orchestrator.run_job`。

设计：`extract_and_persist`（01）只写 novel_chapter 不建 job；本模块组合 `create_job`/`add_chapter_states`（02）
补齐 job 创建，保持 01/02 交付物只读。
"""
from __future__ import annotations

import logging

from novel.persistence import job_state
from novel.pipeline import chapters

logger = logging.getLogger("novel.upload")


def prepare_deconstruct_job(db, file_paths: list[str], book_id: str,
                            book_name: str, user_id: int) -> tuple[str, list[dict]]:
    """切章写 novel_chapter + 建解构 job（pending×N）→ 返回 (job_id, chapter_meta)。

    :param db: SQLAlchemy Session（调用方 `_process_document_upload` 传入）
    :param file_paths: 上传暂存文件路径列表
    :param book_id: doc_{user_id}_{doc_id}
    :param book_name: 书名（解构分组）
    :param user_id: 归属用户
    :return: (job_id, chapter_meta) —— job_id 供 orchestrator.run_job 启动解构
    """
    # 1) 切章写 novel_chapter（同步 DB 写，调用方在 asyncio.to_thread 中执行）
    meta = chapters.extract_and_persist(file_paths, book_id, book_name, user_id, db)
    # 2) 建解构 job（pending）+ 章节状态（pending×N）
    job_id = job_state.create_job(
        db, book_id=book_id, user_id=user_id, trigger_type="upload", total=len(meta))
    job_state.add_chapter_states(db, job_id, meta)
    logger.info("prepare_deconstruct_job | book=%s job=%s chapters=%d", book_id, job_id, len(meta))
    return job_id, meta
