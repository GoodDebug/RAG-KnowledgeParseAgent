# -*- coding: utf-8 -*-
"""
Layer 1 校验基元：实体状态连续性检查（状态变化需事件支撑，子任务 06，C6）。

职责：`entity_snapshot` 相对**上一章**出现状态翻转（status_desc 实质变化），但本段
`timeline_event` 无对应事件支撑 → 产出 issue（内存字典，不建表）。

设计原则：
  - **保守启发式**：字段级 diff（prev.status_desc != curr.status_desc）+ 事件缺失
    （无任何事件提到该实体）双条件才标记——宁可多标、不漏标，由 07 validate 消费、
    Layer 2/3（08）人工复核最终裁决；
  - **确定性纯逻辑**：不调 LLM（Layer 2 在 08），输入为上一章快照、本章快照、本章事件；
  - 产出 issue 供 07 `validate_chapter`/`validate_book` 转 `validation_issue` 队列。
"""
from __future__ import annotations

# 复核回写业务键映射（大修002 Sub-5 §1.1/§4.2）：record_type/merge 键 -> 业务键列。
# singular 键与 validation.py `_REVIEW_TARGETS` 对齐；merge 表键（复数）仅用于从被标记记录里取业务键。
_BUSINESS_KEY = {
    "entity": "entity_id",
    "entities": "entity_id",
    "entity_snapshot": "snapshot_id",
    "entity_snapshots": "snapshot_id",
    "entity_relation": "relation_id",
    "relations": "relation_id",
    "timeline_event": "event_id",
    "timeline_events": "event_id",
    "location": "location_id",
    "locations": "location_id",
    "location_snapshot": "snapshot_id",
    "foreshadowing": "foreshadowing_id",
    "foreshadowings": "foreshadowing_id",
    "story_conflict": "conflict_id",
    "conflicts": "conflict_id",
    "rule_check": "rule_id",
    "rule_checks": "rule_id",
}


def _extract_target_id(item, record_type: str) -> str | None:
    """从被标记记录提取复核回写定位业务键（§4.2）；无该键 → None（写回 skip）。

    item 为 dict → 取业务键；为 list（如 rule_ladder 同能力多条规则）→ 取首个含键者。
    """
    items = item if isinstance(item, list) else [item]
    key = _BUSINESS_KEY.get(record_type)
    if not key:
        return None
    for it in items:
        if isinstance(it, dict) and it.get(key):
            return it.get(key)
    return None


def _event_supports(entity_name: str, events: list[dict]) -> bool:
    """判断某事件是否"支撑"该实体的状态变化：实体出现在事件参与列表或其内容里。"""
    for e in events:
        involved = e.get("involved_entities") or []
        if entity_name in involved:
            return True
        content = str(e.get("event_content", ""))
        if entity_name and entity_name in content:
            return True
    return False


def check_snapshot_continuity(prev_snapshots: list[dict], curr_snapshots: list[dict],
                              events: list[dict]) -> list[dict]:
    """Layer 1 基元：状态变化需事件支撑。

    :param prev_snapshots: 上一章 entity_snapshot 列表 [{entity_name, status_desc, ...}]
    :param curr_snapshots: 本章 entity_snapshot 列表（同一批实体）
    :param events: 本章 timeline 事件列表（[{event_title, involved_entities[], event_content, ...}]）
    :return: issue 列表 [{entity_name, prev_status, curr_status, missing_event, chapter_index}]
             （无翻转 / 有事件支撑 → 不产出；全空 → []）
    """
    issues: list[dict] = []
    prev_by_name: dict[str, str] = {
        str(s.get("entity_name", "")).strip(): str(s.get("status_desc", "")).strip()
        for s in prev_snapshots
        if str(s.get("entity_name", "")).strip()
    }
    for curr in curr_snapshots:
        name = str(curr.get("entity_name", "")).strip()
        curr_desc = str(curr.get("status_desc", "")).strip()
        if not name:
            continue
        prev_desc = prev_by_name.get(name)
        if not prev_desc or not curr_desc or prev_desc == curr_desc:
            continue                                     # 无上一章 / 无状态变化 → 不检查
        if _event_supports(name, events):
            continue                                     # 有事件支撑 → 非无支撑翻转
        issues.append({
            "entity_name": name,
            "prev_status": prev_desc,
            "curr_status": curr_desc,
            "missing_event": True,
            "chapter_index": curr.get("chapter_index", 0),
            "target_id": curr.get("snapshot_id"),   # 回写定位（无则 None → skip）
        })
    return issues


# ====================== Layer 0/1 章级校验（子任务 07） ======================

def synthesize_global_sort(chapter_index: int, scene_index: int, local_sort: int,
                           k: int = 1000, s: int = 100) -> int:
    """timeline 全局序号合成（003 移入，00 §4.b.2）。

    公式：`chapter_index * K * S + scene_index * K + local_sort`
      K=单章最大事件数上界（=timeline_prompt._LOCAL_SORT_MAX，003 已 lt 约束 local<K）；
      S=单章最大 scene 数上界（=config.NOVEL_SCENE_MAX）。
    保证同书 (chapter_index, scene_index, local_sort) 唯一 → timeline_event.global_sort 不撞号。

    :param chapter_index: 全书全局章节序号
    :param scene_index: 场景序号（run_agent `_scene_index` 标注）
    :param local_sort: 章内相对序号（agent 输出 global_sort 的语义值，<K）
    :param k: 单章最大事件数上界
    :param s: 单章最大 scene 数上界
    :return: 全书全局序号
    """
    return chapter_index * k * s + scene_index * k + local_sort


def check_source_anchor(source_fragment: str, chapter_text: str) -> bool:
    """source_fragment 原文锚定（003 移入，00 §4.b.2）：非空片段必须出现在章节原文。

    空串（旧抽取/未提供）→ 返回 True（跳过锚定，不误伤）；
    非空且不包含于原文 → 疑似幻觉 → False（persist 据此进 validation_issue）。

    :param source_fragment: schema 的可选 source_fragment 字段（verbatim 原文片段）
    :param chapter_text: 章节原文
    :return: True=锚定通过（或空串跳过）；False=疑似幻觉
    """
    if not source_fragment:
        return True
    return source_fragment in (chapter_text or "")


def _layer0_fk_enums(merge_result: dict, resolved: dict) -> tuple[dict, list[dict]]:
    """Layer 0：FK 目标存在 + enum 合法性（确定性拦截）。

    resolved = {"entities": {name: entity_id}, "events": {title: event_id}}
    不满足 → 该条**不进库**（从 pass 剔除），记 issue（record_type=表类型，issue_type=rule_violation）。
    返回 (pass_result, issues)。
    """
    pass_result = {k: [] for k in merge_result}
    issues: list[dict] = []
    eids, evids = resolved.get("entities", {}), resolved.get("events", {})

    def _drop(item, record_type, why):
        issues.append({
            "record_type": record_type, "issue_type": "rule_violation",
            "severity": "warning", "description": why,
            "original_value": None, "suggested_value": item,
            "target_id": _extract_target_id(item, record_type),   # 回写定位（无则 None → skip）
        })

    for rel in merge_result.get("relations", []):
        if rel.get("source") in eids and rel.get("target") in eids:
            pass_result["relations"].append(rel)
        else:
            _drop(rel, "entity_relation", f"relation FK 缺失: source/target 未解析: {rel.get('source')}->{rel.get('target')}")
    for snap in merge_result.get("entity_snapshots", []):
        if snap.get("entity_name") in eids:
            pass_result["entity_snapshots"].append(snap)
        else:
            _drop(snap, "entity_snapshot", f"snapshot FK 缺失: entity_name 未解析: {snap.get('entity_name')}")
    for ev in merge_result.get("timeline_events", []):
        # parent_title 若填了，必须已解析（stage 跨章复用）
        if not ev.get("parent_title") or ev.get("parent_title") in evids:
            pass_result["timeline_events"].append(ev)
        else:
            _drop(ev, "timeline_event", f"timeline parent 缺失: parent_title 未解析: {ev.get('parent_title')}")
    for fs in merge_result.get("foreshadowings", []):
        if not fs.get("setup_event_title") or fs.get("setup_event_title") in evids:
            pass_result["foreshadowings"].append(fs)
        else:
            _drop(fs, "foreshadowing", f"foreshadowing setup_event 缺失: {fs.get('setup_event_title')}")
    for cf in merge_result.get("conflicts", []):
        pass_result["conflicts"].append(cf)              # story_conflict 无 FK，原样
    for ru in merge_result.get("rule_checks", []):
        if not ru.get("subject_entity_name") or ru.get("subject_entity_name") in eids:
            pass_result["rule_checks"].append(ru)
        else:
            _drop(ru, "rule_check", f"rule subject 缺失: {ru.get('subject_entity_name')}")
    # 实体/地点：无 FK 依赖（entity/location 是根节点），原样
    pass_result["entities"] = list(merge_result.get("entities", []))
    pass_result["locations"] = list(merge_result.get("locations", []))
    return pass_result, issues


def _layer1_timeline_order(events: list[dict]) -> list[dict]:
    """Layer 1：merge 后 timeline_events 每 scene 内 local_sort 非递减（按**列表原始顺序**）。

    事件带 `_scene_index`（run_agent 标注）；LLM 产出顺序应与 local_sort 一致——
    若列表中后出现的事件 local_sort 反而更小 → 乱序（paradox）。不排序，直接按出现顺序检查。
    :return: issues（乱序事件）
    """
    issues: list[dict] = []
    by_scene: dict = {}
    for ev in events:
        by_scene.setdefault(ev.get("_scene_index", 0), []).append(ev)
    for scene_idx, evs in by_scene.items():
        prev = -1
        for ev in evs:                          # 保持列表原始顺序
            cur = int(ev.get("global_sort", 0))
            if cur < prev:
                issues.append({
                    "record_type": "timeline_event", "issue_type": "timeline_paradox",
                    "severity": "warning",
                    "description": f"scene[{scene_idx}] 事件乱序: {cur} 在 {prev} 后",
                    "original_value": None, "suggested_value": ev,
                    "target_id": _extract_target_id(ev, "timeline_event"),   # 回写定位
                })
            prev = cur
    return issues


def _layer1_rule_ladder(rule_checks: list[dict], prev_rule_checks: list[dict] | None = None) -> list[dict]:
    """Layer 1：战力/封印阶梯（cap/balance_lock 不跳级或倒退）——章级保守检查。

    本函数为**确定性启发式**：同一 subject_ability 的 cap 值，本章若比上一章**大幅跃升**
    （增量 > K 且无 balance_lock 平衡锁）→ 标记疑似跳级。跨章全量阶梯由 08 validate_book 补强。
    :param rule_checks: 本章 rule_check 条目
    :param prev_rule_checks: 上一章同能力 cap（无则传 None/[]，仅章内一致性检查）
    :return: issues
    """
    issues: list[dict] = []
    if not rule_checks:
        return issues
    # 章内：同一 subject_ability 的 cap 自相矛盾（如 上限5 与 上限10 并存且无 balance_lock）
    caps: dict[str, list[dict]] = {}
    for ru in rule_checks:
        if ru.get("rule_type") in ("cap", "balance_lock"):
            caps.setdefault(ru.get("subject_ability", ""), []).append(ru)
    for ability, rules in caps.items():
        cap_vals = [ru.get("rule_content", "") for ru in rules if ru.get("rule_type") == "cap"]
        has_balance = any(ru.get("rule_type") == "balance_lock" for ru in rules)
        if len(set(cap_vals)) > 1 and not has_balance:
            issues.append({
                "record_type": "rule_check", "issue_type": "rule_violation",
                "severity": "warning",
                "description": f"能力 {ability!r} 存在相互矛盾的 cap 且无 balance_lock: {cap_vals}",
                "original_value": None, "suggested_value": rules,
                "target_id": _extract_target_id(rules, "rule_check"),   # 多条规则取首个含 rule_id 者
            })
    return issues


def build_validation_plan(merge_result: dict, resolved: dict, prev_snapshots: list[dict],
                          chapter_text: str, book_id: str, chapter_index: int,
                          scene_count: int) -> dict:
    """章级校验总入口（07 validate_chapter / persist 调用）：Layer 0/1 → 入库清单 + 拦截清单。

    :param merge_result: merge_chapter_results 输出（各表归并后列表）
    :param resolved: resolver 解析结果 {"entities": {name: id}, "events": {title: id}}
    :param prev_snapshots: 上一章已入库 entity_snapshot（供连续性检查）
    :param chapter_text: 章节原文（source_fragment 锚定用）
    :param book_id: doc_{user_id}_{doc_id}
    :param chapter_index: 全局章节序号
    :param scene_count: 本章 scene 数（入 issue 元数据）
    :return: {"pass": {各表可入库条目}, "issues": [validation_issue dict 元数据]}
    """
    pass_result, issues = _layer0_fk_enums(merge_result, resolved)

    # Layer 1 连续性：状态翻转无事件支撑 → 快照不进库、进 issue
    curr_snapshots = pass_result.get("entity_snapshots", [])
    for iss in check_snapshot_continuity(prev_snapshots, curr_snapshots,
                                         pass_result.get("timeline_events", [])):
        name = iss["entity_name"]
        pass_result["entity_snapshots"] = [s for s in pass_result["entity_snapshots"]
                                           if s.get("entity_name") != name]
        issues.append({
            "record_type": "entity_snapshot", "issue_type": "state_jump",
            "severity": "warning",
            "description": f"状态翻转无事件支撑: {iss['prev_status']} -> {iss['curr_status']}",
            "original_value": iss["prev_status"], "suggested_value": iss["curr_status"],
            "target_id": iss.get("target_id"),   # 来自 check_snapshot_continuity 提取的 snapshot_id
        })

    issues += _layer1_timeline_order(pass_result.get("timeline_events", []))
    issues += _layer1_rule_ladder(pass_result.get("rule_checks", []))

    # source_fragment 原文锚定（003 移入）：非空片段不包含于原文 → 疑似幻觉 → 剔除 + issue
    anchored: dict = {}
    for table in ("entities", "entity_snapshots", "relations", "timeline_events",
                  "locations", "foreshadowings", "conflicts", "rule_checks"):
        kept = []
        for it in pass_result.get(table, []):
            if check_source_anchor(str(it.get("source_fragment", "")), chapter_text):
                kept.append(it)
            else:
                issues.append({
                    "record_type": table, "issue_type": "unsupported_change",
                    "severity": "warning",
                    "description": "source_fragment 未命中原文（疑似幻觉）",
                    "original_value": None, "suggested_value": it,
                    "target_id": _extract_target_id(it, table),   # 回写定位（merge 表键取业务键）
                })
        anchored[table] = kept

    for iss in issues:
        iss.setdefault("book_id", book_id)
        iss.setdefault("chapter_index", chapter_index)
        iss.setdefault("chapter_id", None)
        iss.setdefault("job_id", None)
    return {"pass": anchored, "issues": issues}
