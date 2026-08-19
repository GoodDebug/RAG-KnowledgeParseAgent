# -*- coding: utf-8 -*-
"""
novel 解构 LangGraph 图包（子任务 02：图骨架与 State）。

  state.py         State 定义（NovelJobState / ChapterState + reducer）
  chapter_graph.py  ChapterGraph（编译子图）：单章解构流程
  job_graph.py      JobGraph（主图）：整书编排（子图挂为节点）
  nodes/            job 级 / chapter 级 / agent 级 的节点函数

学习主线：先读 state.py（State/reducer），再读 chapter_graph.py + job_graph.py
（Send 并行扇出 / 编译子图作节点 / 父子图共享 key 归约），最后读 nodes/ 与 orchestrator.py。
"""
