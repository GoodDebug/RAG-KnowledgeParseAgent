# -*- coding: utf-8 -*-
"""007 C3 · 存量重复进行中关系行清理（幂等，默认 dry-run；--apply 才写库）。

背景（005 Phase 2 残留）：旧逻辑逐章 upsert 关系（uk 含 start_chapter）→ 同 (src,tgt,type)
跨章产生多行进行中（end=0），如 doc_5_4828 的 belong_to/possess。本脚本一次性收敛：
  同 (book_id, source_entity_id, target_entity_id, relation_type) 且 end=0 的多行 →
  保留最早 start 行（并把最新一条 desc/weight 并入），**删除**其余冗余观察行
  （它们是同一区间的重复观测，删除等价于关闭冗余区间，避免 end<start 的无效区间）。

用法：
  python merge_dup_relations.py [--book_id doc_1_1]   # dry-run：输出计划
  python merge_dup_relations.py --apply [--book_id ...]  # 执行（先备份 entity_relation）
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


def main():
    ap = argparse.ArgumentParser(description="007 C3 重复进行中关系 merge（幂等）")
    ap.add_argument("--book_id", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = _conn()
    cur = conn.cursor()
    gwhere = ""
    params: tuple = ()
    if args.book_id:
        gwhere = " AND book_id=%s"
        params = (args.book_id,)

    cur.execute(
        f"SELECT book_id, source_entity_id, target_entity_id, relation_type, COUNT(*) "
        f"FROM entity_relation WHERE end_chapter=0 {gwhere} "
        f"GROUP BY book_id, source_entity_id, target_entity_id, relation_type HAVING COUNT(*)>1", params)
    groups = cur.fetchall()
    total = 0
    for bid, src, tgt, rtype, cnt in groups:
        cur.execute(
            "SELECT relation_id, start_chapter, relation_desc, relation_weight FROM entity_relation "
            "WHERE book_id=%s AND source_entity_id=%s AND target_entity_id=%s AND relation_type=%s AND end_chapter=0 "
            "ORDER BY start_chapter", (bid, src, tgt, rtype))
        rows = cur.fetchall()
        keep = rows[0]              # 最早 start 行
        extras = rows[1:]
        latest = extras[-1]         # 最新一条（desc/weight 并入）
        if args.apply:
            cur.execute("UPDATE entity_relation SET relation_desc=%s, relation_weight=%s "
                        "WHERE relation_id=%s", (latest[2] or keep[2], latest[3], keep[0]))
            for extra in extras:
                cur.execute("DELETE FROM entity_relation WHERE relation_id=%s", (extra[0],))
            conn.commit()
        total += len(extras)
        print(f"{'[apply]' if args.apply else '[dry ]'} {bid} {src[:12]}→{tgt[:12]} [{rtype}] "
              f"保留 start={keep[1]}，收敛 {len(extras)} 行重复观测")
    cur.close()
    conn.close()
    print(f"合计：{total} 行冗余观测待清理")
    if not args.apply:
        print("提示：dry-run 仅输出计划。执行加 --apply；先备份：mysqldump ai_customer_service entity_relation > backup.sql")
    sys.exit(0)


if __name__ == "__main__":
    main()
