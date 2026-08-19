# -*- coding: utf-8 -*-
"""
11 表单事务入库（子任务 07，C1/C4/C5/C6）。

职责：把 merge 后的章内结果**可靠落库**——消费 06 resolver（跨章 name→entity_id / title→event_id）
→ Layer 0/1 校验（pipeline/validate）→ 拦截项写 validation_issue（不进库）→ pass 项走各表 upsert
→ **单章一个事务（全成或全滚）**。同时落地 timeline 全局序号合成与 source_fragment 原文锚定。

被 `persist_chapter`（graph/nodes/chapter_nodes.py）在 done 分支调用；`db` 由调用方传入
（短生命周期 session），本函数负责**不 commit**（由调用方在全部成功后 commit，异常时 rollback），
以维持"单事务全成或全滚"。
"""
from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import text

from novel.config import novel_scene_max
from novel.pipeline import resolver, validate
from novel.persistence.validation import create_validation_issue
from novel.persistence import upsert
from UTILS.snowflake import snowflake

logger = logging.getLogger("novel.graph.persist")


# ---------- FK 填充辅助 ----------

def _fill_relation(db, rel: dict, eid_map: dict, book_id: str, chapter_index: int) -> dict | None:
    """relation → 填 FK 列；FK 缺失（应已被 Layer 0 拦截）→ None。"""
    src, tgt = rel.get("source"), rel.get("target")
    if src not in eid_map or tgt not in eid_map:
        return None
    return {
        "relation_id": f"rel_{snowflake.generate()}",
        "source_entity_id": eid_map[src],
        "source_entity_type": rel.get("source_type", "human"),
        "target_entity_id": eid_map[tgt],
        "target_entity_type": rel.get("target_type", "human"),
        "relation_type": rel.get("relation_type"),
        "relation_desc": rel.get("desc", ""),
        "relation_weight": rel.get("weight", 2),
        "valid_period": rel.get("valid_period", "temporary"),
        # 二阶段 03：明暗两层 + 趋势（L4 关系质感；inner 需原文锚点，缺省空串）
        "surface_relation": rel.get("surface_relation", ""),
        "inner_relation": rel.get("inner_relation", ""),
        "relation_trend": rel.get("relation_trend", "稳定"),
        # 005 P1-2：start 用 agent 真实值，0/缺 → 当前章（0-based 首章=0 合法）；end 保持 0 = 进行中
        "start_chapter": rel.get("start_chapter") or chapter_index,
        "end_chapter": rel.get("end_chapter") or 0,
        "book_id": book_id,
        "chapter_index": chapter_index,
    }


def _fill_snapshot(snap: dict, eid_map: dict, book_id: str, chapter_index: int) -> dict | None:
    """snapshot → 填 entity_id + 确定性 snapshot_id（s_{entity_id}_{chapter}，幂等）。"""
    name = snap.get("entity_name")
    if name not in eid_map:
        return None
    eid = eid_map[name]
    return {
        "snapshot_id": f"s_{eid}_{chapter_index}",
        "entity_id": eid,
        "entity_name": name,
        "entity_type": snap.get("entity_type", "rule"),
        "status_desc": snap.get("status_desc", ""),
        "attributes": snap.get("attributes", {}),
        "book_id": book_id,
        "chapter_index": chapter_index,
    }


def _fill_timeline(ev: dict, evid_map: dict, book_id: str, chapter_index: int) -> dict | None:
    """timeline event → 填 event_id（resolver 注册/复用）、parent_event_id、global_sort 合成。"""
    title = ev.get("event_title")
    if not title or title not in evid_map:
        return None
    local = int(ev.get("global_sort", 0))
    scene_idx = int(ev.get("_scene_index", 0))
    return {
        "event_id": evid_map[title],
        "event_level": ev.get("event_level", "event"),
        "parent_event_id": evid_map.get(ev.get("parent_title")) if ev.get("parent_title") else None,
        "event_title": title,
        "event_content": ev.get("event_content", ""),
        "time_desc": ev.get("time_desc", ""),
        "global_sort": validate.synthesize_global_sort(
            chapter_index, scene_idx, local, s=novel_scene_max()),
        # 二阶段 03：叙事类型 + 剧情作用（L4 叙事功能）
        "narrative_type": ev.get("narrative_type", ""),
        "plot_impact": ev.get("plot_impact", ""),
        # 005 P1-2：start 用 agent 真实值，0/缺 → 当前章；end 保持 0 = 进行中
        "start_chapter": ev.get("start_chapter") or chapter_index,
        "end_chapter": ev.get("end_chapter") or 0,
        "location_id": None,
        "book_id": book_id,
    }


def _fill_foreshadowing(fs: dict, evid_map: dict, book_id: str, chapter_index: int) -> dict:
    return {
        "foreshadowing_id": f"fs_{snowflake.generate()}",
        "book_id": book_id,
        "title": fs.get("title", ""),
        "description": fs.get("description", ""),
        # 005 P1-2：setup 用 agent 真实值，0/缺 → 当前章；reveal 保持 None = 未回收（进行中）
        "setup_chapter": fs.get("setup_chapter") or chapter_index,
        "setup_event_id": evid_map.get(fs.get("setup_event_title")) if fs.get("setup_event_title") else None,
        "involved_entity_ids": fs.get("involved_entities", []),
        "reveal_chapter": None,
        "reveal_event_id": None,
        "status": fs.get("status", "pending"),
        "related_foreshadowing_ids": [],
        # 二阶段 03：伏笔类型 + 隐蔽度 + 误导（L4 悬念感）
        "foreshadowing_type": fs.get("foreshadowing_type", ""),
        "concealment_level": fs.get("concealment_level"),
        "misleading_info": fs.get("misleading_info", ""),
    }


def _fill_rule(ru: dict, eid_map: dict, book_id: str, chapter_index: int) -> dict:
    return {
        "rule_id": f"rul_{snowflake.generate()}",
        "book_id": book_id,
        "rule_name": ru.get("rule_name", ""),
        "rule_type": ru.get("rule_type", "other"),
        "rule_content": ru.get("rule_content", ""),
        "subject_entity_id": eid_map.get(ru.get("subject_entity_name")) if ru.get("subject_entity_name") else None,
        "subject_ability": ru.get("subject_ability", ""),
        # 005 P1-2：valid_from 用 agent 真实值，0/缺 → 当前章；valid_to 保持 0 = 仍生效（进行中）
        "valid_from_chapter": ru.get("valid_from_chapter") or chapter_index,
        "valid_to_chapter": ru.get("valid_to_chapter") or 0,
    }


# ---------- 005 Phase2：实体关系跨章生命周期 ----------

def _close_relation(db, book_id: str, relation_id: str, end_chapter: int) -> int:
    """关闭进行中关系区间（end=N-1）。谓词 `end_chapter=0` 幂等护栏——已关的不再关。"""
    return db.execute(
        text(
            "UPDATE entity_relation SET end_chapter=GREATEST(:e, 0) "
            "WHERE book_id=:b AND relation_id=:rid AND end_chapter=0"
        ),
        {"e": end_chapter, "b": book_id, "rid": relation_id},
    ).rowcount


def _merge_relation(db, book_id: str, relation_id: str, r: dict) -> int:
    """并入同类型进行中行：保留 start_chapter，更新 desc/weight/valid_period/end/chapter_index。

    二阶段 03：同时更新明暗三列（surface/inner/trend），保证并入最早行时明暗反映最新章。
    """
    return db.execute(
        text(
            "UPDATE entity_relation SET relation_desc=:d, relation_weight=:w, valid_period=:v, "
            "surface_relation=:sr, inner_relation=:ir, relation_trend=:tr, "
            "end_chapter=:e, chapter_index=:ci "
            "WHERE book_id=:b AND relation_id=:rid AND end_chapter=0"
        ),
        {"d": r.get("relation_desc", ""), "w": r.get("relation_weight", 2),
         "v": r.get("valid_period", "temporary"),
         "sr": r.get("surface_relation", ""), "ir": r.get("inner_relation", ""),
         "tr": r.get("relation_trend", "稳定"),
         "e": r.get("end_chapter", 0),
         "ci": r.get("chapter_index", 0), "b": book_id, "rid": relation_id},
    ).rowcount


def persist_relations_lifecycle(db, book_id: str, chapter_index: int,
                                filled_relations: list[dict], *, commit: bool = False) -> int:
    """实体关系跨章生命周期（005 Phase2，用户语义钉死）。

    关系是区间 [start, end]，end=0 = 进行中：
      1. **MERGE**：同 (source,target,relation_type) 后续章出现且无变化 → 并到最早 start 的进行中行
         （不建重复行；uk_relation 含 start，若不并会同 (src,tgt,type) 产生多行 end=0）。
      2. **CLOSE**：同 (source,target) 边本章被重新观测、但旧类型未被再次观测（类型变化）→
         关闭旧区间 end=N-1，随后 OPEN 新区间 start=N（enmity→alliance）。
      3. **OPEN**：类型无进行中行 → 复用 upsert_entity_relation（uk 幂等）。
      4. **缺席不判定结束**：某边整体未出现（agent 漏抽）→ 其进行中行一律不动。
    幂等护栏：uk_relation + CLOSE 谓词 end=0 + ON DUPLICATE 更新（重跑/并发安全）。

    :param filled_relations: 本章 `_fill_relation` 输出（含 source/target eid、relation_type、start/end）
    :return: 受影响行数（CLOSE/MERGE/OPEN 合计）
    """
    if not filled_relations:
        return 0
    N = chapter_index
    # 0) 该书进行中关系快照（end=0）→ 按 (source_eid, target_eid) 索引
    ongoing = db.execute(
        text("SELECT relation_id, source_entity_id, target_entity_id, relation_type, start_chapter "
             "FROM entity_relation WHERE book_id=:b AND end_chapter=0"),
        {"b": book_id},
    ).mappings().all()
    by_pair: dict[tuple, list[dict]] = defaultdict(list)
    for o in ongoing:
        by_pair[(o["source_entity_id"], o["target_entity_id"])].append(dict(o))

    # 1) 本章每对被观测到的类型集合（emitted）——CLOSE/MERGE 判定依据，避免同章新旧类型自相矛盾
    emitted: dict[tuple, set] = defaultdict(set)
    for r in filled_relations:
        emitted[(r["source_entity_id"], r["target_entity_id"])].add(r["relation_type"])

    n = 0
    # 2) 逐对生命周期
    for key, types in emitted.items():
        cands = by_pair.get(key, [])
        # 2a CLOSE：旧类型本章未被观测（该边仍在但类型被新类型替代）→ 关到 N-1
        for c in cands:
            if c["relation_type"] not in types:
                n += _close_relation(db, book_id, c["relation_id"], max(N - 1, 0))
        # 2b MERGE/收敛：每个出现类型若有进行中同类型行 → 并到最早 start 行；多余同类型行（存量脏数据）关闭
        for t in types:
            same = [c for c in cands if c["relation_type"] == t]
            if same:
                target = min(same, key=lambda c: c["start_chapter"])
                r0 = next((r for r in filled_relations
                           if (r["source_entity_id"], r["target_entity_id"]) == key
                           and r["relation_type"] == t), None)
                if r0:
                    n += _merge_relation(db, book_id, target["relation_id"], r0)
                for extra in same:
                    if extra["relation_id"] != target["relation_id"]:
                        n += _close_relation(db, book_id, extra["relation_id"], max(N - 1, 0))
    # 2c OPEN：类型无进行中行 → 插新（复用 upsert_entity_relation；uk 含 start 幂等）
    for r in filled_relations:
        existing_types = {c["relation_type"] for c in by_pair.get((r["source_entity_id"], r["target_entity_id"]), [])}
        if r["relation_type"] not in existing_types:
            n += upsert.upsert_entity_relation(db, r, commit=commit)
    return n


# ---------- 006：rule/conflict 跨章生命周期 ----------

def _merge_rule(db, book_id: str, rule_id: str, r: dict) -> int:
    """并入同 rule_name 的规则行：更新 content/type/subject，保留 valid_from_chapter（不 close+open）。"""
    return db.execute(
        text(
            "UPDATE rule_check SET rule_content=:c, rule_type=:t, subject_entity_id=:s, "
            "subject_ability=:a, last_check_result=:l "
            "WHERE book_id=:b AND rule_id=:rid"
        ),
        {"c": r.get("rule_content", ""), "t": r.get("rule_type", "other"),
         "s": r.get("subject_entity_id"), "a": r.get("subject_ability", ""),
         "l": r.get("last_check_result"), "b": book_id, "rid": rule_id},
    ).rowcount


def persist_rules_lifecycle(db, book_id: str, chapter_index: int,
                            filled_rules: list[dict], *, commit: bool = False) -> int:
    """规则跨章生命周期（006）：同 `rule_name` MERGE（保留 valid_from，不建重复行；不 close+open）。

    规则内容是散文、agent 改写措辞易假变化（011 S4 建议）→ 只做归并去重，不判定取代；
    uk(book_id, rule_name) 保证同规则单行。"""
    if not filled_rules:
        return 0
    rows = db.execute(
        text("SELECT rule_id, rule_name FROM rule_check WHERE book_id=:b"),
        {"b": book_id},
    ).mappings().all()
    by_name = {o["rule_name"]: o["rule_id"] for o in rows}
    n = 0
    for r in filled_rules:
        rid = by_name.get(r.get("rule_name", ""))
        if rid:
            n += _merge_rule(db, book_id, rid, r)
        else:
            n += upsert.upsert_rule_check(db, r, commit=commit)
    return n


def _merge_conflict(db, book_id: str, conflict_id: str, c: dict, chapter_index: int) -> int:
    """并入同 conflict_title 的冲突行：更新 current_status/desc/sides，并按新状态定 end。

    end 语义（005）：agent 显式 end_chapter 用之；`current_status=解决` → end=当前章；否则 0=进行中
    （0/NULL 表进行中；已关闭的冲突重新出现 → end 清 0 = 重新开启，同一行而非新区间）。"""
    end = c.get("end_chapter")
    if end is not None:
        new_end = int(end)
    elif c.get("current_status") == "解决":
        new_end = chapter_index
    else:
        new_end = 0
    return db.execute(
        text(
            "UPDATE story_conflict SET current_status=:s, conflict_desc=:d, side_a=:a, side_b=:b2, "
            "end_chapter=:e WHERE book_id=:b AND conflict_id=:cid"
        ),
        {"s": c.get("current_status", "升级"), "d": c.get("conflict_desc", ""),
         "a": c.get("side_a", ""), "b2": c.get("side_b", ""), "e": new_end,
         "b": book_id, "cid": conflict_id},
    ).rowcount


def persist_conflicts_lifecycle(db, book_id: str, chapter_index: int,
                                filled_conflicts: list[dict], *, commit: bool = False) -> int:
    """冲突跨章生命周期（006）：同 `conflict_title` MERGE（保留 start_chapter，更新状态/end）；缺席不判定结束。

    uk(book_id, conflict_title) 保证同冲突单行；解决（显式 end 或 current_status=解决）→ end 定值；
    重新出现（已关闭）→ end 清 0 重新开启（同一行）。"""
    if not filled_conflicts:
        return 0
    rows = db.execute(
        text("SELECT conflict_id, conflict_title FROM story_conflict WHERE book_id=:b"),
        {"b": book_id},
    ).mappings().all()
    by_title = {o["conflict_title"]: o["conflict_id"] for o in rows}
    n = 0
    for c in filled_conflicts:
        cid = by_title.get(c.get("conflict_title", ""))
        if cid:
            n += _merge_conflict(db, book_id, cid, c, chapter_index)
        else:
            n += upsert.upsert_story_conflict(db, c, commit=commit)
    return n


# ---------- 007 C1：单条/单批写入容错（savepoint 隔离，不整章回滚） ----------

def _safe(db, *, book_id: str, job_id: str, chapter_id: str, record_type: str,
          issue_type: str = "persist_error", fn) -> int:
    """单条/单批写入容错（007 C1）：`begin_nested()` savepoint 内执行 fn；失败 → validation_issue + 跳过。

    语义变更：单章从「全成或全滚」→「best-effort，失败单条/单批隔离」（不整章回滚，010 方案4）。
    成功记 1（fn 返回值忽略，兼容 register_entity 返回 str 等）；失败记 `validation_issue(persist_error)`
    供人工复核；本章其它成功记录仍由外层主事务统一提交。
    """
    try:
        with db.begin_nested():          # SAVEPOINT：失败只回滚本条/本批
            fn()
        return 1
    except Exception as e:
        logger.warning("persist 单批失败隔离 | %s chapter=%s err=%s", record_type, chapter_id, e)
        create_validation_issue(db, {
            "book_id": book_id, "job_id": job_id, "chapter_id": chapter_id,
            "record_type": record_type, "issue_type": issue_type, "severity": "warning",
            "description": f"入库失败已隔离：{e}", "original_value": None, "suggested_value": None,
        })
        return 0


def _safe_apply(db, *, book_id: str, job_id: str, chapter_id: str, record_type: str,
                items: list, apply_one) -> int:
    """逐条 savepoint 隔离：对 items 逐条在 savepoint 内执行 apply_one(item)，失败单条隔离。"""
    n = 0
    for item in items or []:
        n += _safe(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type=record_type,
                   fn=lambda it=item: apply_one(it))
    return n


def _apply_snapshot(db, snap: dict, eid_map: dict, book_id: str, chapter_index: int) -> None:
    """写一条 entity_snapshot（供 007 C1 逐条 savepoint 调用）。"""
    s = _fill_snapshot(snap, eid_map, book_id, chapter_index)
    if s:
        upsert.upsert_entity_snapshot(db, s, commit=False)


def _apply_timeline(db, ev: dict, eid_map: dict, evid_map: dict, book_id: str, chapter_index: int) -> None:
    """写一条 timeline_event + 其参与实体关联（供 007 C1 逐条 savepoint 调用）。"""
    e = _fill_timeline(ev, evid_map, book_id, chapter_index)
    if not e:
        return
    upsert.upsert_timeline_event(db, e, commit=False)
    for ent_name in ev.get("involved_entities", []):
        ent_id = eid_map.get(ent_name)
        if ent_id:
            upsert.upsert_timeline_event_entity(db, {
                "event_id": e["event_id"], "entity_id": ent_id,
                "role": "在场", "book_id": book_id, "chapter_index": chapter_index,
            }, commit=False)


def _apply_location_snapshot(db, snap: dict, loc_map: dict, book_id: str, chapter_index: int) -> None:
    """写一条 location_snapshot（供 007 C1 逐条 savepoint 调用）。"""
    lid = loc_map.get(snap.get("location_name")) or snap.get("location_id")
    if not lid:
        return
    upsert.upsert_location_snapshot(db, {
        "snapshot_id": f"sl_{lid}_{chapter_index}",
        "location_id": lid,
        "location_name": snap.get("location_name", ""),
        "status_desc": snap.get("status_desc", ""),
        "special_rules": snap.get("special_rules", ""),
        "book_id": book_id, "chapter_index": chapter_index,
    }, commit=False)


# ---------- 主入口 ----------

def persist_chapter_tables(db, *, book_id: str, job_id: str, chapter_id: str,
                           chapter_index: int, chapter_text: str,
                           merged: dict, location_snapshots: list[dict],
                           timeline_event_entities: list[dict],
                           prev_snapshots: list[dict], scene_count: int) -> int:
    """11 表单事务入库（单事务全成或全滚，由调用方 commit/rollback）。

    :param db: SQLAlchemy Session（调用方负责 commit/rollback 与关闭）
    :param book_id: doc_{user_id}_{doc_id}
    :param job_id: djob_{snowflake}
    :param chapter_id: nch_{...}
    :param chapter_index: 全书全局章节序号
    :param chapter_text: 章节原文（source_fragment 锚定 + 时序检查用）
    :param merged: merge_chapter_results 输出（entities/relations/...）
    :param location_snapshots: ChapterState.location_snapshots（merge 未覆盖）
    :param timeline_event_entities: ChapterState.timeline_event_entities（merge 未覆盖）
    :param prev_snapshots: 上一章已入库 entity_snapshot（Layer 1 连续性用）
    :param scene_count: 本章 scene 数
    :return: 入库行数（不含 validation_issue）
    :raises: 任一 upsert 异常向上抛（调用方 rollback → 章 failed）
    """
    # 0) source_fragment 原文锚定过滤实体（防止非锚定实体被 resolve 注册的副作用——只注册锚定通过的）
    anchored_entities: list[dict] = []
    anchor_issues: list[dict] = []
    for ent in merged.get("entities", []):
        if validate.check_source_anchor(str(ent.get("source_fragment", "")), chapter_text):
            anchored_entities.append(ent)
        else:
            anchor_issues.append({
                "record_type": "entities", "issue_type": "unsupported_change",
                "severity": "warning", "description": "source_fragment 未命中原文（疑似幻觉）",
                "original_value": None, "suggested_value": ent,
            })
    merged_eff = dict(merged)
    merged_eff["entities"] = anchored_entities

    # 1) 跨章解析（06 resolver 契约；只注册锚定通过的实体；commit=False 保单事务）
    eid_map = resolver.resolve_entity_names(db, book_id, anchored_entities, commit=False)
    evid_map = resolver.resolve_event_titles(db, book_id, merged_eff.get("timeline_events", []))
    resolved = {"entities": eid_map, "events": evid_map}

    # 2) Layer 0/1 校验 → 入库清单 + 拦截清单（基于锚定过滤后的 merged_eff）
    plan = validate.build_validation_plan(merged_eff, resolved, prev_snapshots,
                                          chapter_text, book_id, chapter_index, scene_count)

    # 3) 拦截项写 validation_issue（pending；不 commit——随主事务；含锚定拦截）
    for iss in anchor_issues + plan["issues"]:
        create_validation_issue(db, {
            **iss, "book_id": book_id, "job_id": job_id, "chapter_id": chapter_id,
        })

    # 4) pass 项走各表 upsert（007 C1：逐条/逐批 savepoint 隔离——单表失败不整章回滚，记 persist_error）
    n = 0
    pass_ = plan["pass"]

    # entity（逐条 savepoint）
    n += _safe_apply(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="entity",
                     items=pass_.get("entities", []),
                     apply_one=lambda ent: upsert.register_entity(
                         db, book_id, ent, first_chapter_index=chapter_index,
                         last_chapter_index=chapter_index, commit=False))
    # relation（005 Phase2 生命周期整批 savepoint：内部多操作，单批隔离）
    rel_filled = []
    for rel in pass_.get("relations", []):
        r = _fill_relation(db, rel, eid_map, book_id, chapter_index)
        if r:
            rel_filled.append(r)
    n += _safe(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="entity_relation",
               fn=lambda: persist_relations_lifecycle(db, book_id, chapter_index, rel_filled, commit=False))
    # entity_snapshot（逐条）
    n += _safe_apply(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="entity_snapshot",
                     items=pass_.get("entity_snapshots", []),
                     apply_one=lambda s: _apply_snapshot(db, s, eid_map, book_id, chapter_index))
    # timeline_event（逐条：event + event_entity）
    n += _safe_apply(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="timeline_event",
                     items=pass_.get("timeline_events", []),
                     apply_one=lambda ev: _apply_timeline(db, ev, eid_map, evid_map, book_id, chapter_index))
    # location（逐条注册）+ location_snapshot（逐条）
    loc_map: dict[str, str] = {}
    n += _safe_apply(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="location",
                     items=pass_.get("locations", []),
                     apply_one=lambda loc: loc_map.__setitem__(
                         loc.get("name", ""), upsert.register_location(
                             db, book_id, loc, first_chapter_index=chapter_index,
                             last_chapter_index=chapter_index, commit=False)))
    n += _safe_apply(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="location_snapshot",
                     items=location_snapshots,
                     apply_one=lambda snap: _apply_location_snapshot(db, snap, loc_map, book_id, chapter_index))
    # foreshadowing（逐条）
    n += _safe_apply(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="foreshadowing",
                     items=pass_.get("foreshadowings", []),
                     apply_one=lambda fs: upsert.upsert_foreshadowing(
                         db, _fill_foreshadowing(fs, evid_map, book_id, chapter_index), commit=False))
    # conflict（006 生命周期整批 savepoint）；011 S1：start None/0/缺 → 当前章
    conf_filled = []
    for cf in pass_.get("conflicts", []):
        conf_filled.append({
            "conflict_id": f"cfl_{snowflake.generate()}",
            "book_id": book_id,
            "conflict_title": cf.get("conflict_title", ""),
            "conflict_type": cf.get("conflict_type", "对抗"),
            "conflict_desc": cf.get("conflict_desc", ""),
            "side_a": cf.get("side_a", ""),
            "side_b": cf.get("side_b", ""),
            "start_chapter": cf.get("start_chapter") or chapter_index,
            "end_chapter": cf.get("end_chapter"),
            "current_status": cf.get("current_status", "升级"),
            "escalated_event_ids": [],
        })
    n += _safe(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="story_conflict",
               fn=lambda: persist_conflicts_lifecycle(db, book_id, chapter_index, conf_filled, commit=False))
    # rule（006 生命周期整批 savepoint）
    rule_filled = []
    for ru in pass_.get("rule_checks", []):
        rule_filled.append(_fill_rule(ru, eid_map, book_id, chapter_index))
    n += _safe(db, book_id=book_id, job_id=job_id, chapter_id=chapter_id, record_type="rule_check",
               fn=lambda: persist_rules_lifecycle(db, book_id, chapter_index, rule_filled, commit=False))

    logger.info("persist_chapter_tables | book=%s chapter=%s rows=%d issues=%d",
                book_id, chapter_index, n, len(plan["issues"]))
    return n
