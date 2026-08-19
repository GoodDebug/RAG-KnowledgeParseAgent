# -*- coding: utf-8 -*-
"""
设定规则解构 Agent（子任务 05）—— 抽规则校验点。

照 03/04 模板：调共享 `llm_runner`，导入即注册（经 `agents/__init__.py` 总入口），CLI 独立运行。
subject_entity_name 用实体名（persist 期 06 resolver 解析 entity_id）。
"""
import argparse
import json
import logging

import novel.llm_runner as llm_runner
from novel.agents.registry import register_extractor

logger = logging.getLogger("novel.agents.rule")


def extract(scene_text: str, shrink_level: int = 0, *, hint_entities: list[str] | None = None) -> list[dict]:
    """设定规则抽取：rule_name/rule_type/rule_content/subject_entity_name/subject_ability/生效区间。

    :param scene_text: 章节场景原文
    :param shrink_level: 起始缩窗级别
    :param hint_entities: 跨 Agent 命名对齐名单（003 P1-1；默认 None，名单来源由 06 resolver 跨章建全量名单后提供）
    :return: 规则 dict 列表
    """
    return llm_runner.extract("rule", scene_text, shrink_level, hint_entities=hint_entities)


register_extractor("rule", extract)


def main() -> None:
    """CLI：独立运行设定规则抽取。"""
    parser = argparse.ArgumentParser(description="设定规则解构 Agent（独立运行）")
    parser.add_argument("--text", required=True, help="待解构的章节/场景文本")
    args = parser.parse_args()
    print(json.dumps(extract(args.text, 0), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
