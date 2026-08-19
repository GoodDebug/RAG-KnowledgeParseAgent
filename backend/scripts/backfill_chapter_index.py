# -*- coding: utf-8 -*-
"""005 · 存量 0/空章节字段回填（幂等，默认 dry-run；--apply 才写库）。

覆盖（仅修 0/空，不动正确值）：
  1. entity/location.first·last = 对应 snapshot 的 MIN/MAX chapter_index；
  2. timeline_event.start = 该事件 timeline_event_entity 的 MIN chapter_index；
  3. entity_relation.start = 0 → 该行 chapter_index；
  4. rule_check.valid_from = 0 → 1（heuristic：规则通常首章生效）；
  5. foreshadowing.setup = 0 → 1（heuristic：无法从行内推断，取首章）。

用法：
  python backfill_chapter_index.py            # dry-run：打印计划行数
  python backfill_chapter_index.py --apply    # 执行（先备份：mysqldump ... > backup.sql）
"""
import argparse
import os
import sys

import pymysql

_ENV = os.path.join(os.path.dirname(__file__), "..", "src", ".env")


def _env_val(key: str) -> str:
    with open(_ENV, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def _conn():
    return pymysql.connect(
        host=_env_val("MYSQL_HOST") or "127.0.0.1",
        port=int(_env_val("MYSQL_PORT") or 3306),
        user=_env_val("MYSQL_USER") or "ai_customer",
        password=_env_val("MYSQL_PASSWORD"),
        db=_env_val("MYSQL_DB") or "ai_customer_service",
        charset="utf8mb4",
    )


# (描述, update_sql, count_sql)
STEPS = [
    ("entity.first/last <- snapshot MIN/MAX",
     """UPDATE entity e JOIN (
            SELECT entity_id, MIN(chapter_index) AS fi, MAX(chapter_index) AS li
            FROM entity_snapshot GROUP BY entity_id
        ) s ON s.entity_id = e.entity_id
        SET e.first_chapter_index = s.fi, e.last_chapter_index = s.li""",
     "SELECT COUNT(*) FROM entity WHERE first_chapter_index = 0"),
    ("location.first/last <- location_snapshot MIN/MAX",
     """UPDATE location l JOIN (
            SELECT location_id, MIN(chapter_index) AS fi, MAX(chapter_index) AS li
            FROM location_snapshot GROUP BY location_id
        ) s ON s.location_id = l.location_id
        SET l.first_chapter_index = s.fi, l.last_chapter_index = s.li""",
     "SELECT COUNT(*) FROM location WHERE first_chapter_index = 0"),
    ("timeline_event.start <- event_entity MIN",
     """UPDATE timeline_event te JOIN (
            SELECT event_id, MIN(chapter_index) AS fi
            FROM timeline_event_entity GROUP BY event_id
        ) s ON s.event_id = te.event_id
        SET te.start_chapter = s.fi""",
     "SELECT COUNT(*) FROM timeline_event WHERE start_chapter = 0"),
    ("entity_relation.start=0 <- chapter_index",
     "UPDATE entity_relation SET start_chapter = chapter_index WHERE start_chapter = 0",
     "SELECT COUNT(*) FROM entity_relation WHERE start_chapter = 0"),
    ("rule_check.valid_from=0 -> 1（heuristic）",
     "UPDATE rule_check SET valid_from_chapter = 1 WHERE valid_from_chapter = 0",
     "SELECT COUNT(*) FROM rule_check WHERE valid_from_chapter = 0"),
    ("foreshadowing.setup=0 -> 1（heuristic）",
     "UPDATE foreshadowing SET setup_chapter = 1 WHERE setup_chapter = 0",
     "SELECT COUNT(*) FROM foreshadowing WHERE setup_chapter = 0"),
]


def main():
    ap = argparse.ArgumentParser(description="005 章节字段回填（幂等）")
    ap.add_argument("--apply", action="store_true", help="执行写库（默认 dry-run）")
    args = ap.parse_args()

    conn = _conn()
    cur = conn.cursor()
    total = 0
    for desc, sql, count_sql in STEPS:
        cur.execute(count_sql)
        n = cur.fetchone()[0]
        if args.apply:
            cur.execute(sql)
            conn.commit()
            n = cur.rowcount
        total += n
        print(f"{'[apply]' if args.apply else '[dry ]'} {desc}: {n} 行")
    cur.close()
    conn.close()
    print(f"合计：{total} 行")
    if not args.apply:
        print("提示：dry-run 仅探测命中行数（未写库）。执行加 --apply；先备份："
              "mysqldump ai_customer_service entity location timeline_event "
              "timeline_event_entity entity_relation rule_check foreshadowing > backup.sql")
    sys.exit(0)


if __name__ == "__main__":
    main()
