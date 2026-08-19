# -*- coding: utf-8 -*-
"""
实体快照解构 Agent（子任务 04）—— 抽每实体本章状态。

照 03 `entity_agent` 模板：`extract(scene, shrink)` 调共享 `llm_runner`（JSON + Pydantic + 缩窗重试），
导入即注册（经 `agents/__init__.py` 总入口触发）；CLI 独立运行验证。
"""
import argparse
import json
import logging

import novel.llm_runner as llm_runner
from novel.agents.registry import register_extractor

logger = logging.getLogger("novel.agents.entity_snapshot")


def extract(scene_text: str, shrink_level: int = 0, *,
            hint_entities: list[str] | None = None,
            prev_snapshot_context: str | None = None) -> list[dict]:
    """实体快照抽取：每实体本章状态（status_desc + 固定键 attributes + 三态）。

    :param scene_text: 章节场景原文
    :param shrink_level: 起始缩窗级别（0=全文）
    :param hint_entities: 跨 Agent 命名对齐名单（003 P1-1；默认 None，名单来源由 06 resolver 跨章建全量名单后提供）
    :param prev_snapshot_context: 历史已入库快照摘要（最新可用，因章节并行解构可能非紧邻上一章；
        二阶段 02 增量提取背景参考；默认 None）
    :return: 快照 dict 列表 [{entity_name, status_desc, source_fragment, attributes, three_state}]
    """
    return llm_runner.extract(
        "entity_snapshot", scene_text, shrink_level,
        hint_entities=hint_entities, prev_snapshot_context=prev_snapshot_context,
    )


register_extractor("entity_snapshot", extract)


def main() -> None:
    """CLI：独立运行实体快照抽取。"""
    parser = argparse.ArgumentParser(description="实体快照解构 Agent（独立运行）")
    parser.add_argument("--text", required=True, help="待解构的章节/场景文本")
    args = parser.parse_args()
    print(json.dumps(extract(args.text, 0), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
