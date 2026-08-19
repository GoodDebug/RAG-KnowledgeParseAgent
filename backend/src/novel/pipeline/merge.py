# -*- coding: utf-8 -*-
"""
章内实体归并 + 跨 Agent 引用对齐 + 事实重叠去重（子任务 06，C1/C2）。

职责：8 个解构 Agent 并行抽取后，在**入库前**把章内结果归一——
  1. `merge_entities`：同一章内"互为别名的实体记录"合并为一条（五条汐+汐小姐 → 规范名+别名并集）；
  2. `build_name_map`：由归并结果构建 {别名/旧名 → 规范名} 映射；
  3. `align_references`：把 relation/snapshot/timeline/conflict/rule 里的实体引用名对齐到规范名；
  4. `dedupe_facts`：跨 Agent 事实重叠去重（同一 relation / 同一事件重复）；
  5. `merge_chapter_results`：综合入口，`merge_chapter` 图节点直接调用。

位置：`merge_chapter` 节点（graph/nodes/chapter_nodes.py）在 validate 之后、persist 之前调用，
返回各 reducer key 的归并后数据；prompt 层分工引导由各 Agent prompt 反例承担，这里只做数据归一。
"""
from __future__ import annotations


def _canonical_for(entities: list[dict]) -> list[tuple[str, set[str]]]:
    """把实体列表按"互为别名"归组，返回 [(规范名, 该组全部名字/别名集合), ...]。

    归并规则（双向）：
      - 一条记录的 name ∈ 另一条的 name ∪ aliases → 视为同一实体，归入同组；
      - 规范名取组内**最长**的 name（最正式全名），等长取首现；
      - 集合含该组所有记录的 name + aliases（去重，去掉空串）。

    :param entities: entity_agent 抽取结果 [{name, aliases[], type, description}]
    :return: 归组后的 [(canonical_name, alias_set)]，按组内首条出现顺序
    """
    # 每条记录：规范化名字集合 = {name} ∪ aliases
    rec_names: list[set[str]] = []
    for e in entities:
        name = str(e.get("name", "")).strip()
        aliases = {str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()}
        rec_names.append({name} | aliases if name else aliases)

    # 贪心并查：两集合有交集即合并（同一实体的不同叫法）
    groups: list[set[str]] = []
    for names in rec_names:
        if not names:
            continue
        hit = [g for g in groups if g & names]
        if not hit:
            groups.append(set(names))
        else:
            merged = set(names)
            for g in hit:
                merged |= g
                groups.remove(g)
            groups.append(merged)

    # 规范名 = 组内"最早出现记录"的 name（输入序确定性；首个记录通常即最正式全名）
    record_order: dict[str, int] = {}
    for idx, e in enumerate(entities):
        nm = str(e.get("name", "")).strip()
        if nm and nm not in record_order:
            record_order[nm] = idx
    out: list[tuple[str, set[str]]] = []
    for g in groups:
        # 组内作为某记录 name 出现的候选里，取首次出现最早者（确定性）
        candidates = [n for n in g if n in record_order]
        canonical = min(candidates, key=lambda n: record_order[n], default="")
        if not canonical:
            canonical = min(g, key=len)
        out.append((canonical, g - {canonical}))
    return out


def merge_entities(entities: list[dict]) -> list[dict]:
    """章内实体名归并/去重：互为别名的记录合并为一条。

    :param entities: entity_agent 抽取结果（章内全部实体）
    :return: 归并后的实体列表（保序；规范名取最常用全名，aliases 取并集）
    """
    if not entities:
        return []
    groups = _canonical_for(entities)
    merged: list[dict] = []
    for canonical, alias_set in groups:
        # 收集组内所有记录，取首个非空 description / type
        group_records = [
            e for e in entities
            if canonical in ({str(e.get("name", "")).strip()} | {str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()})
            or any(a in alias_set for a in (e.get("aliases") or []))
        ]
        if not group_records:
            continue
        description = next((e.get("description") for e in group_records if e.get("description")), "")
        etype = next((e.get("type") for e in group_records if e.get("type")), "rule")
        merged.append({
            "name": canonical,
            "aliases": sorted(alias_set),
            "type": etype,
            "description": description,
        })
    return merged


def build_name_map(entities: list[dict]) -> dict[str, str]:
    """构建 {别名/旧名 → 规范名} 映射（供 align_references 对齐各表引用）。

    :param entities: 归并**前**的原始实体列表（与 merge_entities 同源）
    :return: 每个 name/alias → 其归组后的规范名
    """
    name_map: dict[str, str] = {}
    for canonical, alias_set in _canonical_for(entities):
        name_map[canonical] = canonical
        for a in alias_set:
            name_map[a] = canonical
    return name_map


# 各表的实体引用字段（字符串型 + 列表型）—— align_references 据此逐字段对齐
_STRING_REF_FIELDS = ("source", "target", "entity_name", "side_a", "side_b", "subject_entity_name")
_LIST_REF_FIELDS = ("involved_entities",)


def align_references(items: list[dict], name_map: dict[str, str]) -> list[dict]:
    """把各表实体引用名对齐到规范名（merge 融合规则，003 移入）。

    覆盖字段：
      - 字符串：relation.source/target、snapshot.entity_name、conflict.side_a/side_b、
        rule.subject_entity_name；
      - 列表：timeline/foreshadowing 的 involved_entities[]。
    不在 name_map 中的名字保持不变（可能是地点名/尚未注册的实体，交由 07 resolver 处理）。

    :param items: 某表的条目列表
    :param name_map: build_name_map 输出 {旧名 → 规范名}
    :return: 对齐后的条目列表
    """
    out: list[dict] = []
    for item in items:
        it = dict(item)
        for f in _STRING_REF_FIELDS:
            if f in it and isinstance(it[f], str):
                it[f] = name_map.get(it[f], it[f])
        for f in _LIST_REF_FIELDS:
            if f in it and isinstance(it[f], list):
                it[f] = [name_map.get(x, x) for x in it[f]]
        out.append(it)
    return out


def dedupe_facts(items: list[dict], key_fn) -> list[dict]:
    """跨 Agent 事实重叠去重：按 key_fn 判重，保留首现、丢弃后续重复。

    示例：relation 以 (source, target, relation_type) 去重；timeline 以 (event_level, event_title) 去重。

    :param items: 条目列表
    :param key_fn: 每条约目的去重键函数
    :return: 去重后的条目列表（key_fn 异常 → 该条保留，不误去重）
    """
    seen: set = set()
    out: list[dict] = []
    for it in items:
        try:
            k = key_fn(it)
        except Exception:
            out.append(it)                 # 键函数异常 → 保守保留，不误去重
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def merge_chapter_results(entities, entity_snapshots, relations, timeline_events,
                          locations, foreshadowings, conflicts, rule_checks) -> dict:
    """综合章内归并（merge_chapter 节点调用）。

    归并实体 → 构建 name_map → 各表引用对齐 → 关系/时间线事实去重。
    地点（locations）不含实体引用（parent_name 是地点名，由 07 location 解析），原样保留。

    :return: 各 reducer key 的归并后字典
    """
    merged_entities = merge_entities(entities)
    name_map = build_name_map(entities)
    return {
        "entities": merged_entities,
        "entity_snapshots": align_references(entity_snapshots, name_map),
        "relations": dedupe_facts(
            align_references(relations, name_map),
            lambda r: (r.get("source"), r.get("target"), r.get("relation_type")),
        ),
        "timeline_events": dedupe_facts(
            align_references(timeline_events, name_map),
            lambda e: (e.get("event_level"), e.get("event_title")),
        ),
        "locations": locations,
        "foreshadowings": align_references(foreshadowings, name_map),
        "conflicts": align_references(conflicts, name_map),
        "rule_checks": align_references(rule_checks, name_map),
    }
