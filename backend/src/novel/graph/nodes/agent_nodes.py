# -*- coding: utf-8 -*-
"""
Agent 节点：通用执行器 `run_agent` + 8 个薄封装节点。

设计思路（解耦 + 可复用）：
  - 8 个解构 Agent 的**抽取逻辑**各不相同（03-05 子任务实现），但"如何调用抽取器、
    如何收集结果、如何记失败"完全一样 → 抽成一个通用执行器 `run_agent`；
  - 8 个节点函数只是"指定 agent 名"的薄封装（`run_agent("entity", state)` 等），
    在 chapter_graph.py 里分别注册为独立节点；
  - 每个 agent 节点**独占写自己的 reducer key**（entities/relations/...），
    并行分支各写各的，fan-in 时 LangGraph 自动归约，互不污染。

注意：抽取逻辑经 `agents.registry` 注入——子任务 02 阶段未注册则返回空抽取器（骨架可跑），
03-05 各 Agent 模块导入时 `register_extractor` 注册真实实现。
"""
import logging

from novel import events
from novel.agents.registry import get_extractor

logger = logging.getLogger("novel.graph.agents")

# agent 名 → ChapterState reducer key 的映射（run_agent 据此把结果写进正确的 key）
RESULT_KEY: dict[str, str] = {
    "entity": "entities",
    "entity_snapshot": "entity_snapshots",
    "relation": "relations",
    "timeline": "timeline_events",
    "location": "locations",
    "foreshadowing": "foreshadowings",
    "conflict": "conflicts",
    "rule": "rule_checks",
}


def run_agent(agent_name: str, state: dict) -> dict:
    """通用执行器：循环 `scenes`，每 scene 调 registry 抽取器，结果 append 到自己的 key。

    LangGraph 学习点：节点函数**返回一个 dict = 对 State 的"更新"**（不是直接改 state），
    返回的 key 会按 reducer 规则合并进 State。这里返回 {结果key: 本次抽到的条目}，
    若出错再补 {errors: [...]}。

    :param state: 该分支 State（含 chapter_id + scenes）
    :return: {RESULT_KEY[agent_name]: items[, "errors": [...] ]}
    """
    scenes: list[str] = state.get("scenes") or []
    shrink_level: int = int(state.get("shrink_level", 0))
    extractor = get_extractor(agent_name)          # 从注册表取该 agent 的抽取器
    out_key = RESULT_KEY[agent_name]               # 本 agent 专属的结果 key
    chapter_id = state.get("chapter_id", "")
    job_id = state.get("job_id", "")

    events.publish({"type": "agent_started", "job_id": job_id, "chapter_id": chapter_id,
                    "agent": agent_name})
    items: list[dict] = []
    errors: list[dict] = []
    for idx, scene in enumerate(scenes):           # 一个场景一个 LLM 调用（缩窗重试在 03 llm_runner）
        events.publish({"type": "scene_started", "job_id": job_id, "chapter_id": chapter_id,
                        "scene_index": idx})
        try:
            # hint_entities：跨 Agent 命名对齐名单（003 P1-1）——本期 state 无该键则 None；
            # 06 resolver 跨章建全量名单后把 hint_entities 放进 state 即可自动注入。
            # prev_snapshot_context：二阶段 02 增量提取——仅 entity_snapshot 消费
            # （其它 Agent 的 extract 签名无此 kwarg，不能透传）。
            kw: dict = {"hint_entities": state.get("hint_entities")}
            if agent_name == "entity_snapshot":
                kw["prev_snapshot_context"] = state.get("prev_snapshot_context")
            got = extractor(scene, shrink_level, **kw)
            if got:
                # `_scene_index`：内部元数据（07 用）——不写库、不进 schema，
                # 供 persist 的 timeline 全局序号合成公式 scene_index 项使用。
                for it in got:
                    it["_scene_index"] = idx
                items.extend(got)
        except Exception as e:                     # 单场景失败不中断其他场景（错误隔离）
            logger.error("agent %s scene[%d] 抽取失败: %s", agent_name, idx, e, exc_info=True)
            errors.append({"agent": agent_name, "scene_index": idx, "code": "extract_error", "msg": str(e)})

    result: dict = {out_key: items}                # 写自己的 reducer key
    if errors:
        result["errors"] = errors                  # 失败信息进 errors（validate 据此判 failed）
        events.publish({"type": "agent_failed", "job_id": job_id, "chapter_id": chapter_id,
                        "agent": agent_name, "error": str(errors[0].get("msg", ""))[:200]})
    else:
        events.publish({"type": "agent_done", "job_id": job_id, "chapter_id": chapter_id,
                        "agent": agent_name, "status": "ok", "items": len(items)})
    logger.info("agent %s done | scenes=%d items=%d errors=%d",
                agent_name, len(scenes), len(items), len(errors))
    return result


# ---------- 8 个 Agent 节点（薄封装：只指定 agent 名，逻辑全在 run_agent） ----------


def entity_agent_node(state: dict) -> dict:
    return run_agent("entity", state)


def entity_snapshot_agent_node(state: dict) -> dict:
    return run_agent("entity_snapshot", state)


def relation_agent_node(state: dict) -> dict:
    return run_agent("relation", state)


def timeline_agent_node(state: dict) -> dict:
    return run_agent("timeline", state)


def location_agent_node(state: dict) -> dict:
    return run_agent("location", state)


def foreshadowing_agent_node(state: dict) -> dict:
    return run_agent("foreshadowing", state)


def conflict_agent_node(state: dict) -> dict:
    return run_agent("conflict", state)


def rule_agent_node(state: dict) -> dict:
    return run_agent("rule", state)
