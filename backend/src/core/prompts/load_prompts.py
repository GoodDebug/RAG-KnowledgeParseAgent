# -*- coding: utf-8 -*-
"""
提示词模板加载与渲染（提示词工程优化，顶层计划外）。

- 从 core/prompts/<name>.yaml 读取模板（六要素分段）。
- render_* 函数把各段拼接为最终 prompt 字符串。
- 模板含 version key；加载失败由调用方（core/prompts/__init__.py）兜底。
- 占位符用 str.replace 填充（避免用户输入含 { } 触发 .format 异常）。
"""
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent

_CACHE: dict = {}


def _fill(section: str, **kwargs) -> str:
    """用 str.replace 填充占位符（对用户输入安全，无需转义大括号）。"""
    for key, value in kwargs.items():
        section = section.replace("{" + key + "}", value)
    return section


def load_prompt(name: str) -> dict:
    """读取 <name>.yaml 模板，返回 {段名: 文本}。失败抛异常（由上层兜底）。"""
    if name in _CACHE:
        return _CACHE[name]
    path = _PROMPTS_DIR / f"{name}.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _CACHE[name] = data
    return data


def render_system_prompt(tools: str = "", history_summary: str = "") -> str:
    """把模板 C（system_prompt.yaml）按六要素拼接为最终 System Prompt。"""
    t = load_prompt("system_prompt")
    background = _fill(t.get("background", "") or "", tools=tools, history_summary=history_summary)
    return (
        "# 角色\n" + (t.get("role", "") or "") + "\n\n"
        "# 任务\n" + (t.get("task", "") or "") + "\n\n"
        "# 背景/上下文\n" + background + "\n\n"
        "# 输入数据\n" + (t.get("input", "") or "") + "\n\n"
        "# 输出格式\n" + (t.get("output", "") or "") + "\n\n"
        "# 质量与约束\n" + (t.get("quality", "") or "")
    )


def render_free_system_prompt(history_summary: str = "") -> str:
    """把模板（system_prompt_free.yaml）按六要素拼接为自由问答 System Prompt（无 tools 段）。"""
    t = load_prompt("system_prompt_free")
    background = _fill(t.get("background", "") or "", history_summary=history_summary)
    return (
        "# 角色\n" + (t.get("role", "") or "") + "\n\n"
        "# 任务\n" + (t.get("task", "") or "") + "\n\n"
        "# 背景/上下文\n" + background + "\n\n"
        "# 输入数据\n" + (t.get("input", "") or "") + "\n\n"
        "# 输出格式\n" + (t.get("output", "") or "") + "\n\n"
        "# 质量与约束\n" + (t.get("quality", "") or "")
    )


def render_prompt_optimizer_template(user_input_rules: str, user_message: str) -> str:
    """把模板 B（prompt_optimizer_template.yaml）拼接为优化调用 prompt。"""
    t = load_prompt("prompt_optimizer_template")
    background = _fill(t.get("background", "") or "", user_input_rules=user_input_rules)
    input_section = _fill(t.get("input", "") or "", user_message=user_message)
    return (
        "# 角色\n" + (t.get("role", "") or "") + "\n\n"
        "# 背景/上下文\n" + background + "\n\n"
        "# 输入数据\n" + input_section + "\n\n"
        "# 任务\n" + (t.get("task", "") or "") + "\n\n"
        "# 输出格式\n" + (t.get("output", "") or "")
    )


def get_user_input_rules() -> str:
    """返回模板 A（user_input_template.yaml）的 rules 段，供模板 B 组装。"""
    t = load_prompt("user_input_template")
    return t.get("rules", "") or ""


def render_intent_classifier_prompt(user_message: str, categories: list[str]) -> str:
    """把模板（intent_classifier_template.yaml）拼接为意图分类 prompt（加分项①，Spec-E）。"""
    t = load_prompt("intent_classifier")
    categories_section = _fill(t.get("categories", "") or "", categories="、".join(categories))
    input_section = _fill(t.get("input", "") or "", user_message=user_message)
    return (
        "# 角色\n" + (t.get("role", "") or "") + "\n\n"
        "# 类别\n" + categories_section + "\n\n"
        "# 输入数据\n" + input_section + "\n\n"
        "# 任务\n" + (t.get("task", "") or "") + "\n\n"
        "# 输出格式\n" + (t.get("output", "") or "")
    )


def render_followup_prompt(user_message: str, answer: str, intent: str, count: int) -> str:
    """把模板（followup_template.yaml）拼接为追问建议生成 prompt（加分项②，Spec-E）。"""
    t = load_prompt("followup")
    input_section = _fill(
        t.get("input", "") or "",
        user_message=user_message, answer=answer, intent=intent,
    )
    task_section = _fill(t.get("task", "") or "", count=str(count))
    return (
        "# 角色\n" + (t.get("role", "") or "") + "\n\n"
        "# 输入数据\n" + input_section + "\n\n"
        "# 任务\n" + task_section + "\n\n"
        "# 输出格式\n" + (t.get("output", "") or "")
    )
