# -*- coding: utf-8 -*-
"""
novel —— 小说解构能力包。

在**企业 AI 智能客服系统（RAG）**底座之上新增的独立能力包，结构：
  pipeline/   切章管线（章节原文入库，子任务 01）
  persistence/ ORM + 仓储 + 幂等写入 + 任务状态机（01/02）
  graph/      LangGraph 两级图（JobGraph + ChapterGraph 子图，子任务 02）
  agents/     8 个解构 Agent 的抽取注册表（03-05 注册实现）
  orchestrator 图编排器（InMemorySaver + streaming，02）
  events.py   事件总线（09 接 SSE）

主线流程：上传 → pipeline 切章落 novel_chapter → orchestrator 驱动 JobGraph
逐章 Send 扇出 → 每章 8 个 Agent 并行解构 → MySQL 11 张解构表入库。
解构期不产生、不重建 Milvus chunk（chunk 只属入库路）。
"""
