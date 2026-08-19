# -*- coding: utf-8 -*-
"""
novel 图节点：job 级 / chapter 级 / agent 级。

  job_nodes.py     JobGraph 主图节点：load_chapters / aggregate / finalize_job
  chapter_nodes.py ChapterGraph 子图节点：chapter_prepare / validate / merge / persist
  agent_nodes.py   8 个 Agent 节点（run_agent 通用执行器 + 薄封装）

每个节点是 LangGraph 的一个 `node`：入参是当前 State，返回值是对 State 的"更新"。
"""
