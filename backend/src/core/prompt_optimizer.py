# -*- coding: utf-8 -*-
"""
检索前用户输入优化（提示词工程优化·第一段）。

把「用户输入专用模板(规则：模板 A)」+ 用户原始输入组装进「检索前需 LLM 调用优化的
提示词工程模板(模板 B)」，调用 LLM 产出【正式用户提示词】；失败/返回空 → 回退原文。

另含 `detect_use_rag`：规则化检测用户显式"不调用知识库/用模型自身知识"意图（顶层计划外
《提示词工程优化-降级与意图》），用于禁用本轮 RAG 工具。
"""
import logging
import re

from langchain_core.messages import SystemMessage

from core import prompts

logger = logging.getLogger(__name__)


# 用户显式要求不调用知识库的保守触发词（只列明确意图，误命中由前端开关可覆盖）
_NO_RAG_PATTERN = re.compile(
    r"不调用知识库|不要调用知识库|不用调用知识库|不需要知识库|不要用知识库|不用知识库|无需知识库|"
    r"用模型知识|用模型自身知识|用模型自己的知识|用自身知识|用你自己的知识|用你的知识|用你的自身知识|"
    r"靠模型知识|靠自身知识|靠你自己的知识|靠你的知识|"
    r"凭模型知识|凭自身知识|凭你自己的知识|凭你的知识|"
    r"基于模型知识|基于自身知识|基于你自己的知识|基于你的知识|"
    r"自由回答|不查知识库|不查资料|不检索|不用检索|不要检索|"
    r"don'?t use rag|no rag|不用rag|不开知识库"
)


def detect_use_rag(message: str) -> bool:
    """用户显式要求不调用知识库时返回 False（该轮禁用 RAG）。

    保守正则匹配（§3.3 触发词表）；只能把 use_rag 关掉，不能打开。
    """
    return not bool(_NO_RAG_PATTERN.search(message or ""))


def optimize_user_prompt(llm, message: str) -> str:
    """用 LLM 把用户输入优化为正式用户提示词；失败/空回退原文。"""
    if not message:
        return message
    try:
        rules = prompts.get_user_input_rules()
        opt_prompt = prompts.render_prompt_optimizer_template(
            user_input_rules=rules, user_message=message
        )
        chunks: list[str] = []
        chunk_iter = llm.stream(messages=[SystemMessage(content=opt_prompt)], temperature=0.2)
        for chunk in chunk_iter:
            content = getattr(chunk, "content", None) or ""
            if content:
                chunks.append(content)
        optimized = "".join(chunks).strip()
        logger.info("用户输入优化完成 | 原长=%d 优化后=%d", len(message), len(optimized))
        return optimized or message
    except Exception as exc:
        logger.warning("用户输入优化失败，回退原文: %s", exc)
        return message
