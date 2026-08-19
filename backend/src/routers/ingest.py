"""
文件入库路由

POST /api/ingest/files   → 文件入库（通过 MCP 子进程执行 RAG_init_collection）
POST /api/ingest/upload  → 前端上传 TXT 文件 → 暂存 → 入库
GET  /api/ingest/books   → 已入库书籍列表（直连 Milvus 元数据查询）
"""

import logging
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, Form, UploadFile, File as FastAPIFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymilvus import MilvusClient, MilvusException

from config import COLLECTION_REGISTRY
from app_state import state
from core.deps import get_current_user
from db import SessionLocal
from db.models import Document, User

TEMP_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "temp" / "uploads"

router = APIRouter(tags=["ingest"])
logger = logging.getLogger(__name__)


class IngestRequest(BaseModel):
    book_name: str
    file_paths: List[str]
    collection_name: str = "content"


class BookItem(BaseModel):
    id: str
    name: str
    chunks: int = 0


@router.post("/files")
async def ingest_files(req: IngestRequest):
    """
    （deprecated：前端已迁移至 `POST /api/documents/upload`，见 顶层计划外《知识库管理前端迁移》）
    文件入库：通过 MCP 子进程调用 RAG_init_collection 工具。
    MCP 子进程内部加载 Embedding 模型并执行 init_Milvus_Collection。
    """
    tool = state.tool_map.get("RAG_init_collection")
    if not tool:
        return JSONResponse({"error": "MCP 工具未就绪"}, status_code=503)

    try:
        result = await tool.ainvoke({
            "collection_name": req.collection_name,
            "file_paths": req.file_paths,
            "book_name": req.book_name,
        })
        return {"success": True, "result": str(result)}
    except Exception as e:
        logger.error("入库失败: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/upload")
async def upload_files(book_name: str = Form(...), files: list[UploadFile] = FastAPIFile(...)):
    """
    （deprecated：前端已迁移至 `POST /api/documents/upload`，见 顶层计划外《知识库管理前端迁移》）
    前端上传 TXT 文件 → 暂存到 temp/uploads/ → 调用 MCP 工具入库 → 清理临时文件
    """
    file_count = len(files) if files else 0
    logger.info("📥 入库请求: book_name=%s, file_count=%d", book_name, file_count)

    session_id = uuid.uuid4().hex[:8]
    work_dir = TEMP_UPLOAD_DIR / session_id
    work_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    try:
        for i, f in enumerate(files):
            path = work_dir / f.filename
            content = await f.read()
            path.write_bytes(content)
            saved_paths.append(str(path))
            if (i + 1) % 100 == 0:
                logger.info("  保存进度: %d/%d", i + 1, file_count)

        logger.info("📁 文件暂存完成: %d 个文件 → %s", len(saved_paths), work_dir)

        tool = state.tool_map.get("RAG_init_collection")
        if not tool:
            logger.warning("MCP 工具未就绪")
            return JSONResponse({"error": "MCP 工具未就绪"}, status_code=503)

        logger.info("🔧 调用 MCP 工具 RAG_init_collection, book_name=%s", book_name)
        result = await tool.ainvoke({
            "collection_name": "content",
            "file_paths": saved_paths,
            "book_name": book_name,
        })
        logger.info("✅ MCP 入库完成: %s", str(result)[:200])
        return {"success": True, "result": str(result)}

    except Exception as e:
        logger.error("❌ 上传入库失败: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info("🧹 临时目录已清理: %s", work_dir)


@router.get("/books", response_model=List[BookItem])
async def list_books(user: User = Depends(get_current_user)):
    """查询当前用户已入库文档（改查 MySQL documents，Spec-C；形状 {id,name,chunks} 兼容前端）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(Document)
            .filter(Document.user_id == user.id)
            .order_by(Document.uploaded_at.desc(), Document.id.desc())
            .all()
        )
        return [BookItem(id=str(r.id), name=r.file_name, chunks=r.chunk_count or 0) for r in rows]
    finally:
        db.close()
