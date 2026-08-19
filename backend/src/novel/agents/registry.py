# -*- coding: utf-8 -*-
"""
解构 Agent 抽取注册表 —— 8 个 Agent 的"登记处"。

设计动机（解耦）：
  - 8 个解构 Agent（实体/快照/关系/时间线/地点/伏笔/冲突/规则）各有独立抽取逻辑
    （不同 Prompt、不同 JSON Schema），分别由 03-05 子任务实现；
  - 图节点（agent_nodes.run_agent）不需要知道"每个 Agent 怎么抽取"，只需统一调
    `get_extractor(name)(scene, shrink)` —— 实现与调用解耦；
  - 未注册时返回**空抽取器**并 warn → 图骨架在 Agent 未实现前也能跑通/可 mock。
"""
import logging
from typing import Callable

logger = logging.getLogger("novel.agents.registry")

# 8 个解构 Agent 名（与 graph/nodes/agent_nodes.py 的 RESULT_KEY 一一对应）
AGENT_NAMES: tuple[str, ...] = (
    "entity", "entity_snapshot", "relation", "timeline",
    "location", "foreshadowing", "conflict", "rule",
)

# 抽取器签名：fn(scene_text: str, shrink_level: int, *, hint_entities=None) -> list[dict]
# （hint_entities 为可选关键字，003 P1-1 跨 Agent 命名对齐；既有两参调用保持兼容）
AGENT_EXTRACTORS: dict[str, Callable[..., list[dict]]] = {}


def register_extractor(name: str, fn: Callable[..., list[dict]]) -> None:
    """注册 Agent 抽取器（03-05 各 Agent 模块**导入时**调用）。

    导入时注册的好处：只要 import 了对应 Agent 模块，抽取器就自动可用。
    """
    if name not in AGENT_NAMES:
        raise ValueError(f"未知 Agent 名：{name}，可选 {AGENT_NAMES}")
    AGENT_EXTRACTORS[name] = fn
    logger.info("agent 抽取器已注册：%s", name)


def get_extractor(name: str) -> Callable[..., list[dict]]:
    """取抽取器；未注册返回空抽取器（warn），保证骨架可跑。"""
    fn = AGENT_EXTRACTORS.get(name)
    if fn is None:
        logger.warning("agent %s 未注册抽取器（03-05 子任务实现），返回空抽取器", name)
        return lambda _scene, _shrink=0, **_kw: []
    return fn
