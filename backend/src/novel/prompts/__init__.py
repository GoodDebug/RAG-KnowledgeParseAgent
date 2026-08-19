# -*- coding: utf-8 -*-
"""
解构 Agent Prompt 注册表（子任务 03）。

每个解构 Agent 的 prompt 模块在这里登记：
  AGENT_PROMPTS[agent_name] = {
      "build":        build_prompt(scene_text) -> {"system_prompt", "prompt"}   # 组装 LLM 消息
      "schema":       Pydantic 模型（强校验输出 JSON）
      "result_field": 校验后取哪一字段作为"条目列表"（entity → "entities"）
  }

llm_runner.extract(agent_name, ...) 据此查表组装调用。04/05 其余 Agent 照此模式登记。
"""
from novel.prompts import entity_prompt
from novel.prompts import entity_snapshot_prompt
from novel.prompts import relation_prompt
from novel.prompts import timeline_prompt
from novel.prompts import location_prompt
from novel.prompts import foreshadowing_prompt
from novel.prompts import conflict_prompt
from novel.prompts import rule_prompt
from novel.prompts import validator_prompt

AGENT_PROMPTS: dict[str, dict] = {
    "entity": {
        "build": entity_prompt.build_prompt,
        "schema": entity_prompt.EntityOutput,
        "result_field": "entities",
    },
    "entity_snapshot": {
        "build": entity_snapshot_prompt.build_prompt,
        "schema": entity_snapshot_prompt.SnapshotOutput,
        "result_field": "snapshots",
    },
    "relation": {
        "build": relation_prompt.build_prompt,
        "schema": relation_prompt.RelationOutput,
        "result_field": "relations",
    },
    "timeline": {
        "build": timeline_prompt.build_prompt,
        "schema": timeline_prompt.TimelineOutput,
        "result_field": "events",
    },
    "location": {
        "build": location_prompt.build_prompt,
        "schema": location_prompt.LocationOutput,
        "result_field": "locations",
    },
    "foreshadowing": {
        "build": foreshadowing_prompt.build_prompt,
        "schema": foreshadowing_prompt.ForeshadowingOutput,
        "result_field": "foreshadowings",
    },
    "conflict": {
        "build": conflict_prompt.build_prompt,
        "schema": conflict_prompt.ConflictOutput,
        "result_field": "conflicts",
    },
    "rule": {
        "build": rule_prompt.build_prompt,
        "schema": rule_prompt.RuleOutput,
        "result_field": "rules",
    },
    "validator": {
        "build": validator_prompt.build_prompt,
        "schema": validator_prompt.ValidatorOutput,
        "result_field": "findings",
    },
}
