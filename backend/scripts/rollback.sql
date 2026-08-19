
-- ============================================================
-- 回滚 SQL（Spec-A，逆序：先子表后父表，再删库）
-- 独立于 init_db.sql —— init 脚本不得包含回滚（会被容器/手动执行时立即删库）。
-- 用途：需要重置数据库时手动执行：mysql -u root -p < rollback.sql
-- ============================================================
DROP TABLE IF EXISTS ai_customer_service.messages;
DROP TABLE IF EXISTS ai_customer_service.sessions;
DROP TABLE IF EXISTS ai_customer_service.documents;
DROP TABLE IF EXISTS ai_customer_service.users;
-- 小说解构（子任务 01-05）——逆序：先子表后父表
DROP TABLE IF EXISTS ai_customer_service.rule_check;
DROP TABLE IF EXISTS ai_customer_service.story_conflict;
DROP TABLE IF EXISTS ai_customer_service.foreshadowing;
DROP TABLE IF EXISTS ai_customer_service.location_snapshot;
DROP TABLE IF EXISTS ai_customer_service.timeline_event_entity;
DROP TABLE IF EXISTS ai_customer_service.timeline_event;
DROP TABLE IF EXISTS ai_customer_service.entity_relation;
DROP TABLE IF EXISTS ai_customer_service.entity_snapshot;
DROP TABLE IF EXISTS ai_customer_service.location;
DROP TABLE IF EXISTS ai_customer_service.entity_alias;
DROP TABLE IF EXISTS ai_customer_service.entity;
DROP TABLE IF EXISTS ai_customer_service.deconstruct_chapter_state;
DROP TABLE IF EXISTS ai_customer_service.deconstruct_job;
DROP TABLE IF EXISTS ai_customer_service.novel_chapter;
DROP TABLE IF EXISTS ai_customer_service.validation_issue;

-- 删除整库
DROP DATABASE IF EXISTS ai_customer_service;
DROP INDEX uk_conflict_book_title ON story_conflict;
DROP INDEX uk_rule_book_name ON rule_check;
