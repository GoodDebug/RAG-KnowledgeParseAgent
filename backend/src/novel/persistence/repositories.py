# -*- coding: utf-8 -*-
"""
novel_chapter 表读写封装（repositories 层）。

把对 novel_chapter 表的 SQL 查询收拢成具名函数，供上层（chapters 管线 / 图节点）复用；
各函数接收 `db`（SQLAlchemy Session），用 `text(...)` + `:param` 绑定参数（防注入）。
"""
import json

from sqlalchemy import text


def get_max_chapter_index(db, book_id: str) -> int:
    """返回该书当前最大全局章节序号；无记录返回 -1。

    用途：全局章节序号分配的 base —— 新章节从 base+1 起递增；
    `COALESCE(MAX(chapter_index), -1)` 让空表也能返回一个确定值。
    """
    row = db.execute(
        text(
            "SELECT COALESCE(MAX(chapter_index), -1) FROM novel_chapter "
            "WHERE book_id = :b"
        ),
        {"b": book_id},
    ).scalar()
    return int(row)


def list_chapters(db, book_id: str) -> list[dict]:
    """按全局序号升序列出章节元数据。

    注意：**不含 chapter_text**（MEDIUMTEXT 大对象）——列表场景只取元数据，避免大对象拖慢查询；
    需要原文时按 chapter_id 单独读。
    """
    rows = db.execute(
        text(
            "SELECT chapter_id, book_id, book_name, file_name, chapter_index, "
            "chapter_index_in_file, chapter_title, char_offset_start, "
            "char_offset_end, scene_count "
            "FROM novel_chapter WHERE book_id = :b ORDER BY chapter_index"
        ),
        {"b": book_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_entity_by_alias(db, book_id: str, alias_name: str) -> str | None:
    """按别名查同书实体的 entity_id（无则 None）。

    别名消解的查询核心：register_entity 靠它做"同书同别名 → 复用同一 entity_id"。
    """
    return db.execute(
        text("SELECT entity_id FROM entity_alias WHERE book_id=:b AND alias_name=:a"),
        {"b": book_id, "a": alias_name},
    ).scalar()


def get_entity(db, entity_id: str) -> dict | None:
    """按 entity_id 读实体行。"""
    row = db.execute(
        text("SELECT * FROM entity WHERE entity_id=:e"), {"e": entity_id}
    ).mappings().one_or_none()
    return dict(row) if row else None


def list_entity_names(db, book_id: str) -> list[str]:
    """跨章命名全量名单（子任务 06）：查该书 `entity` 表全部规范名。

    供 003 P1-1 的 `hint_entities` 注入（只放规范名控 token）。
    """
    rows = db.execute(
        text("SELECT entity_name FROM entity WHERE book_id=:b ORDER BY entity_name"),
        {"b": book_id},
    ).scalars().all()
    return [r for r in rows if r]


def get_event_by_title(db, book_id: str, title: str) -> str | None:
    """按同书 `timeline_event.event_title` 查 event_id（子任务 06，跨章 stage/事件复用）。"""
    return db.execute(
        text("SELECT event_id FROM timeline_event WHERE book_id=:b AND event_title=:t"),
        {"b": book_id, "t": title},
    ).scalar()


# ====================== 子任务 10：API 查询 ======================

def list_jobs(db, book_id: str) -> list[dict]:
    """该书解构任务列表（最新在前，子任务 10）。"""
    rows = db.execute(
        text("SELECT job_id, trigger_type, status, total_chapters, done_chapters, failed_chapters, "
             "started_at, finished_at FROM deconstruct_job WHERE book_id=:b "
             "ORDER BY id DESC"),
        {"b": book_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_job_chapters(db, job_id: str) -> list[dict]:
    """job 的章节状态列表（联 novel_chapter 取标题，子任务 10）。"""
    rows = db.execute(
        text("SELECT cs.chapter_id, cs.chapter_index, nc.chapter_title, cs.status, "
             "cs.scene_count, cs.retry_count, cs.shrink_level, cs.error_msg "
             "FROM deconstruct_chapter_state cs "
             "JOIN novel_chapter nc ON nc.chapter_id = cs.chapter_id "
             "WHERE cs.job_id=:j ORDER BY cs.chapter_index"),
        {"j": job_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_entity_snapshots_by_name(db, book_id: str, entity_name: str,
                                 chapter: int | None = None) -> list[dict]:
    """实体快照（可选按章过滤，子任务 10 query）。"""
    sql = ("SELECT s.snapshot_id, s.entity_name, s.entity_type, s.status_desc, s.attributes, "
           "s.chapter_index FROM entity_snapshot s "
           "JOIN entity e ON e.entity_id = s.entity_id "
           "WHERE e.book_id=:b AND s.entity_name=:n")
    params: dict = {"b": book_id, "n": entity_name}
    if chapter is not None:
        sql += " AND s.chapter_index=:c"
        params["c"] = chapter
    sql += " ORDER BY s.chapter_index"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_relations_by_entity(db, book_id: str, entity_name: str,
                             chapter_start: int | None = None,
                             chapter_end: int | None = None) -> list[dict]:
    """实体关系（可选章节区间过滤，子任务 10 query）。

    P1-3：LEFT JOIN entity 取 source_name/target_name（规范名），供前端图谱直接渲染，
    不改 11 表结构（实体名在 entity.entity_name）。
    """
    sql = ("SELECT r.relation_id, r.source_entity_id, r.target_entity_id, r.relation_type, "
           "r.relation_desc, r.relation_weight, r.valid_period, r.start_chapter, r.end_chapter, "
           "se.entity_name AS source_name, te.entity_name AS target_name "
           "FROM entity_relation r "
           "LEFT JOIN entity se ON se.entity_id = r.source_entity_id "
           "LEFT JOIN entity te ON te.entity_id = r.target_entity_id "
           "WHERE r.book_id=:b "
           "AND (r.source_entity_id IN (SELECT entity_id FROM entity WHERE book_id=:b AND entity_name=:n) "
           "     OR r.target_entity_id IN (SELECT entity_id FROM entity WHERE book_id=:b AND entity_name=:n))")
    params: dict = {"b": book_id, "n": entity_name}
    if chapter_start is not None:
        sql += " AND r.start_chapter >= :cs"
        params["cs"] = chapter_start
    if chapter_end is not None:
        sql += " AND r.start_chapter <= :ce"
        params["ce"] = chapter_end
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_timeline_events_by_chapter(db, book_id: str, chapter_index: int) -> list[dict]:
    """该章时间线事件（含参与实体，子任务 10 query）。"""
    rows = db.execute(
        text("SELECT te.event_id, te.event_level, te.parent_event_id, te.event_title, "
             "te.event_content, te.time_desc, te.global_sort, te.start_chapter, te.end_chapter "
             "FROM timeline_event te WHERE te.book_id=:b AND te.start_chapter=:c "
             "ORDER BY te.global_sort"),
        {"b": book_id, "c": chapter_index},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        ents = db.execute(
            text("SELECT e.entity_name FROM timeline_event_entity tee "
                 "JOIN entity e ON e.entity_id = tee.entity_id "
                 "WHERE tee.event_id=:e"),
            {"e": d["event_id"]},
        ).scalars().all()
        d["involved_entities"] = list(ents)
        out.append(d)
    return out


def get_prev_snapshots(db, book_id: str, chapter_index: int) -> list[dict]:
    """上一章 `entity_snapshot`（子任务 07，Layer 1 连续性用）：每实体**最新一条**（chapter_index < 当前）。

    :param db: SQLAlchemy Session
    :param book_id: doc_{user_id}_{doc_id}
    :param chapter_index: 当前全局章节序号
    :return: [{entity_name, status_desc, chapter_index}, ...]（每实体一条最新）
    """
    rows = db.execute(
        text(
            "SELECT entity_name, status_desc, chapter_index FROM entity_snapshot "
            "WHERE book_id=:b AND chapter_index < :c ORDER BY chapter_index DESC"
        ),
        {"b": book_id, "c": chapter_index},
    ).mappings().all()
    seen: dict[str, dict] = {}
    for r in rows:                       # DESC 序 → 首个出现即该实体最新一条
        if r["entity_name"] not in seen:
            seen[r["entity_name"]] = dict(r)
    return list(seen.values())


# ====================== 知识库浏览（P1 补强：分页 + 字段筛选/模糊查询） ======================

# 浏览类型 → 查询规格：qualifier 为表名或别名（用于 book_id 限定）；filters 为 {参数: (SQL 条件, 类型)}
# 类型：like=模糊 LIKE；kw=同 like 但 SQL 里用 :kv 占位（如 relation 的 (se OR te)）；eq/ge/le=精确/区间
_BROWSE_SPECS: dict[str, dict] = {
    "entity": {
        "qualifier": "entity",
        "select": "entity_name, entity_type, first_chapter_index, last_chapter_index, is_active, description, confidence, review_status",
        "from": "entity",
        "order": "entity_name",
        "filters": {
            "name": ("entity_name", "like"),
            "entity_type": ("entity_type", "eq"),
            "is_active": ("is_active", "eq"),
        },
    },
    "entity_snapshot": {
        "qualifier": "entity_snapshot",
        "select": "entity_name, entity_type, chapter_index, status_desc, attributes, confidence, review_status",
        "from": "entity_snapshot",
        "order": "chapter_index DESC, entity_name",
        "filters": {
            "entity_name": ("entity_name", "like"),
            "entity_type": ("entity_type", "eq"),
            "chapter_index": ("chapter_index", "eq"),
        },
    },
    "relation": {
        "qualifier": "r",
        "select": "r.relation_id, se.entity_name AS source_name, te.entity_name AS target_name, "
                  "r.relation_type, r.valid_period, r.start_chapter, r.end_chapter, "
                  "r.relation_desc, r.relation_weight, r.confidence, r.review_status",
        "from": "entity_relation r "
                "LEFT JOIN entity se ON se.entity_id = r.source_entity_id "
                "LEFT JOIN entity te ON te.entity_id = r.target_entity_id",
        "order": "r.start_chapter, r.relation_type",
        "filters": {
            "entity_name": ("(se.entity_name LIKE :kv OR te.entity_name LIKE :kv)", "kw"),
            "relation_type": ("r.relation_type", "eq"),
            "valid_period": ("r.valid_period", "eq"),
            "chapter_from": ("r.start_chapter", "ge"),
            "chapter_to": ("r.start_chapter", "le"),
        },
    },
    "timeline_event": {
        "qualifier": "te",
        "select": "te.event_id, te.event_title, te.event_level, te.global_sort, "
                  "te.start_chapter, te.end_chapter, te.time_desc, te.confidence, te.review_status",
        "from": "timeline_event te",
        "order": "te.global_sort",
        "filters": {
            "event_level": ("te.event_level", "eq"),
            "title": ("te.event_title", "like"),
            "chapter_from": ("te.start_chapter", "ge"),
            "chapter_to": ("te.start_chapter", "le"),
        },
    },
    "location": {
        "qualifier": "location",
        "select": "location_name, location_level, parent_location_id, first_chapter_index, last_chapter_index, description, confidence, review_status",
        "from": "location",
        "order": "location_name",
        "filters": {
            "name": ("location_name", "like"),
            "location_level": ("location_level", "eq"),
        },
    },
    "foreshadowing": {
        "qualifier": "foreshadowing",
        "select": "foreshadowing_id, title, status, setup_chapter, reveal_chapter, description, confidence, review_status",
        "from": "foreshadowing",
        "order": "setup_chapter",
        "filters": {
            "status": ("status", "eq"),
            "title": ("title", "like"),
        },
    },
    "conflict": {
        "qualifier": "story_conflict",
        "select": "conflict_id, conflict_title, conflict_type, side_a, side_b, current_status, start_chapter, end_chapter, confidence, review_status",
        "from": "story_conflict",
        "order": "start_chapter",
        "filters": {
            "title": ("conflict_title", "like"),
            "conflict_type": ("conflict_type", "eq"),
            "current_status": ("current_status", "eq"),
        },
    },
    "rule": {
        "qualifier": "rule_check",
        "select": "rule_id, rule_name, rule_type, subject_ability, valid_from_chapter, valid_to_chapter, rule_content, confidence, review_status",
        "from": "rule_check",
        "order": "valid_from_chapter",
        "filters": {
            "name": ("rule_name", "like"),
            "rule_type": ("rule_type", "eq"),
        },
    },
    "alias": {
        "qualifier": "entity_alias",
        "select": "alias_name, alias_type, entity_id",
        "from": "entity_alias",
        "order": "alias_name",
        "filters": {
            "alias_name": ("alias_name", "like"),
            "alias_type": ("alias_type", "eq"),
        },
    },
    "validation": {
        "qualifier": "v",
        "select": "v.issue_id, v.record_type, v.issue_type, v.severity, v.status, v.chapter_id, "
                  "nc.chapter_title, v.description",
        "from": "validation_issue v LEFT JOIN novel_chapter nc ON nc.chapter_id = v.chapter_id",
        "order": "v.id DESC",
        "filters": {
            "status": ("v.status", "eq"),
            "severity": ("v.severity", "eq"),
            "issue_type": ("v.issue_type", "eq"),
        },
    },
}

BROWSE_TYPES: tuple[str, ...] = tuple(_BROWSE_SPECS.keys())

_OP = {"eq": "=", "ge": ">=", "le": "<="}


def browse(db, book_id: str, type_: str, params: dict | None = None) -> dict:
    """知识库浏览（P1 补强）：按类型分页 + 字段筛选/模糊查询该书解构数据。

    :param type_: BROWSE_TYPES 之一（unknown 由上层 404）
    :param params: 过滤字段（见 _BROWSE_SPECS）+ limit(默认20,max100) + offset(默认0)
    :return: {total, items}；relation 含 source_name/target_name，validation 含 chapter_title
    """
    spec = _BROWSE_SPECS[type_]
    q = spec["qualifier"]
    params = params or {}
    try:
        limit = min(max(int(params.get("limit") or 20), 1), 100)
        offset = max(int(params.get("offset") or 0), 0)
    except (TypeError, ValueError):
        limit, offset = 20, 0

    where = [f"{q}.book_id = :b"]
    bind: dict = {"b": book_id}
    i = 0
    for key, (sql, kind) in spec["filters"].items():
        val = params.get(key)
        if val is None or val == "":
            continue
        i += 1
        pname = f"p{i}"
        if kind == "like":
            where.append(f"{sql} LIKE :{pname}")
            bind[pname] = f"%{val}%"
        elif kind == "kw":
            where.append(sql.replace(":kv", f":{pname}"))
            bind[pname] = f"%{val}%"
        else:
            where.append(f"{sql} {_OP[kind]} :{pname}")
            bind[pname] = val

    where_sql = " AND ".join(where)
    total = db.execute(text(f"SELECT COUNT(*) FROM {spec['from']} WHERE {where_sql}"), bind).scalar()
    rows = db.execute(
        text(
            f"SELECT {spec['select']} FROM {spec['from']} WHERE {where_sql} "
            f"ORDER BY {spec['order']} LIMIT :lim OFFSET :off"
        ),
        {**bind, "lim": limit, "off": offset},
    ).mappings().all()
    return {"total": int(total), "items": [dict(r) for r in rows]}


# ====================== 大修002 · Knowledge API 时态/聚合 ======================

def get_entity_aliases(db, book_id: str, entity_id: str) -> list[str]:
    """实体 → 别名列表（反向查 entity_alias，子任务 03）。"""
    return list(db.execute(
        text("SELECT alias_name FROM entity_alias WHERE book_id=:b AND entity_id=:e ORDER BY alias_name"),
        {"b": book_id, "e": entity_id},
    ).scalars().all())


def get_entity_aliases_with_type(db, book_id: str, entity_id: str) -> list[dict]:
    """别名含类型：[{alias_name, alias_type}]（L0 aliases_by_type 分组源，子任务 04）。"""
    rows = db.execute(
        text("SELECT alias_name, alias_type FROM entity_alias "
             "WHERE book_id=:b AND entity_id=:e ORDER BY alias_name"),
        {"b": book_id, "e": entity_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_entity_foreshadowings(db, book_id: str, entity_id: str, names: list[str],
                              status: str | None = None) -> list[dict]:
    """关联伏笔：involved_entity_ids（存实体名 JSON）含 entity 规范名/别名；status 可过滤（None=全部）。

    子任务 04（L4 未回收秘密 + L3 伏笔埋收线）：
    书级伏笔量小，一次查该书伏笔 + Python 解析 involved_entity_ids 过滤 names 交集——
    避免多条 JSON_SEARCH 的复杂 SQL，且无 N+1。
    """
    sql = ("SELECT foreshadowing_id, title, description, setup_chapter, reveal_chapter, status, "
           "involved_entity_ids, foreshadowing_type, concealment_level, misleading_info, confidence, review_status "
           "FROM foreshadowing WHERE book_id=:b")
    params: dict = {"b": book_id}
    if status is not None:
        sql += " AND status=:st"
        params["st"] = status
    rows = db.execute(text(sql), params).mappings().all()
    name_set = {n for n in names if n}
    out = []
    for r in rows:
        d = dict(r)
        involved = d.get("involved_entity_ids")
        ids = involved if isinstance(involved, list) else (json.loads(involved) if involved else [])
        if isinstance(ids, list) and name_set.intersection(ids):
            out.append(d)
    return out


def get_entity_rules(db, book_id: str, entity_id: str) -> list[dict]:
    """关联规则：rule_check.subject_entity_id = entity_id（FK 干净，一次查询，子任务 04）。"""
    rows = db.execute(
        text("SELECT rule_id, rule_name, rule_type, rule_content, subject_ability, "
             "valid_from_chapter, valid_to_chapter, last_check_result, confidence, review_status "
             "FROM rule_check WHERE book_id=:b AND subject_entity_id=:e ORDER BY valid_from_chapter"),
        {"b": book_id, "e": entity_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_entity_conflicts(db, book_id: str, entity_id: str, names: list[str]) -> list[dict]:
    """卷入冲突：story_conflict.side_a/side_b 含 entity 规范名/别名（LIKE OR 条件，一次查询）。

    子任务 04（L4 卷入冲突）：side 存实体/势力名 TEXT（无 FK），按名匹配。
    """
    sql = ("SELECT conflict_id, conflict_title, conflict_type, conflict_desc, side_a, side_b, "
           "current_status, start_chapter, end_chapter, confidence, review_status "
           "FROM story_conflict WHERE book_id=:b")
    params: dict = {"b": book_id}
    conds = []
    for i, n in enumerate(n for n in names if n):
        conds.append(f"(side_a LIKE :na{i} OR side_b LIKE :nb{i})")
        params[f"na{i}"] = f"%{n}%"
        params[f"nb{i}"] = f"%{n}%"
    if conds:
        sql += " AND (" + " OR ".join(conds) + ")"
    sql += " ORDER BY start_chapter"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_entity_events(db, book_id: str, entity_id: str) -> list[dict]:
    """实体参与的事件（join timeline_event_entity，子任务 04）。

    二阶段 03 后含叙事字段（narrative_type/plot_impact，L4 叙事功能）——只增列，旧调用方不受影响。
    """
    rows = db.execute(
        text("SELECT te.event_id, te.event_title, te.event_level, te.global_sort, "
             "te.start_chapter, te.end_chapter, te.narrative_type, te.plot_impact "
             "FROM timeline_event_entity tee JOIN timeline_event te ON te.event_id = tee.event_id "
             "WHERE tee.book_id=:b AND tee.entity_id=:e ORDER BY te.global_sort"),
        {"b": book_id, "e": entity_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_chapter_by_id(db, chapter_id: str) -> dict | None:
    """章节行（含 chapter_text 原文，大修002 证据提取用）。"""
    row = db.execute(
        text("SELECT chapter_id, book_id, book_name, chapter_index, chapter_title, chapter_text, "
             "char_offset_start, char_offset_end FROM novel_chapter WHERE chapter_id=:c"),
        {"c": chapter_id},
    ).mappings().one_or_none()
    return dict(row) if row else None


def get_latest_snapshot_at_chapter(db, book_id: str, entity_id: str, chapter: int) -> dict | None:
    """实体在 `chapter<=N` 的最新快照（大修002 时态 as-of N，非精确 =N）。"""
    row = db.execute(
        text("SELECT chapter_index, status_desc, attributes FROM entity_snapshot "
             "WHERE book_id=:b AND entity_id=:e AND chapter_index<=:n "
             "ORDER BY chapter_index DESC LIMIT 1"),
        {"b": book_id, "e": entity_id, "n": chapter},
    ).mappings().one_or_none()
    return dict(row) if row else None


def get_entity_snapshots(db, book_id: str, entity_id: str,
                         chapter_end: int | None = None) -> list[dict]:
    """实体全量快照（chapter ≤ chapter_end，按章升序）——L3 成长线 + L2 回填输入（子任务 04）。"""
    sql = ("SELECT chapter_index, status_desc, attributes, confidence, review_status "
           "FROM entity_snapshot WHERE book_id=:b AND entity_id=:e")
    params: dict = {"b": book_id, "e": entity_id}
    if chapter_end is not None:
        sql += " AND chapter_index<=:ce"
        params["ce"] = chapter_end
    sql += " ORDER BY chapter_index"
    rows = db.execute(text(sql), params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        attrs = d.get("attributes")
        if isinstance(attrs, str):                      # MySQL JSON 列 raw 查询返回 str → 解析为 dict
            try:
                d["attributes"] = json.loads(attrs) if attrs else {}
            except (TypeError, ValueError):
                d["attributes"] = {}
        out.append(d)
    return out


def _merge_nonempty(target: dict, source: dict) -> None:
    """把 source 的非空叶子合并进 target（后出现的非空值覆盖，保持"最近非空"）。

    状态累积回填的核心：02 增量提取会省略未变化字段 → 逐属性从历史快照回填最近非空值。
    dict 值递归合并（如 attributes.psychology.inner 缺失时回填更早章的值）。
    """
    for k, v in source.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, dict):
            if not isinstance(target.get(k), dict):
                target[k] = {}
            _merge_nonempty(target[k], v)
        else:
            target[k] = v


def _backfill_snapshot(rows: list[dict]) -> dict:
    """状态累积回填：以最新行为基础，status_desc 与 attributes 逐属性回填最近非空值。

    :param rows: get_entity_snapshots 输出（按 chapter 升序）
    :return: {"chapter_index", "status_desc", "attributes"}（attributes 为回填后完整结构）
    """
    merged: dict = {"chapter_index": None, "status_desc": "", "attributes": {}}
    for r in rows:                       # 升序 → 后出现的非空值即"最近非空"
        if r.get("chapter_index") is not None:
            merged["chapter_index"] = r["chapter_index"]
        if r.get("status_desc"):
            merged["status_desc"] = r["status_desc"]
        if r.get("attributes"):
            _merge_nonempty(merged["attributes"], r["attributes"])
    return merged


def get_valid_relations_at_chapter(db, book_id: str, entity_id: str, chapter: int) -> list[dict]:
    """实体 as-of N 有效关系（source 或 target=entity_id；start<=N AND (end 进行中 或 >=N)）。

    二阶段 03 后含明暗三层（surface/inner/trend，L4 关系质感）——只增列，旧调用方不受影响。
    """
    rows = db.execute(
        text("SELECT r.relation_id, r.source_entity_id, r.target_entity_id, r.relation_type, "
             "r.relation_weight, r.valid_period, r.start_chapter, r.end_chapter, "
             "r.surface_relation, r.inner_relation, r.relation_trend, "
             "se.entity_name AS source_name, te.entity_name AS target_name "
             "FROM entity_relation r "
             "LEFT JOIN entity se ON se.entity_id = r.source_entity_id "
             "LEFT JOIN entity te ON te.entity_id = r.target_entity_id "
             "WHERE r.book_id=:b AND (r.source_entity_id=:e OR r.target_entity_id=:e) "
             "AND r.start_chapter<=:n AND (r.end_chapter=0 OR r.end_chapter IS NULL OR r.end_chapter>=:n)"),
        {"b": book_id, "e": entity_id, "n": chapter},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_timeline_events_by_range(db, book_id: str, chapter_start: int | None = None,
                                 chapter_end: int | None = None) -> list[dict]:
    """章节区间时间线事件（含参与实体；缺省=全部，大修002 timeline 端点）。"""
    sql = ("SELECT te.event_id, te.event_level, te.parent_event_id, te.event_title, "
           "te.event_content, te.time_desc, te.global_sort, te.start_chapter, te.end_chapter "
           "FROM timeline_event te WHERE te.book_id=:b")
    params: dict = {"b": book_id}
    if chapter_start is not None:
        sql += " AND te.start_chapter >= :cs"; params["cs"] = chapter_start
    if chapter_end is not None:
        sql += " AND te.start_chapter <= :ce"; params["ce"] = chapter_end
    sql += " ORDER BY te.global_sort"
    rows = db.execute(text(sql), params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        ents = db.execute(
            text("SELECT e.entity_name FROM timeline_event_entity tee "
                 "JOIN entity e ON e.entity_id = tee.entity_id WHERE tee.event_id=:e"),
            {"e": d["event_id"]},
        ).scalars().all()
        d["involved_entities"] = list(ents)
        out.append(d)
    return out


def get_entity_evidence(db, book_id: str, entity_id: str, chapter: int) -> dict | None:
    """实体在指定章的原文证据（含实体名/别名的 ±200 字窗口；未出现返回 None）。"""
    chrow = db.execute(
        text("SELECT chapter_id, chapter_title, chapter_text FROM novel_chapter "
             "WHERE book_id=:b AND chapter_index=:c"),
        {"b": book_id, "c": chapter},
    ).mappings().one_or_none()
    if not chrow:
        return None
    ent = get_entity(db, entity_id)
    names = ([ent["entity_name"]] + get_entity_aliases(db, book_id, entity_id)) if ent else []
    text_content = chrow["chapter_text"] or ""
    pos = -1
    for name in names:
        p = text_content.find(str(name))
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return None
    w = 200
    start = max(0, pos - w)
    end = min(len(text_content), pos + w)
    return {"chapter_index": chapter, "chapter_title": chrow["chapter_title"],
            "text": text_content[start:end], "char_start": start, "char_end": end}


def get_entity_card(db, book_id: str, entity_id: str, chapter: int) -> dict | None:
    """实体卡聚合（二阶段 04：L0-L4 四层视图）。

    旧 7 键（entity_id/name/type/aliases/status/relations/events/evidence/confidence/review_status）
    值不变，只追加 L0_identity / L1_baseline / L2_snapshot / L3_arc / L4_narrative 五键。
    每层一次查询（无 N+1）；L2 状态累积回填兜底 02 增量省略；三态确定性派生（字段类型 + review_status）。
    """
    ent = get_entity(db, entity_id)
    if not ent or ent.get("book_id") != book_id:
        return None

    # ---- 各层数据（一次查询/层，复用既有基元） ----
    aliases_with_type = get_entity_aliases_with_type(db, book_id, entity_id)
    names = [ent["entity_name"]] + [a["alias_name"] for a in aliases_with_type]
    snapshots = get_entity_snapshots(db, book_id, entity_id, chapter_end=chapter)   # L2 回填 + L3 成长线
    valid_rels = get_valid_relations_at_chapter(db, book_id, entity_id, chapter)    # L2/L3 关系 + L4 明暗
    events = get_entity_events(db, book_id, entity_id)                              # L3 履历 + L4 叙事
    foreshadowings = get_entity_foreshadowings(db, book_id, entity_id, names)       # L3 埋收线 + L4 未回收
    rules = get_entity_rules(db, book_id, entity_id)                                # L4 规则
    conflicts = get_entity_conflicts(db, book_id, entity_id, names)                 # L4 卷入冲突

    # ---- L0 身份锚点 ----
    aliases_by_type: dict[str, list[str]] = {}
    for a in aliases_with_type:
        aliases_by_type.setdefault(a["alias_type"], []).append(a["alias_name"])
    l0_identity = {
        "narrative_role": ent.get("narrative_role"),
        "arc_type": ent.get("arc_type"),
        "first_chapter": ent.get("first_chapter_index"),
        "last_chapter": ent.get("last_chapter_index"),
        "is_active": bool(ent.get("is_active")),
        "aliases_by_type": aliases_by_type,
    }

    # ---- L1 静态基线（core_baseline 可 JSON，缺省兜底） ----
    cb = ent.get("core_baseline")
    cb_obj = None
    if cb:
        try:
            parsed = json.loads(cb) if isinstance(cb, str) else cb
            if isinstance(parsed, dict):
                cb_obj = parsed
        except (TypeError, ValueError):
            cb_obj = None
    l1_baseline = {
        "origin": ent.get("description") or "",
        "core_baseline": cb_obj if cb_obj is not None else (cb if cb else {}),
        "personality": (cb_obj or {}).get("personality", ""),
        "memory_points": (cb_obj or {}).get("memory_points", []),
        "three_state": "inference",          # 基线为主观推断，无锚点持久化
    }

    # ---- L2 章节快照 as-of N（状态累积回填） ----
    backfilled = _backfill_snapshot(snapshots)
    last_snap = snapshots[-1] if snapshots else {}
    l2_snapshot = {
        "chapter_index": backfilled.get("chapter_index"),
        "status_desc": backfilled.get("status_desc", ""),
        "attributes": backfilled.get("attributes", {}),
        "three_state": "review" if last_snap.get("review_status") is None else "fact",
        "confidence": last_snap.get("confidence"),
        "review_status": last_snap.get("review_status"),
    }

    # ---- 关系演变 + 明暗（供 L3/L4，一次遍历复用） ----
    def _other(r: dict) -> str:
        return r["target_name"] if r["source_entity_id"] == entity_id else r["source_name"]

    relation_evolution = [{
        "other_name": _other(r), "relation_type": r["relation_type"],
        "start_chapter": r["start_chapter"], "end_chapter": r["end_chapter"],
        "surface_relation": r.get("surface_relation") or "",
        "inner_relation": r.get("inner_relation") or "",
        "relation_trend": r.get("relation_trend") or "稳定",
    } for r in valid_rels]
    surface_inner_relations = [{
        "other_name": _other(r), "relation_type": r["relation_type"],
        "surface_relation": r.get("surface_relation") or "",
        "inner_relation": r.get("inner_relation") or "",
        "relation_trend": r.get("relation_trend") or "稳定",
        "three_state": "inference" if r.get("inner_relation") else "fact",
    } for r in valid_rels]

    # ---- L3 聚合弧光 ----
    l3_arc = {
        "snapshots": [{
            "chapter_index": s["chapter_index"], "status_desc": s["status_desc"],
            "attributes": s["attributes"], "confidence": s["confidence"], "review_status": s["review_status"],
        } for s in snapshots],
        "events": [{
            "event_id": e["event_id"], "event_title": e["event_title"],
            "start_chapter": e["start_chapter"], "end_chapter": e["end_chapter"],
        } for e in events],
        "relation_evolution": relation_evolution,
        "foreshadowing_line": [{
            "title": f["title"], "setup_chapter": f["setup_chapter"],
            "reveal_chapter": f["reveal_chapter"], "status": f["status"],
        } for f in foreshadowings],
    }

    # ---- L4 叙事功能·明暗·规则 ----
    l4_narrative = {
        "unresolved_secrets": [{
            "title": f["title"],
            "foreshadowing_type": f.get("foreshadowing_type") or "",
            "concealment_level": f.get("concealment_level"),
            "misleading_info": f.get("misleading_info") or "",
            "three_state": "inference",
        } for f in foreshadowings if f["status"] == "pending"],
        "rules": [{
            "rule_name": r["rule_name"], "rule_type": r["rule_type"], "rule_content": r["rule_content"],
            "subject_ability": r["subject_ability"], "last_check_result": r["last_check_result"],
        } for r in rules],
        "conflicts": [{
            "conflict_title": c["conflict_title"], "current_status": c["current_status"],
            "side_a": c["side_a"], "side_b": c["side_b"],
        } for c in conflicts],
        "surface_inner_relations": surface_inner_relations,
        "narrative_types": [{
            "event_title": e["event_title"],
            "narrative_type": e.get("narrative_type") or "",
            "plot_impact": e.get("plot_impact") or "",
            "three_state": "inference",
        } for e in events],
    }

    # ---- 旧键（值不变）+ 新键 ----
    relations = [{
        "other_entity_id": r["target_entity_id"] if r["source_entity_id"] == entity_id else r["source_entity_id"],
        "other_name": _other(r), "relation_type": r["relation_type"], "weight": r["relation_weight"],
        "valid_period": r["valid_period"], "start_chapter": r["start_chapter"], "end_chapter": r["end_chapter"],
    } for r in valid_rels]
    return {
        "entity_id": ent["entity_id"], "name": ent["entity_name"], "type": ent["entity_type"],
        "aliases": [a["alias_name"] for a in aliases_with_type],
        "status": get_latest_snapshot_at_chapter(db, book_id, entity_id, chapter),
        "relations": relations,
        "events": [{
            "event_id": e["event_id"], "event_title": e["event_title"], "event_level": e["event_level"],
            "global_sort": e["global_sort"], "start_chapter": e["start_chapter"], "end_chapter": e["end_chapter"],
        } for e in events],
        "evidence": get_entity_evidence(db, book_id, entity_id, chapter),
        "confidence": ent.get("confidence"),
        "review_status": ent.get("review_status"),
        "L0_identity": l0_identity,
        "L1_baseline": l1_baseline,
        "L2_snapshot": l2_snapshot,
        "L3_arc": l3_arc,
        "L4_narrative": l4_narrative,
    }


# ====================== 大修002 P2-1：复核左右分屏（疑点原文证据） ======================

def _json_string_leaves(v) -> list[str]:
    """递归收集 JSON 值中所有字符串叶值（dict/list/str/标量）。

    供 `get_issue_evidence` 从 suggested_value/original_value 提取可检索关键词：
    dict/list 递归下钻取 str 叶子；标量（int/float/bool/None）忽略，非 JSON 容器原样返回。
    """
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        out: list[str] = []
        for val in v.values():
            out.extend(_json_string_leaves(val))
        return out
    if isinstance(v, list):
        out = []
        for item in v:
            out.extend(_json_string_leaves(item))
        return out
    return []


def get_issue_evidence(db, book_id: str, issue_id: str) -> dict | None:
    """疑点 → 章节原文证据窗口（±200 字，大修002 P2-1 复核左右分屏）。

    关键词来源：description（明文）+ suggested/original JSON 字符串叶值（parse 失败回退原始串）；
    过滤（去空白/长度≥2/去重）后按**长词优先**排序，在 chapter_text 里找首个命中词，
    返回其 ±200 字窗口 + 命中词列表（复用 get_entity_evidence 窗口逻辑）。
    无 issue / chapter_id 空 / 章节不存在 / 无命中 → None。
    """
    row = db.execute(
        text("SELECT chapter_id, description, original_value, suggested_value "
             "FROM validation_issue WHERE issue_id=:i AND book_id=:b"),
        {"i": issue_id, "b": book_id},
    ).mappings().one_or_none()
    if not row or not row["chapter_id"]:
        return None
    chrow = db.execute(
        text("SELECT chapter_id, chapter_title, chapter_index, chapter_text "
             "FROM novel_chapter WHERE chapter_id=:c AND book_id=:b"),
        {"c": row["chapter_id"], "b": book_id},
    ).mappings().one_or_none()
    if not chrow:
        return None

    # 关键词提取：description 明文 + suggested/original JSON 字符串叶值
    raw_terms: list[str] = []
    if row["description"]:
        raw_terms.append(str(row["description"]))
    for field in ("suggested_value", "original_value"):
        raw = row.get(field)
        if not raw:
            continue
        try:
            raw_terms.extend(_json_string_leaves(json.loads(raw)))  # 解析 JSON → 收集字符串叶
        except (TypeError, ValueError):
            raw_terms.append(str(raw))                              # 非 JSON（明文）→ 回退原始串

    candidates: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        s = str(term).strip()
        if len(s) >= 2 and s not in seen:    # 过滤：去空白 / 长度≥2 / 去重
            seen.add(s)
            candidates.append(s)
    candidates.sort(key=len, reverse=True)   # 长词（更具体）优先匹配

    text_content = chrow["chapter_text"] or ""
    pos = -1
    matched = ""
    for cand in candidates:
        p = text_content.find(cand)
        if p >= 0:
            pos, matched = p, cand
            break
    if pos < 0:
        return None
    w = 200
    start = max(0, pos - w)
    end = min(len(text_content), pos + w)
    return {"chapter_index": chrow["chapter_index"], "chapter_title": chrow["chapter_title"],
            "text": text_content[start:end], "char_start": start, "char_end": end,
            "matched_terms": [matched]}
