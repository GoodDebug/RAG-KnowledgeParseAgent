"""
FastAPI 应用入口 — Novel RAG Agent

复用现有 FASTAPI/ 模块：
  - MCP_SERVER/MCP_utils → get_mcp_client
  - LLM/llm_adapters → create_llm_adapter
  - RAG.RAG_Milvus_utils → create_Milvus_client (仅 list_books 用)

所有 RAG 操作通过 MCP 子进程执行，不走直接调用。

启动：uvicorn main_fastapi:app --host 0.0.0.0 --port 8000
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from LLM.llm_adapters import create_llm_adapter
from RAG.RAG_Milvus_utils import create_Milvus_client, ensure_collection_ready
from MCP_SERVER.MCP_utils import (
    PersistentMCPTool,
    create_persistent_mcp_session,
)

from config import COLLECTION_REGISTRY, EXPECTED_SCHEMA_CONTENT, INDEX_CONFIG_CONTENT

from core import logging_conf
from core.exceptions import register_exception_handlers
from db import Base, engine
import db.models  # noqa: F401  确保 4 表 ORM 注册到 Base.metadata

from routers import (
    chat_router,
    ingest_router,
    crawler_router,
    auth_router,
    documents_router,
    sessions_router,
    sessions_list_router,
    novel_router,
)
from app_state import state

load_dotenv(Path(__file__).resolve().parent / ".env")

logging_conf.setup()
logger = logging.getLogger(__name__)


# ========== 全局状态（定义见 app_state.py） ==========


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 正在初始化服务依赖...")

    # 1. 持久 MCP 会话（方案 A：一个子进程常驻，Milvus/embedding/reranker 只加载一次）
    _MCP_JSON_PATH = Path(__file__).parent / "MCP_SERVER/mcp.json"
    state.mcp_session = await create_persistent_mcp_session(_MCP_JSON_PATH)
    await state.mcp_session.open()
    raw_tools = await state.mcp_session.list_tools()
    state.tools = raw_tools
    state.tool_map = {t.name: PersistentMCPTool(t.name, state.mcp_session) for t in raw_tools}
    state.openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in raw_tools
    ]
    state.str_tools = f"已获取 {len(raw_tools)} 个 MCP 工具: {[t.name for t in raw_tools]}"
    logger.info(state.str_tools)

    # 2. Milvus 客户端 + 集合自愈
    state.milvus_client = create_Milvus_client()
    for key, reg in COLLECTION_REGISTRY.items():
        ensure_collection_ready(
            client=state.milvus_client,
            collection_name=reg["collection_name"],
            force_rebuild=False,
            coll_desc=reg["description"],
            expected_schema=EXPECTED_SCHEMA_CONTENT,
            index_config=INDEX_CONFIG_CONTENT,
        )
        logger.info("集合 %s 已就绪", reg["collection_name"])

    # 3. LLM 客户端
    state.llm_client = create_llm_adapter(
        interface_format="deepseek",
        model_provider="openai",
        base_url=os.getenv("DeepSeek_API_URL"),
        model_name="deepseek-v4-flash",
        api_key=os.getenv("DeepSeek_API_KEY"),
        temperature=0.7,
        max_tokens=4096,
        timeout=600,
    )

    # 4. 数据库：懒建表（幂等，MySQL 未就绪时仅告警不致命）
    try:
        Base.metadata.create_all(engine)
        logger.info("✅ 数据表已就绪（create_all）")
    except Exception:
        logger.warning("数据表初始化失败（MySQL 可能未就绪），运行时可重试", exc_info=True)

    # 5. 内置测试数据初始化（顶层计划外；SEED_DOCS_ENABLED 控制，默认开；异步不阻塞启动）
    if os.getenv("SEED_DOCS_ENABLED", "1") == "1":
        from routers.documents import seed_builtin_test_data
        asyncio.create_task(seed_builtin_test_data())
        logger.info("✅ 已挂起内置测试数据初始化任务（SEED_DOCS_ENABLED=1）")

    logger.info("✅ 服务依赖初始化完成")
    yield

    if state.mcp_session:
        await state.mcp_session.close()  # 关闭持久会话：子进程退出、释放 GPU 显存
    if state.milvus_client:
        state.milvus_client.close()
    try:
        engine.dispose()
    except Exception:
        pass
    logger.info("🛑 服务资源已释放")


app = FastAPI(title="Novel RAG Agent API", version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 全局异常 + request_id 日志关联（Spec-A）
register_exception_handlers(app)
logging_conf.install_request_id_middleware(app)

app.include_router(chat_router, prefix="/api/chat")
# app.include_router(ingest_router, prefix="/api/ingest")
app.include_router(crawler_router, prefix="/api/crawler")
app.include_router(auth_router, prefix="/api/auth")
app.include_router(documents_router, prefix="/api/documents")
app.include_router(sessions_router, prefix="/api/messages")
app.include_router(sessions_list_router, prefix="/api")  # Spec-E：/api/sessions、/api/sessions/{id}/messages
app.include_router(novel_router, prefix="/api/novel")    # 子任务 10：小说解构 API


@app.get("/api/health")
async def health():
    return {"status": "ok"}
