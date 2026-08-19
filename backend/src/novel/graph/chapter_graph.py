# -*- coding: utf-8 -*-
"""
ChapterGraph（编译子图）—— 单章解构流程。

  chapter_prepare → Send(agent)×8 → validate_chapter → merge_chapter → persist_chapter

本文件是 **LangGraph 学习核心之一**，演示 4 个关键概念：
  1. **StateGraph + 节点/边**：用 add_node 注册节点，add_edge 连顺序边，add_conditional_edges 连条件边；
  2. **编译子图作节点**：`build_chapter_graph()` 返回 `g.compile()` 的编译结果，
     它会被 JobGraph 当作一个普通节点（process_chapter）挂进主图——"图套图"；
  3. **Send 并行扇出（map-reduce）**：`_fan_out_agents` 从**条件边函数**返回 8 个 `Send`，
     每个 Send 指定目标节点 + payload（该分支的初始状态子集）→ 8 个 agent 并行跑；
  4. **fan-in 归约**：8 个 agent 都连到 validate_chapter，LangGraph 自动合并各分支状态
     （8 个 reducer key 各归约一次），且**保留 prepare 阶段写入的上下文**（已实测）。

每个 agent 独占写自己的 reducer key（单一写入方），天然避免并行写冲突。
"""
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from novel.graph.nodes.agent_nodes import (
    conflict_agent_node,
    entity_agent_node,
    entity_snapshot_agent_node,
    foreshadowing_agent_node,
    location_agent_node,
    relation_agent_node,
    rule_agent_node,
    timeline_agent_node,
)
from novel.graph.nodes.chapter_nodes import (
    chapter_prepare,
    merge_chapter,
    persist_chapter,
    validate_chapter,
)
from novel.graph.state import ChapterState

# 8 个解构 Agent 的节点名（顺序即 fan-out 时的分发顺序）
AGENT_NODES: tuple[str, ...] = (
    "entity_agent", "entity_snapshot_agent", "relation_agent", "timeline_agent",
    "location_agent", "foreshadowing_agent", "conflict_agent", "rule_agent",
)

# 节点名 → 节点实现函数 的映射（注册节点时遍历使用）
_AGENT_IMPLS = {
    "entity_agent": entity_agent_node,
    "entity_snapshot_agent": entity_snapshot_agent_node,
    "relation_agent": relation_agent_node,
    "timeline_agent": timeline_agent_node,
    "location_agent": location_agent_node,
    "foreshadowing_agent": foreshadowing_agent_node,
    "conflict_agent": conflict_agent_node,
    "rule_agent": rule_agent_node,
}


def _fan_out_agents(state: dict) -> list[Send | str]:
    """条件边函数：Send×8 并行扇出；未抢到章节（skipped，P0-1）→ 直通 END 不扇出 Agent。

    关键：Send 必须从**条件边函数**返回（而非普通节点），且 payload 是"该分支的初始状态子集"。
    这里 payload 只带 chapter_id + scenes —— 各 agent 只需读共享的章节场景文本；
    其余上下文（book_id/chapter_index 等）在 fan-in 归约时由 LangGraph 保留，无需重复携带。
    """
    if state.get("chapter_status") == "skipped":
        return [END]          # ★ 未抢到章节 → 直通 END（不扇出 8 Agent，杜绝双重抽取）
    return [
        # payload 追加 hint_entities（跨章名单，06 build_hint_entities 注入）：
        # run_agent 从分支 state 读取并透传给 llm_runner（003 P1-1 已落地）。
        Send(name, {
            "chapter_id": state["chapter_id"],
            "scenes": state.get("scenes", []),
            "hint_entities": state.get("hint_entities"),
            "prev_snapshot_context": state.get("prev_snapshot_context"),  # 二阶段 02：仅 entity_snapshot 消费
        })
        for name in AGENT_NODES
    ]


def build_chapter_graph():
    """编译 ChapterGraph 子图。

    注意：子图**不挂 checkpointer**（断点/回放由主图 JobGraph 的 InMemorySaver 统一驱动）。
    """
    g = StateGraph(ChapterState)
    # 顺序节点：准备（读章节原文+切场景）
    g.add_node("chapter_prepare", chapter_prepare)
    # 8 个并行 Agent 节点
    for name, impl in _AGENT_IMPLS.items():
        g.add_node(name, impl)
    # 收尾节点：校验 → 归并 → 持久化
    g.add_node("validate_chapter", validate_chapter)
    g.add_node("merge_chapter", merge_chapter)
    g.add_node("persist_chapter", persist_chapter)

    # 边：START → prepare
    g.add_edge(START, "chapter_prepare")
    # 条件边：prepare 之后 Send×8 并行扇出（map 阶段）；路径表加 END（skipped → 直通 END，P0-1）
    g.add_conditional_edges("chapter_prepare", _fan_out_agents, list(AGENT_NODES) + [END])
    # 8 个 agent 都汇入 validate（fan-in，reduce 阶段：8 个 reducer key 自动归约）
    for name in AGENT_NODES:
        g.add_edge(name, "validate_chapter")
    # 顺序收尾
    g.add_edge("validate_chapter", "merge_chapter")
    g.add_edge("merge_chapter", "persist_chapter")
    g.add_edge("persist_chapter", END)

    return g.compile()
