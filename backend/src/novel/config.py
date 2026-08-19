# -*- coding: utf-8 -*-
"""
novel 模块配置收口 —— 所有可调参数从这里读环境变量（禁止在业务代码里硬编码）。

每个 getter 对应 `.env` 里的一个变量，都有默认值兜底：
改 `.env` 即可调优，不用动代码、不用改镜像。
"""
import os


def novel_chapter_max_chars() -> int:
    """超长章节场景切分阈值（字符数，默认 10000 ≈ 8k token）。

    一章超过该长度时，`pipeline/chapters._split_scenes` 会按段落切成多个场景，
    避免单次喂给 LLM 的文本过长。
    """
    return int(os.getenv("NOVEL_CHAPTER_MAX_CHARS", "10000"))


def novel_agent_max_concurrency() -> int:
    """LangGraph 图最大并发（LLM 限流）。

    运行时以 config 项 `"max_concurrency"` 注入 `graph.astream` ——
    同一 superstep 内最多并行执行的节点数，防止 8×N 个 Agent 的 LLM 请求同时爆发。
    """
    return int(os.getenv("NOVEL_AGENT_MAX_CONCURRENCY", "5"))


def novel_recursion_limit() -> int:
    """LangGraph recursion_limit（覆盖默认 25）。

    LangGraph 默认最多 25 个 superstep；大书多章 Send 扇出 + 未来重试循环需要更大余量，
    故放宽到 200（防止误触发 `GraphRecursionError`）。
    """
    return int(os.getenv("NOVEL_RECURSION_LIMIT", "200"))


def novel_deconstruct_on_upload() -> bool:
    """上传是否默认自动解构（默认关，子任务 09）。

    客服底座普通知识库默认不解构（省 LLM 成本、避免非小说内容产生垃圾解构数据）；
    小说上传流程在前端显式 `deconstruct=1`。
    """
    return os.getenv("NOVEL_DECONSTRUCT_ON_UPLOAD", "0") == "1"


def novel_validator_enabled() -> bool:
    """Layer 2 validator 开关（默认关，子任务 08）。

    默认关闭：确定性 Layer 0/1 + 人工复核已是主防线；LLM 批校验按需开启（控成本）。
    """
    return os.getenv("NOVEL_VALIDATOR_ENABLED", "0") == "1"


def novel_validator_batch() -> int:
    """validator 每批检查的待查记录数（控 token，默认 50）。"""
    return int(os.getenv("NOVEL_VALIDATOR_BATCH", "50"))


def novel_scene_max() -> int:
    """单章最大场景数上界（默认 100，S）。

    子任务 07 timeline 全局序号合成公式的 S：`chapter_index*K*S + scene_index*K + local`，
    与 `timeline_prompt._LOCAL_SORT_MAX`（K=1000）共同保证同书 global_sort 不撞号。
    """
    return int(os.getenv("NOVEL_SCENE_MAX", "100"))


def novel_agent_max_shrink() -> int:
    """单个 Agent 单场景最大缩窗次数。

    预留参数：子任务 03 的 llm_runner 在 LLM 输出 JSON 解析失败时，
    把 scene 文本缩到前一半重试，最多缩 `MAX_SHRINK` 次（完整→1/2→1/4），
    仍失败则该场景记入 errors（validate 判 failed）。
    """
    return int(os.getenv("NOVEL_AGENT_MAX_SHRINK", "2"))


def novel_chapter_lease_seconds() -> int:
    """僵死章节租约阈值（秒，默认 1800 = 30 分钟）。

    顶层计划外 P0-3：processing 状态超过该阈值未收尾（worker 崩溃）→
    `reap_stale_processing` 复位为 pending 重认领。阈值远大于单章最坏耗时（≤90s），
    避免误杀真在跑的章。
    """
    return int(os.getenv("NOVEL_CHAPTER_LEASE_SECONDS", "1800"))


# 解构分析型 LLM 配置

def deconstruct_llm_temperature() -> float:
    """解构分析型 LLM 温度（默认 0.0，对齐 005：0.1-0.3 低随机保证结构化抽取稳定）。

    与续写生成型（0.6-0.9）分开：解构要"可复现、少幻觉"，用低温。
    """
    return float(os.getenv("DECONSTRUCT_LLM_TEMPERATURE", "0.0"))


def deconstruct_llm_timeout() -> int:
    """解构分析型 LLM 超时（秒，默认 120）。"""
    return int(os.getenv("DECONSTRUCT_LLM_TIMEOUT", "120"))


def deconstruct_llm_max_tokens() -> int:
    """解构分析型 LLM 输出上限（默认 4096）。"""
    return int(os.getenv("DECONSTRUCT_LLM_MAX_TOKENS", "4096"))
