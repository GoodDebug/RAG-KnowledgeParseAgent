# -*- coding: utf-8 -*-
"""
novel 持久层：ORM / 仓储 / 幂等写入 / 任务状态机。

  models.py      3 张表 ORM（novel_chapter / deconstruct_job / deconstruct_chapter_state）
  repositories.py novel_chapter 查询封装
  upsert.py      novel_chapter 幂等写入（ON DUPLICATE KEY UPDATE）
  job_state.py   任务/章节状态机

注意：**导入本包即注册 ORM 到 `db.Base.metadata`**（供 `create_all` 建表）——
  `import novel.persistence` 时会执行下面的 `from . import models`，
  从而把 3 张表的 metadata 挂到全局 Base 上（FastAPI 启动 create_all 才能建出这些表）。
"""
from novel.persistence import models  # noqa: F401  注册 ORM 到 Base.metadata
