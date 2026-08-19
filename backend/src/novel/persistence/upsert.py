# -*- coding: utf-8 -*-
"""
novel_chapter 幂等写入（INSERT ... ON DUPLICATE KEY UPDATE）。

幂等范式对齐 `LLM/memory_adapters.py`：唯一键（uk_novel_chapter）命中时走"更新分支"，
**保留原 chapter_index**（全局章节序号是稳定性锚点，重传文件不重编号）。
"""
import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from novel.persistence.repositories import get_entity_by_alias
from UTILS.snowflake import snowflake


def upsert_novel_chapter(db, chapter: dict, *, commit: bool = True) -> int:
    """写入/更新一章；返回受影响行数（1=插入，2=更新，0=内容未变）。

    MySQL 的 `ON DUPLICATE KEY UPDATE`：唯一键冲突时按 SET 子句更新，
    返回 2（更新）或 1（插入）——rowcount 可据此判断是新增还是覆盖。

    :param db: SQLAlchemy Session（调用方负责关闭）
    :param chapter: 含 chapter_id/book_id/book_name/file_name/chapter_index/
        chapter_index_in_file/chapter_title/chapter_text/char_offset_start/
        char_offset_end/scene_count 的字典
    """
    stmt = text(
        """
        INSERT INTO novel_chapter
          (chapter_id, book_id, book_name, file_name, chapter_index, chapter_index_in_file,
           chapter_title, chapter_text, char_offset_start, char_offset_end, scene_count)
        VALUES (:chapter_id, :book_id, :book_name, :file_name, :chapter_index, :chapter_index_in_file,
                :chapter_title, :chapter_text, :char_offset_start, :char_offset_end, :scene_count)
        ON DUPLICATE KEY UPDATE
          chapter_title = VALUES(chapter_title), chapter_text = VALUES(chapter_text),
          char_offset_start = VALUES(char_offset_start), char_offset_end = VALUES(char_offset_end),
          scene_count = VALUES(scene_count)
          -- 注意：chapter_index 不更新（保留原全局索引）
        """
    )
    result = db.execute(stmt, chapter)
    if commit:
        db.commit()
    return int(result.rowcount)


def _touch_entity_range(db, book_id: str, entity_id: str, first: int, last: int) -> None:
    """把实体出现章节区间扩到 [MIN(现值, first), MAX(现值, last)]（005 P1-1）。

    实体跨章复用注册时调用：first 取最早出现、last 取最近出现，区间正确收敛。
    """
    db.execute(
        text(
            "UPDATE entity SET first_chapter_index = LEAST(first_chapter_index, :fi), "
            "last_chapter_index = GREATEST(last_chapter_index, :li) "
            "WHERE book_id=:b AND entity_id=:e"
        ),
        {"fi": first, "li": last, "b": book_id, "e": entity_id},
    )


def register_entity(db, book_id: str, entity: dict, *, first_chapter_index: int = 0,
                    last_chapter_index: int = 0, commit: bool = True) -> str:
    """注册实体（含别名）→ 返回 entity_id（子任务 03，注册去重基元）。

    别名消解逻辑（**去重率 0**）：
      1. 按实体的任一别名（含规范名）查 `entity_alias`：
          命中 → 复用已有 entity_id（同一实体跨章节不新建），并把出现区间扩到 [min, max]；
      2. 未命中 → 新建 entity（entity_id = ent_{snowflake}）+ 写入全部别名；
      3. 并发撞车（另一进程同书注册了该别名）→ `uk_alias_book_alias` IntegrityError →
         回滚重查，复用对方 entity_id（幂等范式）。

    005 P1-1：`first_chapter_index/last_chapter_index` 由 persist 注入当前章（0-based 全局章），
    不再硬编码 0；复用已注册实体时 first 取 MIN、last 取 MAX（跨章出现区间收敛）。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param entity: 含 name / aliases[] / type / description 的字典（entity_agent 抽取结果）
    :param first_chapter_index: 本次出现章节（0-based）
    :param last_chapter_index: 本次出现章节（同 first；跨章复用后 MAX 收敛）
    :return: entity_id（命中复用或新建）
    """
    name = str(entity.get("name", "")).strip()
    aliases = list(dict.fromkeys([name] + [str(a).strip() for a in (entity.get("aliases") or [])]))
    aliases = [a for a in aliases if a]

    # 1. 任一别名已注册 → 复用其 entity_id（去重率 0 的关键），并扩展出现区间
    for alias in aliases:
        existing = get_entity_by_alias(db, book_id, alias)
        if existing:
            _touch_entity_range(db, book_id, existing, first_chapter_index, last_chapter_index)
            if commit:
                db.commit()
            return existing

    # 2. 未命中 → 新建实体 + 写别名
    entity_id = f"ent_{snowflake.generate()}"
    entity_type = str(entity.get("type", "rule")).strip() or "rule"
    description = str(entity.get("description", "")).strip()
    try:
        db.execute(
            text(
                "INSERT INTO entity (entity_id, entity_name, entity_type, description, book_id, "
                "first_chapter_index, last_chapter_index) "
                "VALUES (:eid, :name, :etype, :desc, :bid, :fi, :li)"
            ),
            {"eid": entity_id, "name": name, "etype": entity_type, "desc": description,
             "bid": book_id, "fi": first_chapter_index, "li": last_chapter_index},
        )
        for alias in aliases:
            db.execute(
                text(
                    "INSERT INTO entity_alias (entity_id, alias_name, alias_type, book_id) "
                    "VALUES (:eid, :alias, 'nickname', :bid)"
                ),
                {"eid": entity_id, "alias": alias, "bid": book_id},
            )
        if commit:
            db.commit()
        return entity_id
    except IntegrityError:
        # 3. 并发撞车：另一进程已注册该别名 → 回滚后复用其 entity_id（并扩展区间）
        db.rollback()
        for alias in aliases:
            existing = get_entity_by_alias(db, book_id, alias)
            if existing:
                _touch_entity_range(db, book_id, existing, first_chapter_index, last_chapter_index)
                if commit:
                    db.commit()
                return existing
        raise


# ====================== 快照/关系/时间线 幂等 upsert（子任务 04） ======================
# 说明：本子任务只做"持久化基元"（uk + ON DUPLICATE KEY UPDATE），
#       图内 persist_chapter 调用（11 表入库接线）在 07；parent_event_id 跨章解析在 06。

def upsert_entity_snapshot(db, snapshot: dict, *, commit: bool = True) -> int:
    """幂等写实体快照（uk: book_id, entity_id, chapter_index）。

    :param db: SQLAlchemy Session
    :param snapshot: 含 snapshot_id/entity_id/entity_name/entity_type/status_desc/attributes/
        book_id/chapter_index/source_chunk_ids 的字典（attributes 为 dict，自动 json 序列化）
    :return: 受影响行数（1 插入 / 2 更新 / 0 未变）
    """
    stmt = text(
        """
        INSERT INTO entity_snapshot
          (snapshot_id, entity_id, entity_name, entity_type, status_desc, attributes,
           book_id, chapter_index, source_chunk_ids)
        VALUES (:snapshot_id, :entity_id, :entity_name, :entity_type, :status_desc, :attributes,
                :book_id, :chapter_index, :source_chunk_ids)
        ON DUPLICATE KEY UPDATE
          entity_name=VALUES(entity_name), status_desc=VALUES(status_desc), attributes=VALUES(attributes)
        """
    )
    result = db.execute(stmt, {
        "snapshot_id": snapshot.get("snapshot_id", ""),
        "entity_id": snapshot["entity_id"],
        "entity_name": snapshot.get("entity_name", ""),
        "entity_type": snapshot.get("entity_type", "rule"),
        "status_desc": snapshot.get("status_desc", ""),
        "attributes": json.dumps(snapshot.get("attributes", {}), ensure_ascii=False),
        "book_id": snapshot["book_id"],
        "chapter_index": snapshot.get("chapter_index", 0),
        "source_chunk_ids": snapshot.get("source_chunk_ids"),
    })
    if commit:
        db.commit()
    return int(result.rowcount)


def upsert_entity_relation(db, relation: dict, *, commit: bool = True) -> int:
    """幂等写实体关系（uk: book_id, source_entity_id, target_entity_id, relation_type, start_chapter）。"""
    stmt = text(
        """
        INSERT INTO entity_relation
          (relation_id, source_entity_id, source_entity_type, target_entity_id, target_entity_type,
           relation_type, relation_desc, relation_weight, valid_period, start_chapter, end_chapter,
           surface_relation, inner_relation, relation_trend,
           book_id, chapter_index, source_chunk_ids)
        VALUES (:relation_id, :source_entity_id, :source_entity_type, :target_entity_id, :target_entity_type,
                :relation_type, :relation_desc, :relation_weight, :valid_period, :start_chapter, :end_chapter,
                :surface_relation, :inner_relation, :relation_trend,
                :book_id, :chapter_index, :source_chunk_ids)
        ON DUPLICATE KEY UPDATE
          relation_desc=VALUES(relation_desc), relation_weight=VALUES(relation_weight),
          valid_period=VALUES(valid_period), end_chapter=VALUES(end_chapter),
          surface_relation=VALUES(surface_relation), inner_relation=VALUES(inner_relation),
          relation_trend=VALUES(relation_trend)
        """
    )
    result = db.execute(stmt, {
        "relation_id": relation.get("relation_id", ""),
        "source_entity_id": relation["source_entity_id"],
        "source_entity_type": relation.get("source_entity_type", "human"),
        "target_entity_id": relation["target_entity_id"],
        "target_entity_type": relation.get("target_entity_type", "human"),
        "relation_type": relation["relation_type"],
        "relation_desc": relation.get("relation_desc", ""),
        "relation_weight": relation.get("relation_weight", 2),
        "valid_period": relation.get("valid_period", "temporary"),
        "start_chapter": relation.get("start_chapter", 0),
        "end_chapter": relation.get("end_chapter", 0),
        # 二阶段 03：明暗两层 + 趋势（L4 关系质感）
        "surface_relation": relation.get("surface_relation", ""),
        "inner_relation": relation.get("inner_relation", ""),
        "relation_trend": relation.get("relation_trend", "稳定"),
        "book_id": relation["book_id"],
        "chapter_index": relation.get("chapter_index", 0),
        "source_chunk_ids": relation.get("source_chunk_ids"),
    })
    if commit:
        db.commit()
    return int(result.rowcount)


def upsert_timeline_event(db, event: dict, *, commit: bool = True) -> int:
    """幂等写时间线事件（uk: book_id, event_id）。

    parent_event_id 由调用方（06 跨章 resolver）先解析再传入；本函数只做幂等写入。
    """
    stmt = text(
        """
        INSERT INTO timeline_event
          (event_id, event_level, parent_event_id, event_title, event_content, time_desc,
           global_sort, start_chapter, end_chapter, location_id, narrative_type, plot_impact,
           book_id, source_chunk_ids)
        VALUES (:event_id, :event_level, :parent_event_id, :event_title, :event_content, :time_desc,
                :global_sort, :start_chapter, :end_chapter, :location_id, :narrative_type, :plot_impact,
                :book_id, :source_chunk_ids)
        ON DUPLICATE KEY UPDATE
          event_title=VALUES(event_title), event_content=VALUES(event_content),
          global_sort=VALUES(global_sort), start_chapter=VALUES(start_chapter), end_chapter=VALUES(end_chapter),
          narrative_type=VALUES(narrative_type), plot_impact=VALUES(plot_impact)
        """
    )
    result = db.execute(stmt, {
        "event_id": event["event_id"],
        "event_level": event.get("event_level", "event"),
        "parent_event_id": event.get("parent_event_id"),
        "event_title": event.get("event_title", ""),
        "event_content": event.get("event_content", ""),
        "time_desc": event.get("time_desc", ""),
        "global_sort": event.get("global_sort", 0),
        "start_chapter": event.get("start_chapter", 0),
        "end_chapter": event.get("end_chapter", 0),
        "location_id": event.get("location_id"),
        # 二阶段 03：叙事类型 + 剧情作用（L4 叙事功能）
        "narrative_type": event.get("narrative_type", ""),
        "plot_impact": event.get("plot_impact", ""),
        "book_id": event["book_id"],
        "source_chunk_ids": event.get("source_chunk_ids"),
    })
    if commit:
        db.commit()
    return int(result.rowcount)


def upsert_timeline_event_entity(db, event_entity: dict, *, commit: bool = True) -> int:
    """幂等写事件↔实体关联（uk: event_id, entity_id）。"""
    stmt = text(
        """
        INSERT INTO timeline_event_entity (event_id, entity_id, role, book_id, chapter_index)
        VALUES (:event_id, :entity_id, :role, :book_id, :chapter_index)
        ON DUPLICATE KEY UPDATE role=VALUES(role)
        """
    )
    result = db.execute(stmt, {
        "event_id": event_entity["event_id"],
        "entity_id": event_entity["entity_id"],
        "role": event_entity.get("role", "在场"),
        "book_id": event_entity["book_id"],
        "chapter_index": event_entity.get("chapter_index", 0),
    })
    if commit:
        db.commit()
    return int(result.rowcount)


# ====================== 地点/伏笔/冲突/规则 幂等 upsert（子任务 05） ======================
# 说明：本子任务只做"持久化基元"（uk + ON DUPLICATE KEY UPDATE）；persist 接线在 07。
#       setup_event_id（事件标题→event_id）、subject_entity_id（实体名→entity_id）由 06 resolver 解析后传入。

def register_location(db, book_id: str, location: dict, *, first_chapter_index: int = 0,
                      last_chapter_index: int = 0, commit: bool = True) -> str:
    """注册地点（复用 04 location 表）→ 返回 location_id（同书按名去重，register_entity 同模式）。

    005 P1-1：`first/last_chapter_index` 由 persist 注入当前章（不再硬编码 0）；
    复用同名地点时区间扩到 [MIN, MAX]（跨章出现收敛）。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param location: 含 name/level/parent_name/description 的字典（location_agent 抽取结果）
    :param first_chapter_index: 本次出现章节（0-based）
    :param last_chapter_index: 本次出现章节（跨章复用后 MAX 收敛）
    :return: location_id（命中复用或新建）
    """
    name = str(location.get("name", "")).strip()
    if not name:
        raise ValueError("location.name 不能为空")
    # 1. 同书同名已有 → 复用 location_id（去重），并把出现区间扩到 [MIN, MAX]
    existing = db.execute(
        text("SELECT location_id FROM location WHERE book_id=:b AND location_name=:n"),
        {"b": book_id, "n": name},
    ).scalar()
    if existing:
        db.execute(
            text(
                "UPDATE location SET first_chapter_index = LEAST(first_chapter_index, :fi), "
                "last_chapter_index = GREATEST(last_chapter_index, :li) "
                "WHERE book_id=:b AND location_id=:e"
            ),
            {"fi": first_chapter_index, "li": last_chapter_index, "b": book_id, "e": existing},
        )
        if commit:
            db.commit()
        return existing
    # 2. 未命中 → 新建（parent 层级树由 05/06 在后续章节补挂）
    location_id = f"loc_{snowflake.generate()}"
    db.execute(
        text(
            "INSERT INTO location (location_id, location_name, location_level, description, "
            "book_id, first_chapter_index, last_chapter_index) "
            "VALUES (:lid, :name, :level, :desc, :bid, :fi, :li)"
        ),
        {
            "lid": location_id, "name": name,
            "level": int(location.get("level", 4)),
            "desc": str(location.get("description", "")).strip(),
            "bid": book_id,
            "fi": first_chapter_index, "li": last_chapter_index,
        },
    )
    if commit:
        db.commit()
    return location_id


def upsert_location_snapshot(db, snapshot: dict, *, commit: bool = True) -> int:
    """幂等写地点快照（uk: book_id, location_id, chapter_index）。"""
    stmt = text(
        """
        INSERT INTO location_snapshot (snapshot_id, location_id, location_name, status_desc,
            special_rules, book_id, chapter_index, source_chunk_ids)
        VALUES (:snapshot_id, :location_id, :location_name, :status_desc,
                :special_rules, :book_id, :chapter_index, :source_chunk_ids)
        ON DUPLICATE KEY UPDATE
          status_desc=VALUES(status_desc), special_rules=VALUES(special_rules)
        """
    )
    result = db.execute(stmt, {
        "snapshot_id": snapshot.get("snapshot_id", ""),
        "location_id": snapshot["location_id"],
        "location_name": snapshot.get("location_name", ""),
        "status_desc": snapshot.get("status_desc", ""),
        "special_rules": snapshot.get("special_rules", ""),
        "book_id": snapshot["book_id"],
        "chapter_index": snapshot.get("chapter_index", 0),
        "source_chunk_ids": snapshot.get("source_chunk_ids"),
    })
    if commit:
        db.commit()
    return int(result.rowcount)


def upsert_foreshadowing(db, fs: dict, *, commit: bool = True) -> int:
    """幂等写伏笔（uk: book_id, foreshadowing_id）。

    setup_event_id / reveal_event_id 由 06 resolver 按事件标题解析后传入。
    """
    stmt = text(
        """
        INSERT INTO foreshadowing (foreshadowing_id, book_id, title, description, setup_chapter,
            setup_event_id, involved_entity_ids, reveal_chapter, reveal_event_id, status,
            related_foreshadowing_ids, foreshadowing_type, concealment_level, misleading_info,
            source_chunk_ids)
        VALUES (:fs_id, :book_id, :title, :description, :setup_chapter,
                :setup_event_id, :involved_entity_ids, :reveal_chapter, :reveal_event_id, :status,
                :related_foreshadowing_ids, :foreshadowing_type, :concealment_level, :misleading_info,
                :source_chunk_ids)
        ON DUPLICATE KEY UPDATE
          title=VALUES(title), description=VALUES(description),
          status=VALUES(status), reveal_chapter=VALUES(reveal_chapter),
          foreshadowing_type=VALUES(foreshadowing_type), concealment_level=VALUES(concealment_level),
          misleading_info=VALUES(misleading_info)
        """
    )
    result = db.execute(stmt, {
        "fs_id": fs["foreshadowing_id"],
        "book_id": fs["book_id"],
        "title": fs.get("title", ""),
        "description": fs.get("description", ""),
        "setup_chapter": fs.get("setup_chapter", 0),
        "setup_event_id": fs.get("setup_event_id"),
        "involved_entity_ids": json.dumps(fs.get("involved_entity_ids", []), ensure_ascii=False),
        "reveal_chapter": fs.get("reveal_chapter"),
        "reveal_event_id": fs.get("reveal_event_id"),
        "status": fs.get("status", "pending"),
        "related_foreshadowing_ids": json.dumps(fs.get("related_foreshadowing_ids", []), ensure_ascii=False),
        # 二阶段 03：伏笔类型 + 隐蔽度 + 误导（L4 悬念感）
        "foreshadowing_type": fs.get("foreshadowing_type", ""),
        "concealment_level": fs.get("concealment_level"),
        "misleading_info": fs.get("misleading_info", ""),
        "source_chunk_ids": fs.get("source_chunk_ids"),
    })
    if commit:
        db.commit()
    return int(result.rowcount)


def upsert_story_conflict(db, conflict: dict, *, commit: bool = True) -> int:
    """幂等写冲突（uk: book_id, conflict_id）；escalated_event_ids JSON 序列化。"""
    stmt = text(
        """
        INSERT INTO story_conflict (conflict_id, book_id, conflict_title, conflict_type, conflict_desc,
            side_a, side_b, start_chapter, end_chapter, current_status, escalated_event_ids, source_chunk_ids)
        VALUES (:conflict_id, :book_id, :conflict_title, :conflict_type, :conflict_desc,
                :side_a, :side_b, :start_chapter, :end_chapter, :current_status, :escalated_event_ids, :source_chunk_ids)
        ON DUPLICATE KEY UPDATE
          conflict_title=VALUES(conflict_title), current_status=VALUES(current_status),
          end_chapter=VALUES(end_chapter)
        """
    )
    result = db.execute(stmt, {
        "conflict_id": conflict["conflict_id"],
        "book_id": conflict["book_id"],
        "conflict_title": conflict.get("conflict_title", ""),
        "conflict_type": conflict.get("conflict_type", ""),
        "conflict_desc": conflict.get("conflict_desc", ""),
        "side_a": conflict.get("side_a", ""),
        "side_b": conflict.get("side_b", ""),
        "start_chapter": conflict.get("start_chapter", 0),
        "end_chapter": conflict.get("end_chapter"),
        "current_status": conflict.get("current_status", "升级"),
        "escalated_event_ids": json.dumps(conflict.get("escalated_event_ids", []), ensure_ascii=False),
        "source_chunk_ids": conflict.get("source_chunk_ids"),
    })
    if commit:
        db.commit()
    return int(result.rowcount)


def upsert_rule_check(db, rule: dict, *, commit: bool = True) -> int:
    """幂等写规则校验点（uk: book_id, rule_id）。

    subject_entity_id 由 06 resolver 按实体名解析后传入。
    """
    stmt = text(
        """
        INSERT INTO rule_check (rule_id, book_id, rule_name, rule_type, rule_content,
            subject_entity_id, subject_ability, valid_from_chapter, valid_to_chapter,
            last_check_result, source_chunk_ids)
        VALUES (:rule_id, :book_id, :rule_name, :rule_type, :rule_content,
                :subject_entity_id, :subject_ability, :valid_from_chapter, :valid_to_chapter,
                :last_check_result, :source_chunk_ids)
        ON DUPLICATE KEY UPDATE
          rule_name=VALUES(rule_name), rule_content=VALUES(rule_content),
          rule_type=VALUES(rule_type), last_check_result=VALUES(last_check_result)
        """
    )
    result = db.execute(stmt, {
        "rule_id": rule["rule_id"],
        "book_id": rule["book_id"],
        "rule_name": rule.get("rule_name", ""),
        "rule_type": rule.get("rule_type", "other"),
        "rule_content": rule.get("rule_content", ""),
        "subject_entity_id": rule.get("subject_entity_id"),
        "subject_ability": rule.get("subject_ability", ""),
        "valid_from_chapter": rule.get("valid_from_chapter", 1),
        "valid_to_chapter": rule.get("valid_to_chapter", 0),
        "last_check_result": rule.get("last_check_result"),
        "source_chunk_ids": rule.get("source_chunk_ids"),
    })
    if commit:
        db.commit()
    return int(result.rowcount)
