# -*- coding: utf-8 -*-
"""
novel 解构事件总线雏形（子任务 02）—— 进程内的"发布/订阅"。

图节点在关键节点 publish 事件（chapter_started / agent_done / job_done ...）；
当前只打日志；子任务 09 接 SSE 时由生成器 `subscribe` 订阅，事件实时推给前端。
"""
import logging
from typing import Callable

logger = logging.getLogger("novel.events")

# 订阅者列表（目前为空；09 由 SSE 生成器 append）
_SUBSCRIBERS: list[Callable[[dict], None]] = []


def publish(event: dict) -> None:
    """进程内广播事件（agent_started/done、chapter_done/failed…）。

    设计：订阅者异常不影响主流程（每个订阅者 try/except 隔离），
    避免一个订阅者（如未来 SSE 连接断开）拖垮解构流水线。
    """
    logger.info("novel event: %s", event)
    for sub in list(_SUBSCRIBERS):        # 用快照遍历，允许订阅者在回调里增删
        try:
            sub(event)
        except Exception:                 # 订阅者异常不影响主流程
            logger.exception("novel event subscriber error")


def subscribe(fn: Callable[[dict], None]) -> None:
    """注册订阅者（10 由 SSE 生成器调用）。"""
    _SUBSCRIBERS.append(fn)


def unsubscribe(fn: Callable[[dict], None]) -> None:
    """退订（10，SSE 连接断开时调用，防订阅者泄漏）。"""
    try:
        _SUBSCRIBERS.remove(fn)
    except ValueError:
        pass
