-- ============================================================
-- AI 智能客服系统 数据库初始化脚本（Spec-A）
-- 正本 = docs/spec/01-子任务-A-基础设施与认证.md §4.4
-- 用途：mysql 容器 /docker-entrypoint-initdb.d/ 首启自动建库建表；
--       亦可手动执行：mysql -u root -p < init_db.sql
-- 幂等：CREATE DATABASE / TABLE 均带 IF NOT EXISTS。
-- ============================================================

-- 建库
CREATE DATABASE IF NOT EXISTS ai_customer_service DEFAULT CHARSET=utf8mb4;
USE ai_customer_service;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    phone VARCHAR(20) UNIQUE,
    email VARCHAR(100) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 会话表（key 存前端透传的 session 字符串，user+key 定位会话）
CREATE TABLE IF NOT EXISTS sessions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    `key` VARCHAR(64) NOT NULL,          -- user_{id}_{session} 的 {session} 部分（key 为 MySQL 保留字，需反引号）
    title VARCHAR(200),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_sessions_user_key (user_id, `key`),
    INDEX idx_sessions_user_created (user_id, created_at),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 消息表（唯一事实源；intent/引用/反馈为列，由 router 层补写）
CREATE TABLE IF NOT EXISTS messages (
    id INT PRIMARY KEY AUTO_INCREMENT,
    session_id INT NOT NULL,
    role ENUM('user','assistant','system') NOT NULL,
    content TEXT NOT NULL,
    intent VARCHAR(50),                  -- 意图识别标注（加分项）
    source_refs MEDIUMTEXT,              -- 引用来源 JSON 数组（MEDIUMTEXT 防数百引用溢出，顶层计划外）
    feedback ENUM('up','down') NULL DEFAULT NULL,
    feedback_text TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_messages_session (session_id),
    INDEX idx_messages_session_role (session_id, role),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 知识库文档表
CREATE TABLE IF NOT EXISTS documents (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    book_name VARCHAR(200) NOT NULL,   -- 书目标题分组键（Spec-C DDL 修订）
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(10),
    status ENUM('processing','ready','failed'),
    chunk_count INT,
    book_id VARCHAR(50) NULL,          -- 组 book_id 稳定锚点（顶层计划外：书分组与单文件删除）
    milvus_collection VARCHAR(100),
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_documents_user_uploaded (user_id, uploaded_at),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 内置测试基础用户（开发阶段，顶层计划外）
-- 手机号 12345678910 / 密码 1234567（bcrypt 哈希，passlib 生成并 verify）
-- SEED_DOCS_ENABLED=1 时应用启动把 docs/start_files 导入并绑定到此用户
-- 幂等：INSERT IGNORE 依赖 phone 唯一索引；已存在同名手机号时跳过（不覆盖其密码）
-- ============================================================
INSERT IGNORE INTO users (phone, email, password_hash)
VALUES ('12345678910', NULL, '$2b$12$YE2IgnqXwy14mk1blsFfV.0O7wplQ332ydyB7aCwfsjYje/YtyC7W');

-- ============================================================
-- 小说解构 · 章节原文表（子任务 01：章节切分与原文入库）
-- 用途：解构输入 / 断点续传 / 失败重试的数据源（源文件入库后被删除）
-- 幂等：CREATE TABLE IF NOT EXISTS；重复键不覆盖 chapter_index（保留原全局索引）
-- ============================================================
CREATE TABLE IF NOT EXISTS novel_chapter (
    id INT PRIMARY KEY AUTO_INCREMENT,
    chapter_id VARCHAR(64) NOT NULL,              -- nch_{sha1(book_id|file_name|chapter_index_in_file)[:20]}
    book_id VARCHAR(50) NOT NULL,                 -- doc_{user_id}_{doc_id}
    book_name VARCHAR(200) NOT NULL,
    file_name VARCHAR(255) NOT NULL,              -- 所属源文件（跨文件章节去重/溯源）
    chapter_index INT NOT NULL,                   -- 全书全局章节序号（跨文件重编号；供 11 表锚点）
    chapter_index_in_file INT NOT NULL,           -- 文件内 0-based（对齐 Milvus chunk chapter_index）
    chapter_title VARCHAR(200) NOT NULL,
    chapter_text MEDIUMTEXT NOT NULL,             -- 章节原文（解构输入）
    char_offset_start INT NOT NULL DEFAULT 0,     -- 章节在清洗后文档文本中的字符偏移起点
    char_offset_end INT NOT NULL DEFAULT 0,       -- 字符偏移终点
    scene_count INT NOT NULL DEFAULT 1,           -- 场景子窗口数（超长章节 LLM 输入定大小；不涉及 chunk）
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_novel_chapter (book_id, file_name, chapter_index_in_file),
    INDEX idx_novel_chapter_book (book_id, chapter_index),
    INDEX idx_novel_chapter_file (book_id, file_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 小说解构 · 解构任务表（子任务 02：图骨架与 State）
-- 用途：一次 book 解构 = 一个 job（断点续传/进度/汇总）
-- 幂等：CREATE TABLE IF NOT EXISTS
-- ============================================================
CREATE TABLE IF NOT EXISTS deconstruct_job (
    id INT PRIMARY KEY AUTO_INCREMENT,
    job_id VARCHAR(64) NOT NULL,                  -- djob_{snowflake}
    book_id VARCHAR(50) NOT NULL,                 -- doc_{user_id}_{doc_id}
    user_id INT NOT NULL,
    trigger_type ENUM('upload','manual') NOT NULL DEFAULT 'upload',
    total_chapters INT NOT NULL DEFAULT 0,
    done_chapters INT NOT NULL DEFAULT 0,
    failed_chapters INT NOT NULL DEFAULT 0,
    status ENUM('pending','running','done','failed') NOT NULL DEFAULT 'pending',
    error_msg TEXT,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_deconstruct_job (job_id),
    INDEX idx_deconstruct_job_book (book_id, status),
    INDEX idx_deconstruct_job_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 章节解构状态：每章节一行（pending/processing/done/failed + 溯源锚点）
CREATE TABLE IF NOT EXISTS deconstruct_chapter_state (
    id INT PRIMARY KEY AUTO_INCREMENT,
    job_id VARCHAR(64) NOT NULL,
    chapter_id VARCHAR(64) NOT NULL,              -- novel_chapter.chapter_id
    book_id VARCHAR(50) NOT NULL,
    chapter_index INT NOT NULL,                   -- 全局章节序号
    scene_count INT NOT NULL DEFAULT 1,
    status ENUM('pending','processing','done','failed') NOT NULL DEFAULT 'pending',
    retry_count INT NOT NULL DEFAULT 0,
    shrink_level TINYINT NOT NULL DEFAULT 0,
    error_msg TEXT,
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_chapter_state (job_id, chapter_id),
    INDEX idx_chapter_state_job_status (job_id, status),
    INDEX idx_chapter_state_book (book_id, chapter_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- ============================================================
-- 小说解构 · 实体注册表（子任务 03：实体 Agent）
-- entity：实体节点（统一身份锚点）；entity_alias：别名映射（同书同别名必归同一 entity_id）
-- 幂等：uk_entity_entity_id / uk_alias_book_alias；ON DUPLICATE KEY UPDATE 支持去重
-- ============================================================
CREATE TABLE IF NOT EXISTS entity (
    id INT PRIMARY KEY AUTO_INCREMENT,
    entity_id VARCHAR(100) NOT NULL,             -- ent_{snowflake}，稳定业务ID
    entity_name VARCHAR(200) NOT NULL,
    entity_type ENUM('human','item','skill','spirit','task','faction','rule') NOT NULL,
    description TEXT,
    book_id VARCHAR(50) NOT NULL,
    first_chapter_index INT NOT NULL,            -- 首次出现章节
    last_chapter_index INT NOT NULL,             -- 最近出现章节
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    narrative_role VARCHAR(50) DEFAULT NULL COMMENT '叙事定位：主角/核心配角/反派/导师/镜像/喜剧担当',
    arc_type VARCHAR(50) DEFAULT NULL COMMENT '弧光类型：成长型/堕落型/救赎型/悲剧型/工具型',
    core_baseline TEXT COMMENT '核心基线：欲望/恐惧/执念/决策权重（JSON 或文本），或规范 description 只放基线',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_entity_entity_id (entity_id),
    INDEX idx_entity_book_type (book_id, entity_type),
    INDEX idx_entity_book_name (book_id, entity_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 实体别名映射：统一 "五条汐/汐小姐/六眼神女" 指向同一 entity_id
CREATE TABLE IF NOT EXISTS entity_alias (
    id INT PRIMARY KEY AUTO_INCREMENT,
    entity_id VARCHAR(100) NOT NULL,
    alias_name VARCHAR(200) NOT NULL,
    alias_type ENUM('full_name','nickname','title','pronoun','typo') NOT NULL DEFAULT 'nickname',
    book_id VARCHAR(50) NOT NULL,
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_alias_book_alias (book_id, alias_name),   -- 同书内一个别名只归一个实体
    INDEX idx_alias_entity (book_id, entity_id),
    FOREIGN KEY (entity_id) REFERENCES entity(entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 小说解构 · 快照/关系/时间线表（子任务 04）
-- 建表顺序：location 前置（timeline_event.location_id 的 FK 依赖它）→ entity_snapshot
--   → entity_relation → timeline_event → timeline_event_entity
-- 幂等：各表 uk + ON DUPLICATE KEY UPDATE；FK 完整性是验收红线
-- ============================================================
CREATE TABLE IF NOT EXISTS location (
    id INT PRIMARY KEY AUTO_INCREMENT,
    location_id VARCHAR(100) NOT NULL,
    location_name VARCHAR(200) NOT NULL,
    location_level TINYINT NOT NULL,             -- 1世界/2大陆/3城池/4具体场景
    parent_location_id VARCHAR(100),
    description TEXT,
    book_id VARCHAR(50) NOT NULL,
    first_chapter_index INT NOT NULL,
    last_chapter_index INT NOT NULL,
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_location_id (location_id),             -- 供外键引用（FK 必须指向唯一列）
    UNIQUE KEY uk_location_book (book_id, location_id),  -- 复合唯一：同书内地点不重复
    INDEX idx_location_parent (book_id, parent_location_id),
    FOREIGN KEY (parent_location_id) REFERENCES location(location_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 实体状态快照：每实体每章节一行；幂等键 (book_id, entity_id, chapter_index)
CREATE TABLE IF NOT EXISTS entity_snapshot (
    id INT PRIMARY KEY AUTO_INCREMENT,
    snapshot_id VARCHAR(100) NOT NULL,
    entity_id VARCHAR(100) NOT NULL,
    entity_name VARCHAR(200) NOT NULL,
    entity_type ENUM('human','item','skill','spirit','task','faction','rule') NOT NULL,
    status_desc VARCHAR(1000) NOT NULL,          -- 本章状态描述（觉醒反转术式/肉身孱弱/一级战力）
    attributes JSON,                             -- 技能威力/物品功能/任务目标/势力规模（MySQL 原生 JSON）
    book_id VARCHAR(50) NOT NULL,
    chapter_index INT NOT NULL,
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_entity_snapshot (book_id, entity_id, chapter_index),  -- 幂等 upsert 关键
    INDEX idx_entity_snapshot_entity (entity_id, chapter_index),
    FOREIGN KEY (entity_id) REFERENCES entity(entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 实体关系边：章节级动态关系 + 时效区间
CREATE TABLE IF NOT EXISTS entity_relation (
    id INT PRIMARY KEY AUTO_INCREMENT,
    relation_id VARCHAR(100) NOT NULL,
    source_entity_id VARCHAR(100) NOT NULL,
    source_entity_type ENUM('human','item','skill','spirit','task','faction','rule') NOT NULL,
    target_entity_id VARCHAR(100) NOT NULL,
    target_entity_type ENUM('human','item','skill','spirit','task','faction','rule') NOT NULL,
    relation_type VARCHAR(50) NOT NULL,          -- possess/master/contain/restrain/undertake/belong_to/host_bind/alliance/enmity/family...
    relation_desc VARCHAR(500),
    relation_weight TINYINT NOT NULL DEFAULT 2,  -- 1核心 / 2次要
    valid_period ENUM('permanent','temporary','reversed') NOT NULL DEFAULT 'temporary',
    start_chapter INT NOT NULL,                  -- 关系起始生效章节
    end_chapter INT NOT NULL DEFAULT 0,          -- 结束章节，0=永久/当前有效
    book_id VARCHAR(50) NOT NULL,
    chapter_index INT NOT NULL,                  -- 关系在哪一章节被观测/建立
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    surface_relation VARCHAR(300) DEFAULT NULL COMMENT '对外公开的表层关系',
    inner_relation VARCHAR(300) DEFAULT NULL COMMENT '双方内心真实态度与隐情（合理推断，需原文锚点）',
    relation_trend VARCHAR(20) DEFAULT NULL COMMENT '关系趋势：升温/降温/稳定/破裂',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_relation (book_id, source_entity_id, target_entity_id, relation_type, start_chapter),
    INDEX idx_relation_source (book_id, source_entity_id, chapter_index),
    INDEX idx_relation_target (book_id, target_entity_id, chapter_index),
    INDEX idx_relation_type (book_id, relation_type, chapter_index),
    FOREIGN KEY (source_entity_id) REFERENCES entity(entity_id),
    FOREIGN KEY (target_entity_id) REFERENCES entity(entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 剧情时间线：阶段(stage)+事件(event)两级，父子层级
CREATE TABLE IF NOT EXISTS timeline_event (
    id INT PRIMARY KEY AUTO_INCREMENT,
    event_id VARCHAR(100) NOT NULL,
    event_level ENUM('stage','event') NOT NULL,
    parent_event_id VARCHAR(100),                -- 事件归属的阶段；stage 为 NULL
    event_title VARCHAR(200) NOT NULL,
    event_content TEXT,
    time_desc VARCHAR(200),                      -- 文本内时间描述（怀玉篇/三年后）
    global_sort INT NOT NULL,                    -- 全书全局时序排序号
    start_chapter INT NOT NULL,
    end_chapter INT NOT NULL,
    location_id VARCHAR(100),
    book_id VARCHAR(50) NOT NULL,
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    narrative_type VARCHAR(50) DEFAULT NULL COMMENT '叙事类型：升级/打脸/揭秘/转折/战斗/过渡',
    plot_impact VARCHAR(500) DEFAULT NULL COMMENT '对主线剧情的影响（一句话）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_event_id (event_id),                   -- 供外键引用（FK 必须指向唯一列）
    UNIQUE KEY uk_timeline (book_id, event_id),          -- 复合唯一：同书内事件不重复
    INDEX idx_timeline_parent (book_id, parent_event_id, global_sort),
    INDEX idx_timeline_chapter (book_id, start_chapter, end_chapter),
    INDEX idx_timeline_sort (book_id, global_sort),
    FOREIGN KEY (parent_event_id) REFERENCES timeline_event(event_id),  -- 自引用
    FOREIGN KEY (location_id) REFERENCES location(location_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 事件↔实体关联表：替代逗号分隔 involved_entity_ids
CREATE TABLE IF NOT EXISTS timeline_event_entity (
    id INT PRIMARY KEY AUTO_INCREMENT,
    event_id VARCHAR(100) NOT NULL,
    entity_id VARCHAR(100) NOT NULL,
    role VARCHAR(50) DEFAULT '在场',             -- 主角/敌对/见证/...
    book_id VARCHAR(50) NOT NULL,
    chapter_index INT NOT NULL,
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    UNIQUE KEY uk_event_entity (event_id, entity_id),
    INDEX idx_event_entity_entity (book_id, entity_id, chapter_index),  -- 反查"某实体参与的所有事件"
    FOREIGN KEY (event_id) REFERENCES timeline_event(event_id),
    FOREIGN KEY (entity_id) REFERENCES entity(entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 小说解构 · 地点/伏笔/冲突/规则表（子任务 05）
-- 建表顺序：location_snapshot（FK→location，04 表）→ foreshadowing（FK→timeline_event，04 表）
--   → story_conflict（无 FK）→ rule_check（FK→entity，03 表）
-- location 表在 04 已建（schema 前置），此处不重建
-- ============================================================
CREATE TABLE IF NOT EXISTS location_snapshot (
    id INT PRIMARY KEY AUTO_INCREMENT,
    snapshot_id VARCHAR(100) NOT NULL,
    location_id VARCHAR(100) NOT NULL,
    location_name VARCHAR(200) NOT NULL,
    status_desc VARCHAR(1000),                   -- 开启/封印/损毁/阵法激活
    special_rules VARCHAR(1000),                 -- 本章生效特殊规则
    book_id VARCHAR(50) NOT NULL,
    chapter_index INT NOT NULL,
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_location_snapshot (book_id, location_id, chapter_index),
    INDEX idx_location_snapshot_loc (location_id, chapter_index),
    FOREIGN KEY (location_id) REFERENCES location(location_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 伏笔与回收
CREATE TABLE IF NOT EXISTS foreshadowing (
    id INT PRIMARY KEY AUTO_INCREMENT,
    foreshadowing_id VARCHAR(100) NOT NULL,
    book_id VARCHAR(50) NOT NULL,
    title VARCHAR(200),
    description TEXT,
    setup_chapter INT NOT NULL,                  -- 埋设章节
    setup_event_id VARCHAR(100),
    involved_entity_ids JSON,                    -- 非主查询轴，用 JSON
    reveal_chapter INT,                          -- 回收章节
    reveal_event_id VARCHAR(100),
    status ENUM('pending','revealed','abandoned') NOT NULL DEFAULT 'pending',
    related_foreshadowing_ids JSON,              -- 关联伏笔
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    foreshadowing_type VARCHAR(50) DEFAULT NULL COMMENT '伏笔类型：道具/人物/剧情/世界观/细节/能力/关系/冲突/时间线/规则等',
    concealment_level TINYINT DEFAULT NULL COMMENT '埋设隐蔽度 1-10（1明显~10极隐蔽）',
    misleading_info VARCHAR(500) DEFAULT NULL COMMENT '误导信息（迷惑读者的虚假线索）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_foreshadowing (book_id, foreshadowing_id),
    INDEX idx_foreshadowing_chapter (book_id, status, setup_chapter),
    INDEX idx_foreshadowing_reveal (book_id, reveal_chapter),
    FOREIGN KEY (setup_event_id) REFERENCES timeline_event(event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 冲突核心
CREATE TABLE IF NOT EXISTS story_conflict (
    id INT PRIMARY KEY AUTO_INCREMENT,
    conflict_id VARCHAR(100) NOT NULL,
    book_id VARCHAR(50) NOT NULL,
    conflict_title VARCHAR(200) NOT NULL,
    conflict_type VARCHAR(50),                   -- 对抗/资源争夺/价值观冲突/欲望冲突
    conflict_desc TEXT,
    side_a VARCHAR(500),                         -- 冲突方A（实体ID/势力名）
    side_b VARCHAR(500),                         -- 冲突方B
    start_chapter INT NOT NULL,
    end_chapter INT,
    current_status VARCHAR(50) DEFAULT '升级',    -- 升级/胶着/解决
    escalated_event_ids JSON,                    -- 关键推进事件ID
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_conflict (book_id, conflict_id),
    UNIQUE KEY uk_conflict_book_title (book_id, conflict_title),   -- 006：同冲突单行（业务键）
    INDEX idx_conflict_status (book_id, current_status, start_chapter)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 设定规则校验点（防战力崩坏/防 LLM 违反设定）
CREATE TABLE IF NOT EXISTS rule_check (
    id INT PRIMARY KEY AUTO_INCREMENT,
    rule_id VARCHAR(100) NOT NULL,
    book_id VARCHAR(50) NOT NULL,
    rule_name VARCHAR(200) NOT NULL,
    rule_type ENUM('cap','cost','balance_lock','condition','other') NOT NULL DEFAULT 'other',
    rule_content TEXT NOT NULL,                  -- 能力上限/代价/平衡锁原文
    subject_entity_id VARCHAR(100),              -- 规则适用实体
    subject_ability VARCHAR(200),                -- 适用能力/术式
    valid_from_chapter INT NOT NULL DEFAULT 1,
    valid_to_chapter INT DEFAULT 0,              -- 0=永久有效
    last_check_result VARCHAR(500),              -- 最近一次设定校验结论（违反/通过/存疑）
    source_chunk_ids VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT NULL,          -- 置信度 0~1；NULL = 未复核
    review_status VARCHAR(20) DEFAULT NULL,        -- NULL=未复核 / confirmed / fixed / ignored
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_rule_check (book_id, rule_id),
    UNIQUE KEY uk_rule_book_name (book_id, rule_name),   -- 006：同规则单行（业务键）
    INDEX idx_rule_check_entity (book_id, rule_type, subject_entity_id),
    FOREIGN KEY (subject_entity_id) REFERENCES entity(entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 一致性校验问题表（子任务 07 建表；08 review 状态机；00 §4.1 正本）
CREATE TABLE IF NOT EXISTS validation_issue (
    id INT PRIMARY KEY AUTO_INCREMENT,
    issue_id VARCHAR(64) NOT NULL,                -- vis_{snowflake}
    book_id VARCHAR(50) NOT NULL,
    job_id VARCHAR(64) NOT NULL,
    chapter_id VARCHAR(64),                       -- 关联章节（可能为空=书级问题）
    record_type VARCHAR(50) NOT NULL,             -- entity_relation/entity_snapshot/timeline_event/rule_check...
    issue_type VARCHAR(50) NOT NULL,              -- semantic_conflict/timeline_paradox/state_jump/rule_violation/unsupported_change
    severity ENUM('info','warning','critical') NOT NULL DEFAULT 'warning',
    status ENUM('pending','confirmed','fixed','ignored') NOT NULL DEFAULT 'pending',
    description TEXT,                             -- 冲突说明
    original_value VARCHAR(2000),                 -- 冲突前已入库值（保留，不覆写）
    suggested_value VARCHAR(2000),                -- 新抽取值（挂起待人工裁决）
    target_id VARCHAR(100),                       -- 目标知识行业务键（复核回写定位用）
    resolved_by VARCHAR(100),                     -- 人工确认人/方式
    resolved_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_validation_issue (issue_id),
    INDEX idx_validation_issue_book (book_id, status),
    INDEX idx_validation_issue_job (job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 回滚：见独立文件 rollback.sql（本文件为初始化脚本，不得含回滚，
-- 否则会被容器/手动执行时立即删库）。
-- ============================================================
