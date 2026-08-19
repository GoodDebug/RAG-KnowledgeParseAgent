-- ============================================================
-- 小说解构 · confidence / review_status 迁移脚本（大修002 Sub-5）
-- 依据：docs/开发阶段文档/spec/前端规划/大修002/大修002-Sub5-confidence闭环.md §4.1
-- 内容：
--   * 11 张知识表（entity / entity_alias / entity_snapshot / entity_relation /
--     timeline_event / timeline_event_entity / location / location_snapshot /
--     foreshadowing / story_conflict / rule_check）各加：
--       confidence    DECIMAL(3,2)  NULL   -- 置信度 0~1；NULL = 未复核
--       review_status VARCHAR(20)   NULL   -- NULL=未复核 / confirmed / fixed / ignored
--   * validation_issue 加：target_id VARCHAR(100) NULL（目标知识行业务键，复核回写定位用）
-- 幂等：每个 ADD COLUMN 前先查 information_schema.COLUMNS，列已存在则跳过（可重复执行不报错）。
-- 加列不删列；schema 正本见 models.py 与 init_db.sql。
-- 执行：mysql -u root -p < scripts/migration_confidence.sql
-- ============================================================

-- ---------------- entity ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.entity ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.entity ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- entity_alias ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity_alias' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.entity_alias ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity_alias' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.entity_alias ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- entity_snapshot ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity_snapshot' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.entity_snapshot ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity_snapshot' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.entity_snapshot ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- entity_relation ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity_relation' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.entity_relation ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='entity_relation' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.entity_relation ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- timeline_event ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='timeline_event' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.timeline_event ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='timeline_event' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.timeline_event ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- timeline_event_entity ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='timeline_event_entity' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.timeline_event_entity ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='timeline_event_entity' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.timeline_event_entity ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- location ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='location' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.location ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='location' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.location ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- location_snapshot ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='location_snapshot' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.location_snapshot ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='location_snapshot' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.location_snapshot ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- foreshadowing ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='foreshadowing' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.foreshadowing ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='foreshadowing' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.foreshadowing ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- story_conflict ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='story_conflict' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.story_conflict ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='story_conflict' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.story_conflict ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- rule_check ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='rule_check' AND COLUMN_NAME='confidence') = 0,
    'ALTER TABLE ai_customer_service.rule_check ADD COLUMN confidence DECIMAL(3,2) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='rule_check' AND COLUMN_NAME='review_status') = 0,
    'ALTER TABLE ai_customer_service.rule_check ADD COLUMN review_status VARCHAR(20) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------- validation_issue（支撑列 target_id） ----------------
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA='ai_customer_service' AND TABLE_NAME='validation_issue' AND COLUMN_NAME='target_id') = 0,
    'ALTER TABLE ai_customer_service.validation_issue ADD COLUMN target_id VARCHAR(100) NULL',
    'SELECT 1'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;
