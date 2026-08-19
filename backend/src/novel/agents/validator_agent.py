# -*- coding: utf-8 -*-
"""
Layer 2 一致性校验 Agent（子任务 08）—— LLM 批校验，产出疑点 flags。

职责：读该书一批已入库抽取记录（关系/快照/事件），调 LLM 检查**语义冲突**
（人物生死 / 关系方向 / 时间错位 / 阶段标记），产出疑点 → 写 `validation_issue(pending)`。

**只作 flags，绝不作 auto-fix**：validator 输出一律进 validation_issue，绝不直接改 11 表；
只有人工确认后才走 re-persist（00 §4.f 核心纪律）。

与 8 个解构 agent 不同：validator **不入 ChapterGraph 扇出**（不逐章跑），
由 `validate_book` 在书级按 `NOVEL_VALIDATOR_ENABLED` 触发、按书批处理（控成本）。
"""
import json
import logging

from sqlalchemy import text

import novel.llm_runner as llm_runner
from novel.config import novel_validator_batch, novel_validator_enabled
from novel.persistence.validation import create_validation_issue

logger = logging.getLogger("novel.validator")


def _build_batch(db, book_id: str, limit: int) -> str:
    """构造待查批次文本：取该书最近已入库的关系/快照/事件（≤limit 条）。"""
    rels = db.execute(
        text("SELECT relation_id, source_entity_id, target_entity_id, relation_type, relation_desc "
             "FROM entity_relation WHERE book_id=:b ORDER BY id DESC LIMIT :l"),
        {"b": book_id, "l": limit},
    ).mappings().all()
    snaps = db.execute(
        text("SELECT entity_name, status_desc FROM entity_snapshot WHERE book_id=:b ORDER BY id DESC LIMIT :l"),
        {"b": book_id, "l": limit},
    ).mappings().all()
    evs = db.execute(
        text("SELECT event_id, event_title, event_content, time_desc "
             "FROM timeline_event WHERE book_id=:b ORDER BY id DESC LIMIT :l"),
        {"b": book_id, "l": limit},
    ).mappings().all()
    return json.dumps({
        "relations": [dict(r) for r in rels],
        "snapshots": [dict(s) for s in snaps],
        "events": [dict(e) for e in evs],
    }, ensure_ascii=False)


def run_book_validator(db, book_id: str) -> int:
    """按书批处理 Layer 2 校验：分批 → LLM 产出疑点 → 写 validation_issue（只作 flags）。

    :param db: SQLAlchemy Session（调用方负责 commit）
    :param book_id: doc_{user_id}_{doc_id}
    :return: 新增疑点数（开关关闭 → 0）
    """
    if not novel_validator_enabled():
        logger.info("validator 关闭（NOVEL_VALIDATOR_ENABLED=0）| book=%s", book_id)
        return 0
    limit = novel_validator_batch()
    batch_text = _build_batch(db, book_id, limit)
    try:
        findings = llm_runner.extract("validator", batch_text, 0)
    except llm_runner.LLMExtractError as e:
        logger.error("validator LLM 失败 | book=%s err=%s", book_id, e)
        return 0
    n = 0
    for f in findings:
        create_validation_issue(db, {
            "book_id": book_id,
            "record_type": f.get("record_type", ""),
            "issue_type": "semantic_conflict",
            "severity": f.get("severity", "warning"),
            "description": f.get("conflict_desc", ""),
            "suggested_value": f.get("suggested_value", ""),
        })
        n += 1
    logger.info("validator | book=%s 疑点=%d", book_id, n)
    return n
