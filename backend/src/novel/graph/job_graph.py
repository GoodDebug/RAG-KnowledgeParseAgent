# -*- coding: utf-8 -*-
"""
JobGraph（主图）—— 整本书的解构编排。

  load_chapters → Send(process_chapter)×N → aggregate → finalize_job

本文件是 **LangGraph 学习核心之二**，演示：
  1. **Send 按章并行扇出**：`_fan_out_chapters` 从条件边函数返回 N 个 Send（每章一个），
     一本几百章的小说 → 每章一个并行任务；
  2. **编译子图作节点**：`build_chapter_graph()` 的编译结果被挂为 `process_chapter` 节点，
     每个 Send 就是"调用一次这个子图"（子图 State = ChapterState）；
  3. **父子图共享 reducer key**：`chapter_results` 同时声明在父图 NovelJobState 和
     子图 ChapterState 里——N 个子图并行完成后各写 1 条，父图自动归约成 N 条；
  4. **Checkpointer**：`compile(checkpointer=InMemorySaver())` —— 图运行到哪一步都会
     存快照，支持断点续跑/时间旅行（子任务 10 断点续传的基础）。
"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from novel.graph.chapter_graph import build_chapter_graph
from novel.graph.nodes.job_nodes import aggregate, finalize_job, load_chapters, validate_book
from novel.graph.state import NovelJobState


def _fan_out_chapters(state: dict) -> list[Send]:
    """条件边函数：Send×N 按章并行扇出。

    payload 是 ChapterState 的子集（chapter_id + 身份元数据 + 共享 reducer key chapter_results）。
    章节文本**不进 payload**（由子图的 chapter_prepare 从 MySQL 读），保持 state lean。
    """
    job_id = state["job_id"]
    book_id = state["book_id"]
    return [
        Send("process_chapter", {
            "chapter_id": c["chapter_id"],
            "job_id": job_id,
            "book_id": book_id,
            "book_name": c.get("book_name", ""),
            "chapter_index": c.get("chapter_index", 0),
            "chapter_title": c.get("chapter_title", ""),
            # 共享 reducer key：子图 persist 写它、父图跨 N 个子图归约（必须带初始 []）
            "chapter_results": [],
        })
        for c in state["chapters"]
    ]


def build_job_graph():
    """编译 JobGraph 主图。

    checkpointer=InMemorySaver()：内存快照器，支持断点/时间旅行；
    max_concurrency（LLM 并发限流）是**运行时 config 项**，在 orchestrator.run_job 注入，
    不在 compile() 传（LangGraph 1.2.9 的 API 差异，子任务 02 实测确认）。
    """
    chapter_graph = build_chapter_graph()          # 先编译子图（ChapterGraph）
    g = StateGraph(NovelJobState)
    g.add_node("load_chapters", load_chapters)     # 读待处理章节清单
    g.add_node("process_chapter", chapter_graph)   # 编译子图作节点 = 每章一个解构任务
    g.add_node("aggregate", aggregate)             # 归约各章结果 → 更新任务计数
    g.add_node("validate_book", validate_book)     # ★ 08：跨章 Layer 1 全局一致性 → validation_issues
    g.add_node("finalize_job", finalize_job)       # 定任务终态（done/failed）

    g.add_edge(START, "load_chapters")
    g.add_conditional_edges("load_chapters", _fan_out_chapters, ["process_chapter"])  # Send×N
    g.add_edge("process_chapter", "aggregate")     # 所有章完成后（fan-in）进入汇总
    g.add_edge("aggregate", "validate_book")       # 08：全书章完成后统一跨章校验
    g.add_edge("validate_book", "finalize_job")
    g.add_edge("finalize_job", END)

    return g.compile(checkpointer=InMemorySaver())
