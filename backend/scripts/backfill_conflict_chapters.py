# -*- coding: utf-8 -*-
"""007 C2 · 跨章冲突 start/end 全局回填（幂等，默认 dry-run；--apply 才写库）。

背景（011/010）：跨章节冲突（本章只提及、起始章在别处）单章内无法确定 start_chapter，
已由 006 兜底为当前章（`or chapter_index`）；本脚本按参与实体最早出现章推断真实 start，
`current_status=解决` 且 end 缺失 → 用最后一次出现章推断 end。

推断规则（按 (book_id, conflict_title)）：
  1. start_chapter 缺失/0 → 该书 entity_snapshot（或 timeline_event_entity 联 entity）中
     涉及 side_a / side_b（实体名）的最早 chapter_index；无匹配 → 保留现状。
  2. end_chapter 缺失 且 current_status=解决 → 该冲突 side 实体最后一次出现的 chapter_index。
只补缺失（WHERE start 缺失/0、end 缺失+解决），不动正确值；dry-run 输出计划。

用法：
  python backfill_conflict_chapters.py [--book_id doc_1_1]   # dry-run
  python backfill_conflict_chapters.py --apply [--book_id ...]  # 执行（先备份 story_conflict）
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


def _first_seen(cur, book_id: str, names: list[str]) -> int | None:
    """side 实体最早出现章（entity_snapshot 优先，其次 timeline_event_entity）。"""
    if not names:
        return None
    # 直接按实体名匹配（snapshot.entity_name / event_entity 联 entity.entity_name）
    cur.execute(
        "SELECT MIN(chapter_index) FROM entity_snapshot WHERE book_id=:b AND entity_name IN %s",
        (tuple(names),),
    )
    v = cur.fetchone()[0]
    if v is not None:
        return v
    cur.execute(
        "SELECT MIN(tee.chapter_index) FROM timeline_event_entity tee "
        "JOIN entity e ON e.entity_id = tee.entity_id "
        "WHERE tee.book_id=:b AND e.entity_name IN %s", (tuple(names),))
    return cur.fetchone()[0]


def _last_seen(cur, book_id: str, names: list[str]) -> int | None:
    if not names:
        return None
    cur.execute(
        "SELECT MAX(chapter_index) FROM entity_snapshot WHERE book_id=:b AND entity_name IN %s",
        (tuple(names),),
    )
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser(description="007 C2 跨章冲突 start/end 回填（幂等）")
    ap.add_argument("--book_id", default=None, help="限定某书；缺省全书")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = _conn()
    cur = conn.cursor()
    where = "WHERE (start_chapter = 0 OR start_chapter IS NULL OR (end_chapter IS NULL AND current_status='解决'))"
    params = {}
    if args.book_id:
        where += " AND book_id=%s"
        params["book_id"] = args.book_id
    cur.execute(f"SELECT conflict_id, book_id, conflict_title, side_a, side_b, start_chapter, end_chapter, current_status "
                f"FROM story_conflict {where}", tuple(params.values()) if params else ())
    rows = cur.fetchall()
    total = 0
    for cid, bid, title, a, b, start, end, status in rows:
        names = [x for x in (a, b) if x]
        n_start = start if start else None
        if n_start is None or n_start == 0:
            n_start = _first_seen(cur, bid, names)
        n_end = end
        if n_end is None and status == "解决":
            n_end = _last_seen(cur, bid, names)
        if n_start is None and (n_end is None or n_end == end):
            continue  # 无可推断值 → 保留现状
        plan = []
        if (n_start is not None and (start is None or start == 0)):
            plan.append(f"start {start}→{n_start}")
        if n_end is not None and n_end != end:
            plan.append(f"end {end}→{n_end}")
        if not plan:
            continue
        total += 1
        if args.apply:
            cur.execute("UPDATE story_conflict SET start_chapter=COALESCE(%s, start_chapter), "
                        "end_chapter=COALESCE(%s, end_chapter) WHERE conflict_id=%s",
                        (n_start, n_end, cid))
            conn.commit()
        print(f"{'[apply]' if args.apply else '[dry ]'} {title[:30]} {bid}: {', '.join(plan)}")
    cur.close()
    conn.close()
    print(f"合计：{total} 条冲突待回填")
    if not args.apply:
        print("提示：dry-run 仅输出计划。执行加 --apply；先备份：mysqldump ai_customer_service story_conflict > backup.sql")
    sys.exit(0)


if __name__ == "__main__":
    main()
