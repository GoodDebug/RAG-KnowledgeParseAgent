# -*- coding: utf-8 -*-
"""
解构 LLM 运行器（共享基础设施，子任务 03 落地，04/05 复用）。

职责：把"调 LLM 拿结构化 JSON"这步做成**可靠可重试**的公共环节——
  1. 用 `create_llm_adapter` 分析型配置（低温）调 LLM；
  2. 严格解析 JSON（剥代码围栏/前后杂讯）；
  3. Pydantic 强校验（结构不合法 = 失败）；
  4. 失败 → **缩窗重试**（scene 取前一半，最多 `NOVEL_AGENT_MAX_SHRINK` 次）。

设计要点：JSON/schema 错误在**这里闭环**（不改 LangGraph 图，顶层 Spec 已钉死 #14）；
`run_agent` 捕获抛出的 `LLMExtractError` 写进 errors（validate 判 failed）。
"""
import json
import logging
import os

from pydantic import ValidationError

from novel.config import (
    deconstruct_llm_max_tokens,
    deconstruct_llm_temperature,
    deconstruct_llm_timeout,
    novel_agent_max_shrink,
)
from novel.prompts import AGENT_PROMPTS
from LLM.llm_adapters import create_llm_adapter

logger = logging.getLogger("novel.llm_runner")


class LLMExtractError(Exception):
    """LLM 抽取失败（JSON 非法/校验不过/缩窗耗尽）。"""


# ---------- LLM 客户端（懒加载单例） ----------

_llm = None


def _get_llm():
    """懒加载分析型 LLM 适配器（首次调用时按 env 建，之后复用）。

    与客服链路同一套 `create_llm_adapter`，只是温度用解构专用的 0.2（低温、可复现）。
    """
    global _llm
    if _llm is None:
        _llm = create_llm_adapter(
            interface_format="deepseek",
            model_provider="openai",
            base_url=os.getenv("DeepSeek_API_URL"),
            model_name="deepseek-v4-flash",
            api_key=os.getenv("DeepSeek_API_KEY"),
            temperature=deconstruct_llm_temperature(),
            max_tokens=deconstruct_llm_max_tokens(),
            timeout=deconstruct_llm_timeout(),
        )
    return _llm


# ---------- JSON 解析（容错） ----------

def _largest_balanced(t: str, open_ch: str, close_ch: str) -> str | None:
    """取最大平衡块（等长取第一个）；引号内的 `{}`/`[]` 不参与配平。无则返回 None。"""
    best: str | None = None
    n = len(t)
    for start in range(n):
        if t[start] != open_ch:
            continue
        depth = 0
        in_str = False
        esc = False
        i = start
        while i < n:
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        cand = t[start:i + 1]
                        if best is None or len(cand) > len(best):
                            best = cand
                        break
            i += 1
    return best


def _wrap_items(items: list, result_field: str | None, raw: str) -> dict:
    """把顶层数组解析结果规整为 dict：
      - [{result_field: [...]}] 单元素包裹 → 直接用内层 dict；
      - 裸 item 数组（[{"name":...}, ...]）→ 包回 {result_field: [items]}；
      - 无 result_field 且仅单 dict → 用之；否则报错触发缩窗重试。
    """
    if len(items) == 1 and isinstance(items[0], dict) and result_field and result_field in items[0]:
        return items[0]
    if result_field:
        return {result_field: items}
    if len(items) == 1 and isinstance(items[0], dict):
        return items[0]
    raise json.JSONDecodeError("裸数组无法包装（缺 result_field）", raw, 0)


def _parse_json_strict(text: str, result_field: str | None = None) -> dict:
    """从 LLM 输出中严格提取 JSON 对象（003 P0-3 鲁棒化）。

    容错策略（比取"第一个平衡 {...}"更稳，降低重试轮数）：
      1. 剥 ```json 围栏 / 前后杂讯；
      2. 优先整段 json.loads（顶层 dict/数组都能直接解析）；
      3. 整段失败 → 取**最大**平衡 {...} 块（等长取第一个，引号内不参与配平）；
      4. 若顶层是数组（[...]）：剥离后取最大 {...}；剥离后若为裸 item 列表，
         包回 {result_field: [items]} 外壳再返回（否则 Pydantic 校验必然失败）；
      5. 都取不到 → json.JSONDecodeError 抛给调用方触发缩窗重试。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        # 剥 ```json ... ``` 围栏
        lines = t.splitlines()
        if lines and lines[0].strip().lstrip("`") == "json":
            lines = lines[1:]
        if lines and lines[-1].strip().strip("`") == "":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    if not t:
        raise json.JSONDecodeError("空输出", t, 0)

    # 1) 整段解析（优先，顶层 dict 或数组都能处理）
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return _wrap_items(obj, result_field, t)
    except json.JSONDecodeError:
        pass

    # 2) 最大平衡 {...} 块
    block = _largest_balanced(t, "{", "}")
    if block:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass

    # 3) 数组外壳：[{...}] 包裹 或 裸 item 数组
    arr = _largest_balanced(t, "[", "]")
    if arr:
        try:
            items = json.loads(arr)
            if isinstance(items, list):
                return _wrap_items(items, result_field, t)
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("未找到合法 JSON", t, 0)


def _retry_feedback(exc) -> str:
    """把校验/解析失败转成一句话字段级反馈（≤200 字符），拼进重试 prompt（003 P0-2）。

    ValidationError → 取第一条最具体的 (loc, input)；
    JSONDecodeError → 用 msg。
    """
    if isinstance(exc, ValidationError):
        try:
            items = json.loads(exc.json())[:1]          # 取第一条最具体
            loc = ".".join(str(p) for p in items["loc"])
            return f"上次输出校验失败：{loc}={items['input']!r} 非法。请修正后只输出合法 JSON。"
        except Exception:
            return f"上次输出校验失败：{str(exc)[:150]}。请修正后只输出合法 JSON。"
    if isinstance(exc, json.JSONDecodeError):
        return f"上次输出不是合法 JSON（{exc.msg}）。请只输出合法 JSON。"
    return "请只输出合法 JSON。"


def _shrink(text: str, level: int) -> str:
    """缩窗：取 scene 前一半的 level 次方（level=1 前一半，level=2 前四分之一…）。

    目的：LLM 输出非法 JSON 时，可能是"文本太长信息混乱"导致——截短重试提高成功率。
    """
    return text[: len(text) // (2 ** level)]


# ---------- 主入口 ----------

def extract(agent_name: str, scene_text: str, shrink_level: int = 0, *,
            hint_entities: list[str] | None = None,
            prev_snapshot_context: str | None = None) -> list[dict]:
    """LLM 结构化抽取：组装 prompt → 调 LLM → 解析 JSON → Pydantic 校验 → 失败缩窗重试。

    :param agent_name: 解构 Agent 名（查 prompts 注册表，entity → 实体抽取）
    :param scene_text: 章节场景原文
    :param shrink_level: 起始缩窗级别（run_agent 透传的 state.shrink_level；0=用全文）
    :param hint_entities: 跨 Agent 命名对齐名单（003 P1-1；非 None 时注入 prompt 对齐规范名，
        默认 None 不注入。名单来源由 06 resolver 跨章建全量名单后传入，本期传 None）
    :param prev_snapshot_context: 历史已入库快照摘要（最新可用，因章节并行解构可能非紧邻上一章；
        二阶段 02 增量提取背景参考；仅 entity_snapshot 消费，非 None 时经 build_prompt 追加历史块。
        **仅当非 None 才传 build_prompt**——其它 Agent 的 build_prompt 无此参数，默认 None 保持向后兼容）
    :return: 结构化条目列表（entity → entities 列表）
    :raises LLMExtractError: JSON 非法/校验不过且缩窗耗尽
    """
    entry = AGENT_PROMPTS[agent_name]
    build_prompt = entry["build"]
    schema = entry["schema"]
    result_field = entry["result_field"]

    def _compose(current: str, feedback: str) -> dict:
        """组装本轮 prompt：基础 prompt + 可选增量块 + 可选 hint + 重试反馈（仅重试时带）。"""
        if prev_snapshot_context is not None:
            msgs = build_prompt(current, prev_snapshot_context=prev_snapshot_context)
        else:
            msgs = build_prompt(current)      # 其它 Agent / 单参路径：不传新参数，签名不破
        if hint_entities:
            msgs["prompt"] += "\n已注册实体规范名（请对齐使用）：" + "、".join(hint_entities) + "\n"
        if feedback:
            msgs["prompt"] += "\n" + feedback
        return msgs

    current_text = scene_text
    level = shrink_level
    feedback = ""
    while True:
        msgs = _compose(current_text, feedback)
        try:
            raw = _get_llm().invoke(
                prompt=msgs["prompt"],
                system_prompt=msgs["system_prompt"],
                temperature=deconstruct_llm_temperature(),
            )
            content = raw.content if hasattr(raw, "content") else str(raw)
            data = _parse_json_strict(content, result_field)
            validated = schema.model_validate(data)
            return list(validated.model_dump()[result_field])
        except (json.JSONDecodeError, ValidationError) as e:
            if level < novel_agent_max_shrink():
                level += 1
                current_text = _shrink(scene_text, level)
                feedback = _retry_feedback(e)           # 只带最近一次错误，拼进下一次 prompt（P0-2）
                logger.warning("agent %s JSON/校验失败，缩窗重试 level=%d | %s", agent_name, level, str(e)[:80])
                continue
            logger.error("agent %s 缩窗耗尽仍失败 | shrink=%d err=%s", agent_name, level, str(e)[:120])
            raise LLMExtractError(f"agent {agent_name} 抽取失败（缩窗耗尽）：{str(e)[:120]}") from e
