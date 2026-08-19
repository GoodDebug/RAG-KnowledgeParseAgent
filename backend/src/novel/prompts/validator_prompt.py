# -*- coding: utf-8 -*-
"""
validator（Layer 2 一致性校验）专属 Prompt + JSON Schema（子任务 08）。

职责：给 LLM 一批待查抽取记录，检查**语义冲突**（人物生死 / 关系方向 / 时间错位 / 阶段标记），
产出疑点 flags。**只作 flags**——输出直接写 `validation_issue(pending)`，绝不 auto-fix 11 表。

复用 `llm_runner`（JSON 解析 + 缩窗重试）；AGENT_PROMPTS 登记 `validator` 条目。
"""
from pydantic import BaseModel, Field

from novel.prompts.base import BASE_SYSTEM_PROMPT

_FINDING_SEVERITIES = ("info", "warning", "critical")


class ValidatorFinding(BaseModel):
    """单条疑点（Layer 2 flags）。"""

    record_type: str = Field(description="疑点记录类型：entity_relation/entity_snapshot/timeline_event/rule_check/...")
    target: str = Field(min_length=1, description="记录名/事件标题（定位到具体记录）")
    conflict_desc: str = Field(default="", description="语义冲突说明（人物生死/关系方向/时间错位/阶段标记）")
    severity: str = Field(default="warning", description=f"严重度：{'/'.join(_FINDING_SEVERITIES)}")
    suggested_value: str = Field(default="", description="修正建议（供人工裁决，不作 auto-fix）")


class ValidatorOutput(BaseModel):
    """validator 整体输出契约。"""

    findings: list[ValidatorFinding] = Field(default_factory=list)


def build_prompt(batch_text: str) -> dict:
    """组装 validator 的 LLM 消息。

    :param batch_text: 待查抽取记录批次（JSON 文本，由 run_book_validator 序列化传入）
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    prompt = (
        "任务：你是一致性校验 Agent。检查以下抽取记录批次中的**语义冲突**，输出疑点清单。\n"
        "重点检查四类冲突：\n"
        "  1. 人物生死矛盾（同一人既被救又被杀死、复活无事件）；\n"
        "  2. 关系方向矛盾（同一对实体关系方向冲突、主客颠倒）；\n"
        "  3. 时间错位（事件时序矛盾、阶段归属错误）；\n"
        "  4. 阶段标记错误（状态翻转无事件支撑、战力/封印阶梯跳级或倒退）。\n"
        "输出 JSON 结构："
        '{"findings": [{"record_type": 记录类型, "target": 记录名/事件标题, '
        '"conflict_desc": 冲突说明, "severity": "info|warning|critical", '
        '"suggested_value": 修正建议}]}\n'
        "要求：\n"
        "  1. 只输出确认存在语义冲突的疑点；无明显冲突返回空 findings。\n"
        "  2. severity 只作参考分级，最终由人工裁决；你**不修改任何数据**，只上报疑点。\n"
        "  3. 只输出 JSON，不要任何额外文本。\n\n"
        f"待查记录批次：\n{batch_text}"
    )
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": prompt}
