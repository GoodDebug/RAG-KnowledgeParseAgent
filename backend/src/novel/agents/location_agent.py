# -*- coding: utf-8 -*-
"""
地点解构 Agent（子任务 05）—— 抽地点层级 + 本章状态。

照 03/04 模板：调共享 `llm_runner`，导入即注册（经 `agents/__init__.py` 总入口），CLI 独立运行。
产出支撑 location（04 复用表）与 location_snapshot 两张表。
"""
import argparse
import json
import logging

import novel.llm_runner as llm_runner
from novel.agents.registry import register_extractor

logger = logging.getLogger("novel.agents.location")


def extract(scene_text: str, shrink_level: int = 0, *, hint_entities: list[str] | None = None) -> list[dict]:
    """地点抽取：层级（name/level/parent）+ 本章状态（status_desc/special_rules）。

    :param scene_text: 章节场景原文
    :param shrink_level: 起始缩窗级别
    :param hint_entities: 跨 Agent 命名对齐名单（003 P1-1；默认 None，名单来源由 06 resolver 跨章建全量名单后提供）
    :return: 地点 dict 列表
    """
    return llm_runner.extract("location", scene_text, shrink_level, hint_entities=hint_entities)


register_extractor("location", extract)


def main() -> None:
    """CLI：独立运行地点抽取。"""
    parser = argparse.ArgumentParser(description="地点解构 Agent（独立运行）")
    parser.add_argument("--text", required=True, help="待解构的章节/场景文本")
    args = parser.parse_args()
    print(json.dumps(extract(args.text, 0), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
