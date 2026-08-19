# -*- coding: utf-8 -*-
"""
意图识别（加分项①，Spec-E）：规则词典优先 + LLM 兜底。

- `classify_intent(message, mode)`：入口。mode ∈ {"rule","llm","hybrid"}。
  - `rule`（零 LLM 成本）：规则词典命中即返回，确定性；
  - `hybrid`（默认）：规则优先，未命中才走 LLM 兜底（小 prompt 输出 JSON）；
  - `llm`：总是走 LLM。
- 结果写入 `messages.intent`（router 层按 messages.id UPDATE，见 05 spec §4.4）。
- LLM 兜底不走 `response_format`（llm_adapters 不透传），用 prompt 引导 JSON + 健壮解析 +
  非法/异常回退 `DEFAULT_INTENT`，严格满足"在调用 LLM 前"且不阻塞主链路。
"""
import json
import logging

from langchain_core.messages import SystemMessage

from core import prompts

logger = logging.getLogger(__name__)

INTENT_CATEGORIES: list[str] = ["产品咨询", "售后问题", "闲聊", "投诉", "其他"]
DEFAULT_INTENT: str = "其他"

# 优先级顺序即判定优先级：投诉 > 售后问题 > 产品咨询 > 闲聊（首命中即返回）
_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "投诉",
        (
            "投诉", "差评", "太差", "垃圾", "气死", "欺诈", "虚假宣传", "被骗",
            "诈骗", "坑人", "骗子", "态度差", "客服不回复", "举报", "投诉电话", "黑心",
            "赔偿", "要求道歉",
        ),
    ),
    (
        "售后问题",
        (
            "退货", "退款", "换货", "维修", "保修", "发票", "物流", "快递",
            "发货", "补发", "破损", "少件", "安装", "售后", "售后电话",
            "联系客服", "客服热线", "售后政策",
        ),
    ),
    (
        "产品咨询",
        (
            "价格", "多少钱", "购买", "怎么买", "套餐", "功能", "产品", "优惠",
            "活动", "折扣", "介绍", "区别", "版本", "试用", "订阅", "收费",
            "支持哪些", "有什么", "政策", "怎么样",
        ),
    ),
    (
        "闲聊",
        (
            "你好", "在吗", "谢谢", "再见", "你是谁", "你叫什么", "吃了吗", "哈哈",
            "天气", "无聊", "辛苦了", "早安", "晚安", "hello", "hi", "在干嘛",
        ),
    ),
]


def rule_classify(message: str) -> str | None:
    """规则优先：按优先级顺序首命中返回类别；全部未命中返回 None（不调 LLM 信号）。"""
    text = message or ""
    for intent, kws in _RULES:
        for kw in kws:
            if kw in text:
                return intent
    return None


def _extract_intent(raw: str, categories: list[str]) -> str | None:
    """从 LLM 文本中提取首个 JSON 对象，校验 intent 属于 categories；否则 None。"""
    s = raw.strip()
    if s.startswith("```"):  # 去掉 ```json``` 围栏
        s = s.strip("`").removeprefix("json").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    intent = obj.get("intent")
    return intent if isinstance(intent, str) and intent in categories else None


def llm_classify(llm, message: str, categories: list[str] | None = None) -> str:
    """LLM 兜底：小 prompt 输出 {"intent": 类别}；解析失败/异常/非法值 → DEFAULT_INTENT。"""
    if llm is None:
        return DEFAULT_INTENT
    cats = categories or INTENT_CATEGORIES
    try:
        prompt = prompts.render_intent_classifier_prompt(message, cats)
        resp = llm.invoke(messages=[SystemMessage(content=prompt)], temperature=0.1)
        raw = getattr(resp, "content", "") or ""
        intent = _extract_intent(raw, cats)
        if intent is not None:
            return intent
        logger.warning("意图 LLM 输出非法，回退默认 | %.80s", raw[:80])
    except Exception as exc:
        logger.warning("意图 LLM 兜底失败，回退默认: %s", exc)
    return DEFAULT_INTENT


def classify_intent(llm, message: str, mode: str = "hybrid") -> str:
    """入口。mode ∈ {"rule","llm","hybrid"}；非法 mode 归一为 hybrid。

    规则命中时 llm 不会被调用（可传 None）；LLM 兜底仅在规则未命中且 mode∈{llm,hybrid} 时执行。
    """
    if mode not in ("rule", "llm", "hybrid"):
        mode = "hybrid"
    if mode == "llm":
        return llm_classify(llm, message)
    hit = rule_classify(message)
    if hit is not None:
        return hit
    if mode == "hybrid":
        return llm_classify(llm, message)
    return DEFAULT_INTENT
