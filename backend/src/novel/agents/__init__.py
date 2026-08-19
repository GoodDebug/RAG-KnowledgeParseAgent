# -*- coding: utf-8 -*-
"""
novel 解构 Agent 包 —— 8 个"内容解构器"的载体。

设计：本包只提供抽取注册表（registry.py）；8 个 Agent 各自独立的抽取实现
（不同 Prompt / 不同 JSON Schema，分别对应 8 种解构内容）由 03-05 子任务实现，
在各自模块**导入时** `register_extractor` 注册进 registry，图节点即可统一调用。

**总入口 = 导入即注册的显式触发点**：Python 规定"模块级代码只在模块被 import 时执行"，
所以"注册表里有 entity"这件事只发生在 entity_agent 被 import 的那一刻。
运行路径上 `novel.graph.nodes.agent_nodes` 只 `from novel.agents.registry import get_extractor`（查表），
它不会 import entity_agent —— 注册的触发只能由本 `__init__.py` 兜住：
本包被 import 时必然执行此文件 → import entity_agent → 模块体 register_extractor → 图内生效。
（顶层计划外《002-导入即注册触发点补丁与AI代码审查方法论》§3；04/05 其余 agent 依同法追加）
"""
from novel.agents import entity_agent  # noqa: F401  导入即注册
from novel.agents import entity_snapshot_agent  # noqa: F401  导入即注册（04 追加）
from novel.agents import relation_agent  # noqa: F401  导入即注册（04 追加）
from novel.agents import timeline_agent  # noqa: F401  导入即注册（04 追加）
from novel.agents import location_agent  # noqa: F401  导入即注册（05 追加）
from novel.agents import foreshadowing_agent  # noqa: F401  导入即注册（05 追加）
from novel.agents import conflict_agent  # noqa: F401  导入即注册（05 追加）
from novel.agents import rule_agent  # noqa: F401  导入即注册（05 追加）
from novel.agents import validator_agent  # noqa: F401  导入即注册（08，Layer 2 批校验；不入 ChapterGraph 扇出）
