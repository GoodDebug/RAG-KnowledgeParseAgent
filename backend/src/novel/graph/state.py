# -*- coding: utf-8 -*-
"""
LangGraph 图状态定义（子任务 02）—— 整个解构流水线的"骨架"。

在 LangGraph 里，**State（状态）就是图的"内存"**：每个节点读它、改它，节点之间靠它传数据。
本文件定义了两个 State：

  ┌─ NovelJobState（作业级）── JobGraph 主图的状态，贯穿整本小说的解构
  └─ ChapterState（章节级）── ChapterGraph 子图的状态，每章一个独立实例

学习点（LangGraph 核心机制）：
  1. `TypedDict` + 类型注解声明 State 的"通道"（channel），LangGraph 据此决定每个 key 怎么合并；
  2. **Reducer（归约器）**：用 `Annotated[类型, 归约函数]` 标注的 key，多个并行分支同时写它时按归约函数合并。
     最常用 `operator.add`（列表拼接）——并行分支各自 append，结果自动汇总，不会互相覆盖；
  3. **标量 key**（不带 reducer）：同一 superstep 内只允许一个写入者，多个并行分支写会抛 `InvalidUpdateError`；
  4. **State lean**：只放必要数据（单章文本/结构化 dict），绝不把整本书塞进内存。
"""
from operator import add
from typing import Annotated, Optional, TypedDict


def _coalesce(a, b):
    """并行分支写入相同标量时的归约：保留已有非空值（消除 InvalidUpdateError）。

    背景：父图 `NovelJobState` 和子图 `ChapterState` 都声明了 `job_id`/`book_id` 这两个标量 key。
    当 N 个章节子图并行完成时，每个子图都会把自己的 `job_id`（相同值）合并回父图 →
    同一 superstep 出现"对一个标量 key 多次写入"→ LangGraph 抛 `InvalidUpdateError`。
    解法：给这两个 key 加上"保留任一非空值"的归约器（各分支值相同，任取其一即可）。
    """
    return a if a not in (None, "") else b


class NovelJobState(TypedDict):
    """作业级状态（JobGraph 主图）：一次"整书解构任务"贯穿全图的共享上下文。"""

    # ---- 身份 / 元数据（标量，加载时写入，之后只读）----
    job_id: Annotated[str, _coalesce]                    # 解构任务 ID，djob_{snowflake}
    book_id: Annotated[str, _coalesce]                   # 小说分组 ID，doc_{user_id}_{doc_id}
    user_id: int                                         # 归属用户
    trigger_type: str                                    # 触发方式：upload / manual
    total_chapters: int                                  # 本次待处理章节总数（load_chapters 写）

    # ---- 流转数据 ----
    chapters: list[dict]                                 # load_chapters 单写：待解构章节清单
    chapter_results: Annotated[list[dict], add]          # ★ reducer：每章子图完成追加 1 条结果
    #              ↑ 关键：子图里也声明同名 key（ChapterState.chapter_results），
    #                父子图共享 key，子图 persist 写、父图这里自动跨 N 个子图归约
    validation_issues: Annotated[list[dict], add]        # ★ reducer：validate_book（08）追加跨章疑点
    job_status: str                                      # 终态单写：done / failed（finalize_job 写）
    error_msg: Optional[str]                             # 失败原因


class ChapterState(TypedDict):
    """章节级状态（ChapterGraph 子图，每章一个独立实例）—— 单章解构的"私有工作台"。

    注意：`chapter_results` 必须与父图 `NovelJobState.chapter_results` **共享同名 key**，
    子图 persist 写它、父图才在跨 Send 实例上归约（已实测）。其余 key 是子图私有，
    不在父图 schema 中时会被 LangGraph 自动丢弃（不污染父图）。
    """

    # ---- 章节身份（Send payload 带入；标量，各分支相同值）----
    chapter_id: str
    job_id: Annotated[str, _coalesce]
    book_id: Annotated[str, _coalesce]
    book_name: str
    chapter_index: int                                   # 全书全局章节序号
    chapter_title: str

    # ---- 章节输入（chapter_prepare 从 MySQL 读取后写入）----
    chapter_text: str                                    # 章节原文（从 novel_chapter 表读）
    scenes: list[str]                                    # 场景子文本（超长章按段落切分）
    scene_count: int                                     # 场景数
    shrink_level: int                                    # 当前缩窗级别（JSON 失败重试用）
    hint_entities: list[str]                             # 跨章命名全量名单（06 build_hint_entities，供 003 P1-1 hint 注入）
    prev_snapshot_context: Optional[str]                 # 二阶段 02：历史已入库快照摘要（最新可用，增量提取背景参考，仅 entity_snapshot 消费）

    # ---- 8 个 Agent 结果键：每 Agent 独占写自己的 key（reducer=append，单一写入方）----
    #     8 个 agent 并行扇出时各自往自己的 key append，fan-in 到 validate 时自动汇总。
    entities: Annotated[list[dict], add]                 # entity_agent → 实体
    entity_snapshots: Annotated[list[dict], add]         # entity_snapshot_agent → 实体快照
    relations: Annotated[list[dict], add]                # relation_agent → 关系
    timeline_events: Annotated[list[dict], add]          # timeline_agent → 时间线事件
    timeline_event_entities: Annotated[list[dict], add]  # timeline_agent → 事件↔实体关联
    locations: Annotated[list[dict], add]                # location_agent → 地点
    location_snapshots: Annotated[list[dict], add]       # location_agent → 地点快照
    foreshadowings: Annotated[list[dict], add]           # foreshadowing_agent → 伏笔
    conflicts: Annotated[list[dict], add]                # conflict_agent → 冲突
    rule_checks: Annotated[list[dict], add]              # rule_agent → 规则校验点
    errors: Annotated[list[dict], add]                   # 抽取失败记录 {agent, scene_index, code, msg}
    merged: dict                                         # merge_chapter 单写（标量，覆盖）：章内归并后结果，persist 消费

    # ---- 结果（persist_chapter 单写）----
    chapter_status: str                                  # done / failed / skipped（P0-1 未抢到章节，仅内存值）
    chapter_results: Annotated[list[dict], add]          # 与父图共享：persist 写、父图归约
