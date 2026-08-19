# -*- coding: utf-8 -*-
"""
novel 管线层：章节切分 / 合并 / 校验 / 解析。

  chapters.py  本地切章 + novel_chapter 原文入库（子任务 01，已完成）
  merge.py     章内实体名归并（子任务 06）
  validate.py  章节结果完整性校验（子任务 07）
  resolver.py  跨章 name→entity_id / event_title→event_id 解析（子任务 06）

当前仅 chapters.py 落地；其余子任务按 §9.2 分解表推进。
"""
