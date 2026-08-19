# -*- coding: utf-8 -*-
"""
小说解构 ORM 模型 —— SQLAlchemy 2.0 声明式（继承 `db.Base`）。

三张表（字段与 `backend/scripts/init_db.sql` 的 DDL **严格一致**，即"ORM 与 DDL 双正本"）：
  novel_chapter              章节原文表（子任务 01）：解构输入 / 断点续传 / 失败重试的数据源
  deconstruct_job            解构任务表（子任务 02）：一次 book 解构 = 一个 job（进度/汇总）
  deconstruct_chapter_state  章节解构状态表（子任务 02）：每章节一行（pending/processing/done/failed）

命名注意：本模块与 `db.models`（Spec-A 的 4 张核心表）分开 —— 那个文件禁改，
novel 的全部表 ORM 统一放这里。

SQLAlchemy 2.0 学习点：
  - `Mapped[类型]` + `mapped_column(...)` 声明式映射；
  - `server_default`（服务端默认值）：必须用它而非 `default`（客户端默认）——
    因为建表时 `create_all` 会按 `server_default` 生成 DDL 里的 DEFAULT；
    若只用客户端 `default`，裸 SQL INSERT 不传该列会因"无默认值"报错（子任务 02 实测）；
  - `Enum(..., name=...)`：MySQL 原生 ENUM 类型（name 必须全局唯一，否则 create_all 报错）。
"""
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.mysql import JSON, MEDIUMTEXT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class NovelChapter(Base):
    """章节原文表：解构输入 / 断点续传 / 失败重试的数据源。

    关键设计：唯一键 `uk(book_id, file_name, chapter_index_in_file)` ——
    同一本书、同一文件、同一章只存一行 → 重传/重跑走 `ON DUPLICATE KEY UPDATE` 幂等更新。
    """

    __tablename__ = "novel_chapter"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "file_name", "chapter_index_in_file", name="uk_novel_chapter"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)  # nch_{sha1[:20]}（确定性）
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)  # doc_{user_id}_{doc_id}
    book_name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 全书全局章节序号
    chapter_index_in_file: Mapped[int] = mapped_column(Integer, nullable=False)  # 文件内 0-based
    chapter_title: Mapped[str] = mapped_column(String(200), nullable=False)
    chapter_text: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)  # 章节原文（解构输入）
    char_offset_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_offset_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scene_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 场景子窗口数
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class DeconstructJob(Base):
    """解构任务表：一次 book 解构 = 一个 job（断点续传/进度/汇总）。"""

    __tablename__ = "deconstruct_job"
    __table_args__ = (UniqueConstraint("job_id", name="uk_deconstruct_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)  # djob_{snowflake}
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)  # doc_{user_id}_{doc_id}
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        Enum("upload", "manual", name="dc_job_trigger_type"), nullable=False,
        server_default=text("'upload'"),   # 服务端默认：上传自动触发
    )
    total_chapters: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    done_chapters: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_chapters: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "done", "failed", name="dc_job_status"),
        nullable=False, server_default=text("'pending'"),
    )
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DeconstructChapterState(Base):
    """章节解构状态表：每章节一行（pending/processing/done/failed + 溯源锚点）。

    唯一键 `uk(job_id, chapter_id)`：同一 job 里每章只一行（重跑幂等）。
    """

    __tablename__ = "deconstruct_chapter_state"
    __table_args__ = (UniqueConstraint("job_id", "chapter_id", name="uk_chapter_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(64), nullable=False)  # novel_chapter.chapter_id
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 全局章节序号
    scene_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        Enum("pending", "processing", "done", "failed", name="dc_chapter_status"),
        nullable=False, server_default=text("'pending'"),
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    shrink_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Entity(Base):
    """实体注册表（子任务 03）：实体节点，`entity_id` 为全局稳定身份锚点。

    唯一键 `uk_entity_entity_id(entity_id)` —— 同一实体跨章节复用同一 id；
    别名（五条汐/汐小姐/六眼神女）统一经 `EntityAlias` 指向本表。
    """

    __tablename__ = "entity"
    __table_args__ = (UniqueConstraint("entity_id", name="uk_entity_entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)  # ent_{snowflake}
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        Enum("human", "item", "skill", "spirit", "task", "faction", "rule", name="entity_type"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    first_chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    last_chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    narrative_role: Mapped[str | None] = mapped_column(String(50), nullable=True)  # L0 叙事定位
    arc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # L1 弧光类型
    core_baseline: Mapped[str | None] = mapped_column(Text, nullable=True)  # L1 核心基线（欲望/恐惧/执念/决策）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EntityAlias(Base):
    """实体别名映射（子任务 03）：统一"同物异名"到同一 entity_id。

    唯一键 `uk_alias_book_alias(book_id, alias_name)` —— **同书内一个别名只归一个实体**，
    这是"去重率 0"的 DB 保证（register_entity 依赖它做别名消解）。
    """

    __tablename__ = "entity_alias"
    __table_args__ = (
        UniqueConstraint("book_id", "alias_name", name="uk_alias_book_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("entity.entity_id"), nullable=False
    )
    alias_name: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_type: Mapped[str] = mapped_column(
        Enum("full_name", "nickname", "title", "pronoun", "typo", name="alias_type"),
        nullable=False, server_default=text("'nickname'"),
    )
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Location(Base):
    """地点注册表（子任务 04 schema 前置）：timeline_event.location_id 的 FK 依赖本表。

    本子任务仅建表/ORM（因 FK 前置）；location 的 Agent / location_snapshot / 持久化在 05。
    唯一键 uk_location_id(location_id) 供 timeline_event FK 引用。
    """

    __tablename__ = "location"
    __table_args__ = (
        UniqueConstraint("location_id", name="uk_location_id"),
        UniqueConstraint("book_id", "location_id", name="uk_location_book"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[str] = mapped_column(String(100), nullable=False)
    location_name: Mapped[str] = mapped_column(String(200), nullable=False)
    location_level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1世界/2大陆/3城池/4具体场景
    parent_location_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("location.location_id"), nullable=True  # 自引用层级树
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    first_chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    last_chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EntitySnapshot(Base):
    """实体状态快照（子任务 04）：每实体每章节一行。

    幂等键 uk_entity_snapshot(book_id, entity_id, chapter_index) —— 一章一实体现一行，重跑走 ON DUPLICATE KEY UPDATE。
    """

    __tablename__ = "entity_snapshot"
    __table_args__ = (
        UniqueConstraint("book_id", "entity_id", "chapter_index", name="uk_entity_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(100), nullable=False)  # s_{entity_id}_{chapter}
    entity_id: Mapped[str] = mapped_column(String(100), ForeignKey("entity.entity_id"), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        Enum("human", "item", "skill", "spirit", "task", "faction", "rule", name="entity_type"),
        nullable=False,
    )
    status_desc: Mapped[str] = mapped_column(String(1000), nullable=False)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 技能威力/物品功能/任务目标/势力规模
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EntityRelation(Base):
    """实体关系边（子任务 04）：source/target + relation_type + 时效区间。"""

    __tablename__ = "entity_relation"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "source_entity_id", "target_entity_id", "relation_type", "start_chapter",
            name="uk_relation",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    relation_id: Mapped[str] = mapped_column(String(100), nullable=False)  # rel_{snowflake}
    source_entity_id: Mapped[str] = mapped_column(String(100), ForeignKey("entity.entity_id"), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(
        Enum("human", "item", "skill", "spirit", "task", "faction", "rule", name="entity_type"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(String(100), ForeignKey("entity.entity_id"), nullable=False)
    target_entity_type: Mapped[str] = mapped_column(
        Enum("human", "item", "skill", "spirit", "task", "faction", "rule", name="entity_type"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(50), nullable=False)  # possess/master/...
    relation_desc: Mapped[str | None] = mapped_column(String(500), nullable=True)
    relation_weight: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    valid_period: Mapped[str] = mapped_column(
        Enum("permanent", "temporary", "reversed", name="relation_valid_period"),
        nullable=False, server_default=text("'temporary'"),
    )
    start_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    end_chapter: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    surface_relation: Mapped[str | None] = mapped_column(String(300), nullable=True)  # L4 表层关系
    inner_relation: Mapped[str | None] = mapped_column(String(300), nullable=True)  # L4 内心真实态度（合理推断，需原文锚点）
    relation_trend: Mapped[str | None] = mapped_column(String(20), nullable=True)  # L4 关系趋势：升温/降温/稳定/破裂
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TimelineEvent(Base):
    """剧情时间线（子任务 04）：阶段(stage)+事件(event)两级，父子层级。"""

    __tablename__ = "timeline_event"
    __table_args__ = (
        UniqueConstraint("event_id", name="uk_event_id"),
        UniqueConstraint("book_id", "event_id", name="uk_timeline"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False)  # ev_{snowflake}
    event_level: Mapped[str] = mapped_column(
        Enum("stage", "event", name="timeline_event_level"), nullable=False
    )
    parent_event_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("timeline_event.event_id"), nullable=True  # 自引用；stage 为 NULL
    )
    event_title: Mapped[str] = mapped_column(String(200), nullable=False)
    event_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_desc: Mapped[str | None] = mapped_column(String(200), nullable=True)
    global_sort: Mapped[int] = mapped_column(Integer, nullable=False)  # 全书全局时序排序号
    start_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    end_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("location.location_id"), nullable=True
    )
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    narrative_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # L4 叙事类型：升级/打脸/揭秘/转折/战斗/过渡
    plot_impact: Mapped[str | None] = mapped_column(String(500), nullable=True)  # L4 剧情作用：对主线的影响
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TimelineEventEntity(Base):
    """事件↔实体关联（子任务 04）：替代逗号分隔 involved_entity_ids，可索引反查。"""

    __tablename__ = "timeline_event_entity"
    __table_args__ = (UniqueConstraint("event_id", "entity_id", name="uk_event_entity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(100), ForeignKey("timeline_event.event_id"), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), ForeignKey("entity.entity_id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'在场'"))
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LocationSnapshot(Base):
    """地点状态快照（子任务 05）：每地点每章节一行。

    幂等键 uk_location_snapshot(book_id, location_id, chapter_index)；FK → location（04 表）。
    """

    __tablename__ = "location_snapshot"
    __table_args__ = (
        UniqueConstraint("book_id", "location_id", "chapter_index", name="uk_location_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(100), nullable=False)
    location_id: Mapped[str] = mapped_column(String(100), ForeignKey("location.location_id"), nullable=False)
    location_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status_desc: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # 开启/封印/损毁/阵法激活
    special_rules: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Foreshadowing(Base):
    """伏笔与回收（子任务 05）：埋设/回收章节 + 状态机。"""

    __tablename__ = "foreshadowing"
    __table_args__ = (UniqueConstraint("book_id", "foreshadowing_id", name="uk_foreshadowing"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    foreshadowing_id: Mapped[str] = mapped_column(String(100), nullable=False)  # fs_{snowflake}
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    setup_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    setup_event_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("timeline_event.event_id"), nullable=True
    )
    involved_entity_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 次轴，JSON
    reveal_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reveal_event_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "revealed", "abandoned", name="foreshadowing_status"),
        nullable=False, server_default=text("'pending'"),
    )
    related_foreshadowing_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    foreshadowing_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # L4 伏笔类型
    concealment_level: Mapped[int | None] = mapped_column(TINYINT, nullable=True)  # L4 隐蔽度 1-10
    misleading_info: Mapped[str | None] = mapped_column(String(500), nullable=True)  # L4 误导信息
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StoryConflict(Base):
    """冲突核心（子任务 05）：冲突方 + 状态机；无 FK（side 用原名）。"""

    __tablename__ = "story_conflict"
    __table_args__ = (
        UniqueConstraint("book_id", "conflict_id", name="uk_conflict"),
        UniqueConstraint("book_id", "conflict_title", name="uk_conflict_book_title"),  # 006：同冲突单行（业务键）
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conflict_id: Mapped[str] = mapped_column(String(100), nullable=False)  # cfl_{snowflake}
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    conflict_title: Mapped[str] = mapped_column(String(200), nullable=False)
    conflict_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 对抗/资源争夺/...
    conflict_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    side_a: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 实体/势力名
    side_b: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    end_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_status: Mapped[str | None] = mapped_column(String(50), nullable=True, server_default=text("'升级'"))
    escalated_event_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RuleCheck(Base):
    """设定规则校验点（子任务 05）：能力上限/代价/平衡锁，供 Layer 1 校验。"""

    __tablename__ = "rule_check"
    __table_args__ = (
        UniqueConstraint("book_id", "rule_id", name="uk_rule_check"),
        UniqueConstraint("book_id", "rule_name", name="uk_rule_book_name"),  # 006：同规则单行（业务键）
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)  # rul_{snowflake}
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[str] = mapped_column(
        Enum("cap", "cost", "balance_lock", "condition", "other", name="rule_type"),
        nullable=False, server_default=text("'other'"),
    )
    rule_content: Mapped[str] = mapped_column(Text, nullable=False)  # 能力上限/代价/平衡锁原文
    subject_entity_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("entity.entity_id"), nullable=True
    )
    subject_ability: Mapped[str | None] = mapped_column(String(200), nullable=True)
    valid_from_chapter: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    valid_to_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text("0"))
    last_check_result: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_chunk_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)  # 置信度 0~1；NULL = 未复核
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NULL/confirmed/fixed/ignored
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ValidationIssue(Base):
    """一致性校验问题表（子任务 07 建表 + 写入；08 review 状态机）。

    Layer 0/1 拦截项 / 关键记录冲突 / 无原文锚点疑似幻觉 → 挂起人工复核；
    状态机 pending → confirmed / fixed / ignored（08 人工裁决）。
    """

    __tablename__ = "validation_issue"
    __table_args__ = (UniqueConstraint("issue_id", name="uk_validation_issue"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[str] = mapped_column(String(64), nullable=False)  # vis_{snowflake}
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chapter_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_type: Mapped[str] = mapped_column(String(50), nullable=False)  # entity_relation/entity_snapshot/...
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)  # semantic_conflict/state_jump/...
    severity: Mapped[str] = mapped_column(
        Enum("info", "warning", "critical", name="vis_severity"),
        nullable=False, server_default=text("'warning'"),
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "confirmed", "fixed", "ignored", name="vis_status"),
        nullable=False, server_default=text("'pending'"),
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_value: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    suggested_value: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 目标知识行业务键，复核回写定位用
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
