"""
SSE 流式对话路由（Spec-B：核心问答链路）

GET /api/chat/stream?message=用户问题&session_id=xxx   （Bearer 认证）
→ text/event-stream
   data: {"type": "thinking", "content": "..."}
   data: {"type": "tool", "content": "🔧 正在检索..."}
   data: {"type": "separator"}
   data: {"type": "answer", "content": "...", "source_refs": [...]}
   data: {"type": "done"}
   空检索：仅 {"type":"answer","content":兜底话术,"source_refs":[]} + done（无 thinking/tool，不二轮 LLM）
   异常：{"type":"error","content":...,"error_code":...} + done

契约：docs/spec/02-子任务-B-核心问答链路.md §4
- 会话隔离：内部 Key = user_{uid}_{session}，经 MysqlMemoryAdapter 写入 messages 表
- 校验顺序：401（鉴权）→ 400（>500 字 / 空 session_id）→ 429（每日限流）
- history 只查不建；DB 调用在 async 生成器内一律 asyncio.to_thread（禁止阻塞事件循环）
"""
import asyncio
import json
import logging
import os

from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import func

from core import prompts
from core.deps import get_current_user
from core.intent_classifier import classify_intent, DEFAULT_INTENT
from core.prompt_optimizer import detect_use_rag, optimize_user_prompt
from db import SessionLocal
from db.models import Message, Session as DBSession, User
from LLM.memory_adapters import MysqlMemoryAdapter, create_memory_adapter

from app_state import state

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

# ========== 配置（Spec-B §3.2：一律环境变量，禁止硬编码） ==========
MAX_QUESTION_LEN: int = int(os.getenv("MAX_QUESTION_LEN", "500"))
DAILY_QUOTA: int = int(os.getenv("DAILY_QUOTA", "100"))
CONTEXT_MAX_CHARS: int = int(os.getenv("CONTEXT_MAX_CHARS", "3000"))
HISTORY_RECENT_TURNS: int = int(os.getenv("HISTORY_RECENT_TURNS", "10"))
FALLBACK_COPY: str = os.getenv(
    "FALLBACK_COPY",
    "抱歉，知识库中暂无相关信息。请尝试换个问法，或联系人工客服获取帮助。",
)
SNIPPET_MAX_CHARS: int = 200  # source_refs.snippet 截断长度（常量）
PROMPT_OPTIMIZE_ENABLED: str = os.getenv("PROMPT_OPTIMIZE_ENABLED", "1")  # 检索前用户输入优化开关（顶层计划外）
# 加分项（Spec-E）：意图识别 + 追问引导 + 会话自动命名
INTENT_ENABLED: str = os.getenv("INTENT_ENABLED", "1")
INTENT_MODE: str = os.getenv("INTENT_MODE", "hybrid")  # rule | llm | hybrid
INTENT_TIMEOUT: float = float(os.getenv("INTENT_TIMEOUT", "8"))  # 意图 LLM 兜底超时秒
FOLLOWUP_ENABLED: str = os.getenv("FOLLOWUP_ENABLED", "1")
FOLLOWUP_SUGGESTION_COUNT: int = int(os.getenv("FOLLOWUP_SUGGESTION_COUNT", "3"))
FOLLOWUP_TIMEOUT: float = float(os.getenv("FOLLOWUP_TIMEOUT", "15"))  # 追问生成超时秒
FOLLOWUP_ANSWER_MAX_CHARS: int = 2000  # 常量：追问生成时回答截断长度，控 token
SESSION_AUTO_TITLE: str = os.getenv("SESSION_AUTO_TITLE", "1")
SESSION_TITLE_MAX_LEN: int = 20  # 常量：会话自动命名截断长度


# ======================= SSE 底层 =======================

def _event(data: dict) -> str:
    """格式化 SSE 事件"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_stream_and_collect(
    llm, msgs, kwargs, answer_extra: Optional[dict] = None,
) -> AsyncGenerator[str, str]:
    """
    SSE 版流式输出 + 累计 chunk 检测 tool_calls。
    最后 yield 一个 __ACC__ 哨兵携带累计结果给调用方。
    answer_extra：合并进每个 answer 事件（如 source_refs）。
    """
    chunk_iter = await asyncio.to_thread(llm.stream, messages=msgs, **kwargs)
    acc = None
    phase = "thinking"

    for chunk in chunk_iter:
        acc = chunk if acc is None else acc + chunk

        reasoning = chunk.additional_kwargs.get("reasoning_content", "")
        if reasoning:
            if phase == "thinking":
                yield _event({"type": "thinking", "content": reasoning})
        elif chunk.content:
            if phase == "thinking":
                phase = "answer"
                yield _event({"type": "separator"})
            data = {"type": "answer", "content": chunk.content}
            if answer_extra:
                data.update(answer_extra)
            yield _event(data)

    yield "__ACC__" + json.dumps({"acc": None if acc is None else {
        "content": acc.content if hasattr(acc, 'content') else "",
        "tool_calls": [
            {"name": t["name"], "args": t["args"], "id": t["id"]}
            for t in (acc.tool_calls if acc and acc.tool_calls else [])
        ],
    }})

# ======================= 工具函数区 =======================

def _is_empty_result(result) -> bool:
    """RAG 工具结果判空：list/tuple 空、或字符串形式为 []/空/None。"""
    if result is None:
        return True
    if isinstance(result, (list, tuple)):
        return len(result) == 0
    return str(result).strip() in ("", "[]")


def _extract_source_refs(results: List) -> List[dict]:
    """从 RAG 原始结果提取引用（容错 dict 与 object），按 chunk_id 去重、不含 snippet 原文。

    顶层计划外《引用存储精简与去重修复》：只存 {book_name, file_name, chunk_id}——去 snippet
    大幅减小 source_refs 体积（避免 TEXT 溢出）；用 chunk_id 精确去重（Milvus 重复向量多份只留一条）。
    """
    refs: List[dict] = []
    seen: set = set()
    for r in results or []:
        if isinstance(r, dict):
            md = r.get("metadata", {}) or {}
            chunk_id = r.get("chunk_id", "") or md.get("chunk_id", "")
        else:
            md = getattr(r, "metadata", {}) or {}
            chunk_id = getattr(r, "chunk_id", "") or md.get("chunk_id", "")
        if chunk_id and chunk_id in seen:
            continue  # 相同 chunk（重复向量）只保留一条
        if chunk_id:
            seen.add(chunk_id)
        refs.append({
            "file_name": md.get("file_name", ""),
            "book_name": md.get("book_name", ""),
            "chunk_id": chunk_id,
        })
    return refs


def _check_quota(user_id: int) -> None:
    """每日限流：当天该用户 role='user' 消息数 ≥ DAILY_QUOTA → 429。

    用独立短生命周期 SessionLocal 计数（不依赖请求级 get_db 会话，避免事务快照陈旧）。
    """
    db = SessionLocal()
    try:
        count = (
            db.query(func.count(Message.id))
            .join(DBSession, Message.session_id == DBSession.id)
            .filter(
                DBSession.user_id == user_id,
                Message.role == "user",
                Message.created_at >= func.curdate(),
            )
            .scalar()
            or 0
        )
    finally:
        db.close()
    if count >= DAILY_QUOTA:
        logger.info("限流命中 | user_id=%s count=%s", user_id, count)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="今日提问次数已达上限",
        )


def _build_message_stack(history_messages, message: str):
    """
    上下文 trim（Spec-B §4.6）：
    - 只保留最近 HISTORY_RECENT_TURNS 条历史；
    - 全局字符预算 CONTEXT_MAX_CHARS，超限丢最旧完整消息；当前提问始终保留。
    """
    kept = list(history_messages)[-HISTORY_RECENT_TURNS:]
    user_msg = HumanMessage(content=message)
    stack = kept + [user_msg]
    total = sum(len(getattr(m, "content", "") or "") for m in stack)
    while total > CONTEXT_MAX_CHARS and kept:
        kept.pop(0)
        stack = kept + [user_msg]
        total = sum(len(getattr(m, "content", "") or "") for m in stack)
    return stack


def _persist_and_set_refs(history, message, refs) -> Optional[int]:
    """持久化 assistant 消息并按其 messages.id 补写 source_refs（Spec-B §4.8）。

    :return: 落库的 messages.id（供 Spec-D done.message_id 使用）；add_message 失败返回 None。
    """
    mid = history.add_message(message)
    if mid is None:
        return None
    if refs is None:
        return mid
    db = SessionLocal()
    try:
        db.query(Message).filter(Message.id == mid).update(
            {"source_refs": json.dumps(refs, ensure_ascii=False)}
        )
        db.commit()
    finally:
        db.close()
    return mid


# ======================= 加分项（Spec-E）：意图 + 追问 + 会话命名 =======================

def _update_message_intent(message_id: int, intent: str) -> None:
    """按 messages.id UPDATE 补写 intent（顶层计划 §4.1：router 层补写，不由适配器写）。"""
    db = SessionLocal()
    try:
        db.query(Message).filter(Message.id == message_id).update({"intent": intent})
        db.commit()
    finally:
        db.close()


def _maybe_name_session(session_id: int, message: str) -> None:
    """会话自动命名（可选，Spec-E §2.1-9）：仅当 title=='新会话' 时置为消息前 20 字。"""
    db = SessionLocal()
    try:
        row = db.query(DBSession).filter(DBSession.id == session_id).first()
        if row is not None and row.title == "新会话":
            title = (message or "").strip()[:SESSION_TITLE_MAX_LEN].strip()
            if title:
                row.title = title
                db.commit()
    finally:
        db.close()


def _parse_suggestions(raw: str, count: int) -> list:
    """解析追问建议 JSON 数组；去 ```json``` 围栏；过滤非 str/空；截断至 count。"""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").removeprefix("json").strip()
    try:
        arr = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        arr = None
    if not isinstance(arr, list):
        return []
    out: list = []
    for item in arr:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) >= count:
            break
    return out


async def _followup_event(user_message: str, answer: str, intent: str) -> Optional[dict]:
    """加分项②：回答结束后生成追问建议；失败/超时/关闭 → None（优雅跳过，不断流）。"""
    if FOLLOWUP_ENABLED != "1" or not answer:
        return None
    try:
        prompt = prompts.render_followup_prompt(
            user_message=(user_message or "")[:MAX_QUESTION_LEN],
            answer=answer[:FOLLOWUP_ANSWER_MAX_CHARS],
            intent=intent,
            count=FOLLOWUP_SUGGESTION_COUNT,
        )
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                state.llm_client.invoke,
                messages=[SystemMessage(content=prompt)],
                temperature=0.5,
            ),
            timeout=FOLLOWUP_TIMEOUT,
        )
        raw = getattr(resp, "content", "") or ""
        suggestions = _parse_suggestions(raw, FOLLOWUP_SUGGESTION_COUNT)
        if suggestions:
            return {"type": "followup", "suggestions": suggestions}
    except Exception as exc:
        logger.warning("追问建议生成失败，跳过 | %s", exc)
    return None


# ======================= 问答链路主流程 =======================

async def _stream_chat(message: str, internal_key: str, use_rag: bool = True) -> AsyncGenerator[str, None]:
    """SSE 事件生成器（Spec-B + 顶层计划外降级/意图）。通过 MysqlMemoryAdapter 持久化到 messages 表。"""
    chat_log = logger.info
    chat_log("💬 对话开始 | session=%s | message=%.80s", internal_key, message)

    # 顶层计划外《提示词工程优化-降级与意图》：use_rag = 前端参数 && 文本规则检测（只能关不能开）
    use_rag = use_rag and detect_use_rag(message)
    # 首个进度事件：覆盖优化器 + 首轮静默期
    yield _event({"type": "status", "content": "正在理解您的问题..."})

    try:
        adapter = create_memory_adapter("mysql")
        history = await asyncio.to_thread(adapter.get_session_history, internal_key)
        history_messages = await asyncio.to_thread(lambda: list(history.messages))
        chat_log("📚 加载历史 %d 条 | session=%s", len(history_messages), internal_key)

        # 两段式·第一段：检索前用 LLM 把用户输入优化为【正式用户提示词】（失败回退原文）
        optimized = message
        if PROMPT_OPTIMIZE_ENABLED == "1":
            optimized = await asyncio.to_thread(
                optimize_user_prompt, state.llm_client, message
            )
            chat_log("🔀 用户输入优化 | 原文=%.40s 优化后=%.40s", message, optimized)

        stack = _build_message_stack(history_messages, optimized)

        # 写路径：持久化用户问题（用原文；add_message 返回 messages.id 供 intent 补写，Spec-E）
        user_msg_id: Optional[int] = await asyncio.to_thread(
            history.add_message, HumanMessage(content=message)
        )
        chat_log("➕ 用户消息已持久化 | session=%s", internal_key)

        # 加分项①（Spec-E）：意图识别（规则优先+LLM 兜底）→ UPDATE messages.intent → SSE intent 事件。
        # 发生在第一轮 LLM 之前（满足"在调用 LLM 前"）；超时/异常回退默认类，不断流。
        intent: str = DEFAULT_INTENT
        if INTENT_ENABLED == "1":
            try:
                intent = await asyncio.wait_for(
                    asyncio.to_thread(classify_intent, state.llm_client, message, INTENT_MODE),
                    timeout=INTENT_TIMEOUT,
                )
            except Exception:
                intent = DEFAULT_INTENT
            if user_msg_id is not None:
                await asyncio.to_thread(_update_message_intent, user_msg_id, intent)
            yield _event({"type": "intent", "intent": intent, "message_id": user_msg_id})

        # 会话自动命名（可选，Spec-E §2.1-9）：首条消息把 title="新会话" 置为消息前 20 字
        if SESSION_AUTO_TITLE == "1" and user_msg_id is not None:
            await asyncio.to_thread(_maybe_name_session, history.session_id, message)

        # 顶层计划外降级/意图：按 use_rag 选 prompt 与工具（自由问答模式禁用 RAG）
        if use_rag:
            system_prompt = prompts.render_system_prompt(
                tools=state.str_tools, history_summary=""
            )
        else:
            yield _event({"type": "status", "content": "已关闭知识库，正在基于模型知识回答..."})
            system_prompt = prompts.render_free_system_prompt(history_summary="")
        msgs: List = [SystemMessage(content=system_prompt)] + stack

        round_tools = state.openai_tools if use_rag else []
        round_extra = None if use_rag else {"source_refs": [], "knowledge_mode": "model"}

        # 首轮推理：产物缓存不转发（空检索时不发 thinking/tool）
        buf: List[str] = []
        acc_data = None
        async for ev in _sse_stream_and_collect(
            state.llm_client, msgs,
            {"tools": round_tools, "thinking_enabled": True,
             "reasoning_effort": "high", "temperature": 0.7},
            answer_extra=round_extra,
        ):
            buf.append(ev)
        for ev in buf:
            if ev.startswith("__ACC__"):
                acc_data = json.loads(ev[7:])["acc"]

        has_tools = bool(acc_data and acc_data.get("tool_calls"))
        source_refs: List[dict] = []
        mid = None  # 落库的 assistant 消息 id（Spec-D done.message_id）；未持久化时为 None

        if has_tools and acc_data:
            ai_msg = AIMessage(content=acc_data["content"], tool_calls=acc_data["tool_calls"])
            tool_msgs: List = [ai_msg]
            rag_results: List = []
            try:
                for tc in acc_data["tool_calls"]:
                    target_tool = state.tool_map.get(tc["name"])
                    chat_log("🔧 执行工具: %s | args=%.120s", tc["name"], str(tc["args"])[:120])
                    if target_tool and tc["name"] == "RAG_search_by_query":
                        # 进度可视化：检索冷启动可能数秒，先给用户反馈
                        yield _event({"type": "status", "content": "正在检索知识库（可能需要几秒）..."})
                    if target_tool:
                        result = await target_tool.ainvoke(tc["args"])
                        chat_log("✅ 工具执行完成: %s | result_len=%d", tc["name"], len(str(result)))
                    else:
                        result = f"错误：不存在工具 {tc['name']}"
                        logger.warning("⚠️ 工具未注册: %s", tc["name"])
                    tool_msgs.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

                    if tc["name"] == "RAG_search_by_query" and _is_empty_result(result):
                        # 空检索降级（顶层计划外）：自由 prompt 二轮 LLM 用模型自身知识回答，
                        # 答不出才兜底话术——覆盖 00 §2.1 row9「空检索不调 LLM」决策
                        yield _event({"type": "status", "content": "知识库未命中，正在基于模型知识回答..."})
                        fallback_msgs = [
                            SystemMessage(content=prompts.render_free_system_prompt(history_summary="")),
                        ] + stack
                        fallback_acc = None
                        saw_answer = False
                        async for ev in _sse_stream_and_collect(
                            state.llm_client, fallback_msgs,
                            {"thinking_enabled": True, "reasoning_effort": "high"},
                            answer_extra={"source_refs": [], "knowledge_mode": "model"},
                        ):
                            if ev.startswith("__ACC__"):
                                fallback_acc = json.loads(ev[7:])["acc"]
                            else:
                                if json.loads(ev[6:]).get("type") == "answer":
                                    saw_answer = True
                                yield ev
                        fallback_content = (fallback_acc or {}).get("content", "") if fallback_acc else ""
                        if not fallback_content:
                            # 模型知识也没答出 → 兜底话术
                            fallback_content = FALLBACK_COPY
                            if not saw_answer:
                                yield _event({"type": "answer", "content": fallback_content, "source_refs": []})
                        mid = await asyncio.to_thread(
                            _persist_and_set_refs, history, AIMessage(content=fallback_content), []
                        )
                        chat_log("💾 空检索降级持久化 | session=%s", internal_key)
                        # 加分项②（Spec-E）：空检索降级路径也发 followup（assistant 落库后、done 前）
                        ev = await _followup_event(message, fallback_content, intent)
                        if ev:
                            yield _event(ev)
                        yield _event({"type": "done", "message_id": mid})
                        return
                    if tc["name"] == "RAG_search_by_query":
                        rag_results.extend(result if isinstance(result, list) else [result])

                # 非空 → flush 首轮缓存（thinking），再补 tool 事件
                for ev in buf:
                    if not ev.startswith("__ACC__"):
                        yield ev
                yield _event({"type": "tool", "content": "\U0001f527 正在检索知识库..."})
                source_refs = _extract_source_refs(rag_results)

                yield _event({"type": "status", "content": "正在基于检索结果生成回答..."})
                # 二轮推理（携带工具结果 + source_refs）
                chat_log("🤖 LLM 第二轮推理开始 | refs=%d", len(source_refs))
                async for ev in _sse_stream_and_collect(
                    state.llm_client, msgs + tool_msgs,
                    {"thinking_enabled": True, "reasoning_effort": "high"},
                    answer_extra={"source_refs": source_refs},
                ):
                    if ev.startswith("__ACC__"):
                        acc_data = json.loads(ev[7:])["acc"]
                    else:
                        yield ev
            except Exception as exc:
                logger.exception("工具执行异常 | session=%s", internal_key)
                yield _event({"type": "error", "content": "检索或回答过程出现异常，请稍后重试",
                              "error_code": "TOOL_EXEC_ERROR"})
                yield _event({"type": "done", "message_id": None})
                return
        else:
            # 无工具，LLM 直接作答 → flush 首轮缓存
            for ev in buf:
                if not ev.startswith("__ACC__"):
                    yield ev

        if acc_data and acc_data.get("content"):
            mid = await asyncio.to_thread(
                _persist_and_set_refs, history, AIMessage(content=acc_data["content"]), source_refs
            )
            chat_log("💾 AI 回复已持久化 | content_len=%d refs=%d",
                     len(acc_data["content"]), len(source_refs))

        # 加分项②（Spec-E）：追问引导（assistant 落库后、done 前）；失败/超时静默跳过
        if acc_data and acc_data.get("content"):
            ev = await _followup_event(message, acc_data["content"], intent)
            if ev:
                yield _event(ev)

        chat_log("🏁 对话流结束 | session=%s", internal_key)
        yield _event({"type": "done", "message_id": mid})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("问答链路异常 | session=%s", internal_key)
        yield _event({"type": "error", "content": "服务暂时不可用，请稍后重试",
                      "error_code": "LLM_ERROR"})
        yield _event({"type": "done", "message_id": None})


def _parse_source_refs(raw: Optional[str]) -> List:
    """解析 messages.source_refs 的 JSON 字符串；空/损坏 → []（Spec-D history 扩展）。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.warning("source_refs 解析失败，按空处理: %.80s", raw)
        return []


def read_messages_for_session(session_id: int) -> list:
    """按 sessions.id 读完整消息历史（Spec-E 单源）。

    `/api/chat/history`（按 key）与 `/api/sessions/{id}/messages`（按 id）共用本函数，避免重复实现。
    Spec-D 字段：id / source_refs(解析数组) / feedback / feedback_text；Spec-E 追加 intent。
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.id.asc())
            .all()
        )
        msgs = []
        for r in rows:
            if r.role == "system":
                continue
            msgs.append({
                "role": "user" if r.role == "user" else "ai",
                "content": r.content,
                "id": r.id,
                "source_refs": _parse_source_refs(r.source_refs),
                "feedback": r.feedback,
                "feedback_text": r.feedback_text,
                "intent": r.intent,  # Spec-E：意图标注（VARCHAR50，无则 None）
            })
        return msgs
    finally:
        db.close()


def _read_history(internal_key: str) -> list:
    """只查不建：按 (user_id,key) 读 messages，无会话返回 []，不插 sessions 行。

    Spec-D 扩展：每条返回 id / source_refs(解析数组) / feedback / feedback_text；
    Spec-E 扩展：每条返回 intent。
    """
    try:
        user_id, key = MysqlMemoryAdapter.parse_key(internal_key)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id 非法")
    db = SessionLocal()
    try:
        srow = db.query(DBSession).filter_by(user_id=user_id, key=key).first()
        if srow is None:
            return []
        return read_messages_for_session(srow.id)
    finally:
        db.close()

# ======================= 路由区 =======================

@router.get("/stream")
def chat_stream(
    message: str,
    session_id: str = "default",
    use_rag: bool = True,
    user: User = Depends(get_current_user),
):
    """SSE 流式对话（Spec-B：鉴权 → 长度 → 限流）。"""
    logger.info("🌐 SSE 请求 | user_id=%s | session=%s | message=%.80s", user.id, session_id, message)
    if len(message) > MAX_QUESTION_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="提问内容过长，单次最多 500 字",
        )
    if not session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id 不能为空")
    _check_quota(user.id)
    internal_key = f"user_{user.id}_{session_id[:64]}"
    return StreamingResponse(
        _stream_chat(message, internal_key, use_rag=use_rag),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history")
def chat_history(
    session_id: str = "default",
    user: User = Depends(get_current_user),
):
    """返回指定 session 的对话历史（只查不建，读 MySQL）。"""
    logger.info("📋 获取历史 | user_id=%s | session=%s", user.id, session_id)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id 不能为空")
    internal_key = f"user_{user.id}_{session_id[:64]}"
    return _read_history(internal_key)
