# snowflake.py
# -*- coding: utf-8 -*-
import os
import time
import logging


_WORKER_ID_MAX = 0x3FF  # 10 bits


def _resolve_worker_id(worker_id: int | None) -> int:
    """解析 worker_id：显式传入优先；无参则读 env `SNOWFLAKE_WORKER_ID`（默认 1），校验 0..1023。

    多进程/多机器（顶层计划外 P0-2）：每个进程/机器必须分配唯一 worker_id，
    否则同 ms 生成的 ID 会碰撞（时间位+worker 位相同、序列每实例独立计数）。
    """
    if worker_id is None:
        raw = os.getenv("SNOWFLAKE_WORKER_ID", "1")
        try:
            worker_id = int(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(f"SNOWFLAKE_WORKER_ID 非法（需 0..1023 整数）：{raw!r}") from e
    if not (0 <= worker_id <= _WORKER_ID_MAX):
        raise ValueError(f"worker_id 必须在 0..1023，实际 {worker_id}（跨进程/跨机器必须唯一）")
    return worker_id


class SnowflakeGenerator:
    """
    简易雪花 ID 生成器，生成 64 位整数 ID。
    结构：41 位时间戳 + 10 位 worker_id + 12 位序列号
    """
    def __init__(self, worker_id: int | None = None):
        self.worker_id = _resolve_worker_id(worker_id)  # 无参 → 读 env SNOWFLAKE_WORKER_ID，默认 1
        self.sequence = 0
        self.last_timestamp = -1
        self.epoch = 1700000000000  # 自定义起始时间（ms）

    def _timestamp(self) -> int:
        return int(time.time() * 1000) - self.epoch

    def generate(self) -> int:
        ts = self._timestamp()
        if ts == self.last_timestamp:
            self.sequence = (self.sequence + 1) & 0xFFF  # 12 bits
        else:
            self.sequence = 0
            self.last_timestamp = ts
        return (ts << 22) | (self.worker_id << 12) | self.sequence


# 进程级单例，全局共享同一个生成器实例（构造时读 env；env 须在进程 import 前注入，与 conftest 设 MYSQL_DB 同法）
snowflake = SnowflakeGenerator()
