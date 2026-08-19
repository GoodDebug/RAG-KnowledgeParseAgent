# -*- coding: utf-8 -*-
"""
实体解构 Agent（子任务 03）—— 8 个解构 Agent 的第一个真实实现。

职责：把一章场景文本喂给 `llm_runner`，抽取出实体列表（规范名 + 别名 + 类型 + 描述）。

设计：
  - `extract(scene_text, shrink_level)` 是 registry 约定的抽取器签名（`fn(scene, shrink) -> list[dict]`），
    图节点 `run_agent("entity", state)` 直接调它；
  - **导入即注册**：本模块被 import 时 `register_extractor("entity", extract)`，
    02 的 registry 立即可用（04/05 其余 Agent 同一模式）；
  - `main()` 是 CLI 入口：`python -m novel.agents.entity_agent --text <样例>` 独立验证（顶层 Spec 验收项 4）。
"""
import argparse
import json
import logging
import sys

import novel.llm_runner as llm_runner
from novel.agents.registry import register_extractor

logger = logging.getLogger("novel.agents.entity")


def extract(scene_text: str, shrink_level: int = 0, *, hint_entities: list[str] | None = None) -> list[dict]:
    """实体抽取：调共享 `llm_runner.extract`（JSON 解析 + Pydantic 校验 + 缩窗重试）。

    :param scene_text: 章节场景原文
    :param shrink_level: 起始缩窗级别（0=全文）
    :param hint_entities: 跨 Agent 命名对齐名单（003 P1-1；默认 None，名单来源由 06 resolver 跨章建全量名单后提供）
    :return: 实体 dict 列表 [{name, aliases[], type, description}]
    :raises llm_runner.LLMExtractError: 缩窗耗尽仍失败（run_agent 捕获进 errors）
    """
    return llm_runner.extract("entity", scene_text, shrink_level, hint_entities=hint_entities)


# 导入即注册：让 02 的 agents.registry 立即可用
register_extractor("entity", extract)


def main() -> None:
    """CLI：独立运行实体抽取（用于手动验证 / 调试 prompt）。"""
    parser = argparse.ArgumentParser(description="实体解构 Agent（独立运行）")
    parser.add_argument("--text", required=True, help="待解构的章节/场景文本")
    parser.add_argument("--shrink", type=int, default=0, help="起始缩窗级别")
    args = parser.parse_args()

    result = extract(args.text, args.shrink)
    # stdout 只输出纯 JSON（CLI 契约，测试 json.loads 依赖）；信息行走 stderr，不污染 stdout
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"共抽取 {len(result)} 个实体", file=sys.stderr)


if __name__ == "__main__":
    main()
