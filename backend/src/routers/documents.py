# -*- coding: utf-8 -*-
"""
知识库文档路由（Spec-C：知识库管理）。

- POST /api/documents/upload（Bearer）：multipart book_name + files[]，异步入库（BackgroundTask 状态机）
- GET  /api/documents（Bearer）：当前用户文档列表（文件级，含 book_name 分组）
- DELETE /api/documents/{id}（Bearer）：按书删（Milvus 删 book_id + 删 (user, book_name) 组内全部行），幂等 204

契约：docs/spec/03-子任务-C-知识库管理.md §4.1/§4.3/§4.7/§4.8
- book_id = doc_{user_id}_{doc_id}，按 (user_id, book_name) 分组复用（多文件同书名共享一个 book_id）
- 一致性：先写 MySQL（processing）→ 异步写 Milvus → 回填 ready/failed；删除先删 Milvus 后删 MySQL 行
"""
import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from starlette.background import BackgroundTasks
from sqlalchemy import func
from sqlalchemy.orm import Session

from app_state import state
from config import COLLECTION_REGISTRY
from core.deps import get_current_user
from core.security import hash_password
from db import SessionLocal
from db.models import Document, User
from novel.config import novel_deconstruct_on_upload
from novel.orchestrator import run_job
from novel.pipeline.upload import prepare_deconstruct_job

router = APIRouter(tags=["documents"])
logger = logging.getLogger(__name__)

TEMP_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "temp" / "uploads"
ALLOWED_EXTS = {".txt", ".md", ".pdf"}


def _resolve_deconstruct(form_value: str | None) -> bool:
    """解析上传的 deconstruct 表单值：None → 取 env 默认；否则显式 "1" 为真。"""
    if form_value is None:
        return novel_deconstruct_on_upload()
    return str(form_value).strip() == "1"


def _ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _real_collection(key: str = "content") -> str:
    """registry key → 真实集合名（documents.milvus_collection 存 key，见优化 Spec）。"""
    return COLLECTION_REGISTRY.get(key, COLLECTION_REGISTRY["content"])["collection_name"]


def _normalize_collection_key(key: str) -> str:
    """归一化集合 key：在 registry 则返回，否则返回默认 key（"content"）。"""
    return key if key in COLLECTION_REGISTRY else next(iter(COLLECTION_REGISTRY))


def _resolve_group_book_id(db: Session, user_id: int, book_name: str) -> str | None:
    """返回该 (user, book_name) 组的稳定 book_id（存于行 book_id 列，顶层计划外）。

    读组内最早行的存储 book_id；缺（存量数据未回填）→ 派生 doc_{uid}_{首行id} 并回填
    （回填 update 交由调用方 commit/flush）。无组 → None。
    """
    first = (
        db.query(Document)
        .filter_by(user_id=user_id, book_name=book_name)
        .order_by(Document.id.asc())
        .first()
    )
    if first is None:
        return None
    if first.book_id:
        return first.book_id
    derived = f"doc_{user_id}_{first.id}"
    # 存量回填：bulk update 关闭 session 同步（避免 expiring 内存对象引发 detached 访问）
    db.query(Document).filter_by(user_id=user_id, book_name=book_name).update(
        {"book_id": derived}, synchronize_session=False
    )
    return derived


def _update_group_status(db: Session, user_id: int, book_name: str, file_names, status_val: str):
    """把 (user, book_name) 组中与给定文件同名的 processing 行更新为指定状态。"""
    rows = (
        db.query(Document)
        .filter(
            Document.user_id == user_id,
            Document.book_name == book_name,
            Document.status == "processing",
        )
        .all()
    )
    for r in rows:
        if r.file_name in file_names:
            r.status = status_val
    db.commit()


def _backfill_chunk_count_fallback(
    db: Session,
    real_collection: str,
    book_id: str,
    book_name: str,
    user_id: int,
    file_names: List[str],
) -> None:
    """批量回退 chunk_count：per_file 未上报（旧工具/兼容）时，一次 flush + 一次 file_name in [...] 查询。

    顶层计划外《入库去重按书隔离与重复上传治理》：相对旧的逐文件 flush+query（约 10s/文件），
    只做一次 flush 与一次批量查询，消除串行阻塞。命中→ready+旧计数；未命中→ready（无计数）。
    """
    if not file_names:
        return
    if state.milvus_client is None:
        for fn in file_names:
            logger.warning("chunk_count 未回填（不可查）| book=%s file=%s", book_name, fn)
            db.query(Document).filter(
                Document.user_id == user_id,
                Document.book_name == book_name,
                Document.file_name == fn,
            ).update({"status": "ready"})
        return
    rows = []
    try:
        state.milvus_client.flush(real_collection)
        # f-string 表达式内不能含反斜杠（Python <3.12），先在外层转义文件名
        escaped = [fn.replace("\\", "\\\\").replace('"', '\\"') for fn in file_names]
        quoted = ", ".join(f'"{e}"' for e in escaped)
        rows = state.milvus_client.query(
            collection_name=real_collection,
            filter=f'book_id == "{book_id}" and file_name in [{quoted}]',
            output_fields=["chunk_id", "file_name"],
        )
    except Exception as qe:
        logger.warning("chunk_count 回退查询失败 | book=%s err=%s", book_name, qe, exc_info=True)
    counts: Dict[str, int] = {}
    for r in rows:
        fn = r.get("file_name")
        if fn is not None:
            counts[fn] = counts.get(fn, 0) + 1
    for fn in file_names:
        count = counts.get(fn)
        if count is not None:
            logger.info("chunk_count 回填（回退查询）| book=%s file=%s count=%d", book_name, fn, count)
            db.query(Document).filter(
                Document.user_id == user_id,
                Document.book_name == book_name,
                Document.file_name == fn,
            ).update({"status": "ready", "chunk_count": count})
        else:
            logger.warning("chunk_count 未回填（不可查）| book=%s file=%s", book_name, fn)
            db.query(Document).filter(
                Document.user_id == user_id,
                Document.book_name == book_name,
                Document.file_name == fn,
            ).update({"status": "ready"})


async def _process_document_upload(
    user_id: int, book_name: str, book_id: str, saved_paths, work_dir: Path,
    deconstruct: bool = False,
) -> None:
    """后台任务：RAG_init_collection 入库 → 回填 status/chunk_count（最终一致，§4.7）。

    子任务 09：`deconstruct=True` 时，与 Milvus 入库**并行**跑 LangGraph 逐章解构
    （`asyncio.gather(..., return_exceptions=True)`，单侧失败互不影响）。
    """
    logger.info("入库任务开始 | user=%s book=%s book_id=%s deconstruct=%s",
                user_id, book_name, book_id, deconstruct)
    db = SessionLocal()
    try:
        first_row = (
            db.query(Document)
            .filter_by(user_id=user_id, book_name=book_name)
            .order_by(Document.id.asc())
            .first()
        )
        collection_key = first_row.milvus_collection if first_row else "content"
        tool = state.tool_map.get("RAG_init_collection")
        if tool is None:
            logger.warning("MCP 工具未就绪")
            # 用 basename（documents.file_name 是文件名，非全路径）
            _update_group_status(db, user_id, book_name, [Path(p).name for p in saved_paths], "failed")
            return
        file_names = [Path(p).name for p in saved_paths]
        # ★ 09：deconstruct 分支——先切章建 job（同步 DB 写走 to_thread），再与入库并行
        deconstruct_coro = None
        if deconstruct:
            job_id, _meta = await asyncio.to_thread(
                prepare_deconstruct_job, db, saved_paths, book_id, book_name, user_id)
            logger.info("解构 job 已建 | book=%s job=%s", book_id, job_id)
            deconstruct_coro = run_job(job_id)                 # LangGraph 逐章解构（FastAPI 主进程）
        # Milvus 入库（MCP 子进程）——与解构并行
        milvus_coro = tool.ainvoke({
            "collection_name": collection_key,
            "file_paths": saved_paths,
            "book_name": book_name,
            "book_id": book_id,
        })
        if deconstruct_coro is not None:
            result, deconstruct_exc = await asyncio.gather(
                milvus_coro, deconstruct_coro, return_exceptions=True)
            if isinstance(deconstruct_exc, BaseException):
                # 解构失败不影响入库（documents 状态仍由 Milvus 结果决定）
                logger.error("解构失败（不影响入库）| book=%s job=%s err=%s",
                             book_name, job_id, deconstruct_exc)
            if isinstance(result, BaseException):
                # 入库失败 → re-raise 交给外层 except 标 documents failed（解构已完成，不受影响）
                raise result
        else:
            result = await milvus_coro
        logger.info("入库完成 | book_id=%s result=%s", book_id, str(result)[:200])
        # 顶层计划外《入库 chunk 数回填优化》：工具直接带回逐文件计数（Option B，主路径不查询 Milvus）
        # 可见性：auto_flush=False 下刚写完查询读不到 → 不再二次查询，用工具计数规避竞争
        per_file = (
            result[2]
            if isinstance(result, (list, tuple)) and len(result) >= 3 and isinstance(result[2], dict)
            else None
        )
        real_collection = _real_collection(collection_key)
        missing: List[str] = []  # per_file 未上报（旧工具/兼容）→ 循环后批量回退
        for fn in file_names:
            count = per_file.get(fn) if per_file else None
            if count is not None and count > 0:
                logger.info("chunk_count 回填（工具计数）| book=%s file=%s count=%d", book_name, fn, count)
                db.query(Document).filter(
                    Document.user_id == user_id,
                    Document.book_name == book_name,
                    Document.file_name == fn,
                ).update({"status": "ready", "chunk_count": count})
            elif count == 0:
                # 顶层计划外《入库去重按书隔离与重复上传治理》：本次全被 content_hash 去重 →
                # 删除本次新建 processing 行（不碰旧 ready 行），停掉重复上传的 MySQL 行增长，
                # 也不回填旧 chunk 数掩盖"本次未写入"
                logger.warning("文件已存在（内容重复），本次跳过 | book=%s file=%s", book_name, fn)
                db.query(Document).filter(
                    Document.user_id == user_id,
                    Document.book_name == book_name,
                    Document.file_name == fn,
                    Document.status == "processing",
                    Document.book_id == book_id,
                ).delete(synchronize_session=False)
            else:
                # count is None：per_file 缺失（旧工具/兼容）→ 收集后批量回退查询
                missing.append(fn)

        if missing:
            _backfill_chunk_count_fallback(db, real_collection, book_id, book_name, user_id, missing)

        # 顶层计划外《入库去重按书隔离与重复上传治理》：整批均为重复内容 → WARNING，
        # "成功 0"不再无声伪装成成功
        if (
            isinstance(result, (list, tuple))
            and len(result) >= 3
            and result[0] == 0
            and result[1] == 0
            and per_file
            and all(v == 0 for v in per_file.values())
        ):
            logger.warning("整批 %d 个文件均为重复内容，无新入库", len(file_names))
        db.commit()
    except Exception as exc:
        logger.error("入库失败 | book_id=%s: %s", book_id, exc, exc_info=True)
        _update_group_status(db, user_id, book_name, [Path(p).name for p in saved_paths], "failed")
    finally:
        db.close()
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info("临时目录已清理: %s", work_dir)


@router.post("/upload")
def upload_document(
    book_name: str = Form(...),
    files: list[UploadFile] = File(...),
    collection_name: str = Form("content"),
    deconstruct: str = Form(None),                  # ★ 09："1"/"0"，缺省取 env NOVEL_DECONSTRUCT_ON_UPLOAD
    user: User = Depends(get_current_user),
    background_tasks: BackgroundTasks = None,
):
    """上传文档（txt/md/pdf），异步入库（状态机 processing→ready/failed）。

    子任务 09：`deconstruct=1` 时在同一后台任务内并行跑 LangGraph 解构（documents 状态机不变）。
    """
    book_name = book_name.strip()
    if not book_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="book_name 不能为空")
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未上传文件")

    # 1. 扩展名白名单 + 空文件校验
    file_names = []
    for f in files:
        ext = _ext(f.filename or "")
        if ext not in ALLOWED_EXTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不支持的文件类型 {ext}，仅支持 .txt/.md/.pdf",
            )
        content = f.file.read()
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"文件为空: {f.filename}")
        f.file.seek(0)
        file_names.append(f.filename)

    # 2. 暂存文件
    session_id = uuid.uuid4().hex[:8]
    work_dir = TEMP_UPLOAD_DIR / session_id
    work_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []
    try:
        for f in files:
            path = work_dir / (f.filename or "upload")
            content = f.file.read()
            path.write_bytes(content)
            saved_paths.append(str(path))
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="文件保存失败")

    # 3. 解析组 book_id（复用）或建组首行（新组）；为每个文件建 documents 行（文件级）
    # milvus_collection 列存 registry key（key 语义，见优化 Spec）
    collection_key = _normalize_collection_key(collection_name)
    real_collection = _real_collection(collection_key)
    db = SessionLocal()
    try:
        book_id = _resolve_group_book_id(db, user.id, book_name)
        rows_to_add = file_names
        if book_id is None:
            first = Document(
                user_id=user.id, book_name=book_name, file_name=file_names[0],
                file_type=_ext(file_names[0]), status="processing", milvus_collection=collection_key,
            )
            db.add(first)
            db.commit()
            db.refresh(first)
            book_id = f"doc_{user.id}_{first.id}"
            first.book_id = book_id  # 组锚点行回填稳定 book_id（顶层计划外）
            rows_to_add = file_names[1:]
        for fn in rows_to_add:
            db.add(Document(
                user_id=user.id, book_name=book_name, file_name=fn,
                file_type=_ext(fn), status="processing", milvus_collection=collection_key,
                book_id=book_id,
            ))
        db.commit()
    finally:
        db.close()

    # 4. 异步任务（BackgroundTask：响应后执行入库 + 可选并行解构）
    deconstruct_enabled = _resolve_deconstruct(deconstruct)
    background_tasks.add_task(
        _process_document_upload, user.id, book_name, book_id, saved_paths, work_dir,
        deconstruct_enabled,
    )
    return {"id": int(book_id.split("_")[-1]), "book_id": book_id, "status": "processing",
            "file_names": file_names, "deconstruct_enabled": deconstruct_enabled}


@router.get("/books")
def list_books_grouped(user: User = Depends(get_current_user)):
    """distinct (user, book_name) 聚合列表（左列：书分组与单文件删除）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(
                Document.book_name,
                func.count(Document.id).label("doc_count"),
                func.coalesce(func.sum(Document.chunk_count), 0).label("chunk_total"),
                func.max(Document.uploaded_at).label("uploaded_at"),
            )
            .filter(Document.user_id == user.id)
            .group_by(Document.book_name)
            .order_by(func.max(Document.uploaded_at).desc())
            .all()
        )
        # 前端 P0：每组补 book_id（前端 novel 端点 /api/novel/books/{book_id}/* 需它）。
        # 复用 _resolve_group_book_id：取组内首个 Document.book_id；存量 NULL 时派生 doc_{uid}_{首行id}
        # 并回填 —— 正是 P0「解锁存量书」的目标（若只按非空 book_id 建 map，会漏掉未回填的存量组）。
        books = []
        for r in rows:
            books.append({
                "book_name": r.book_name,
                "book_id": _resolve_group_book_id(db, user.id, r.book_name),
                "doc_count": int(r.doc_count),
                "chunk_total": int(r.chunk_total),
                "uploaded_at": r.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if r.uploaded_at else None,
            })
        return books
    finally:
        db.close()


@router.get("")
def list_documents(
    user: User = Depends(get_current_user),
    book_name: Optional[str] = None,
):
    """当前用户文档列表（文件级，按 uploaded_at DESC；可选 book_name 过滤）。"""
    db = SessionLocal()
    try:
        q = db.query(Document).filter(Document.user_id == user.id)
        if book_name:
            q = q.filter(Document.book_name == book_name)
        rows = (
            q.order_by(Document.uploaded_at.desc(), Document.id.desc()).all()
        )
        return [
            {
                "id": r.id,
                "book_name": r.book_name,
                "file_name": r.file_name,
                "file_type": r.file_type,
                "status": r.status,
                "chunk_count": r.chunk_count,
                "milvus_collection": r.milvus_collection,
                "uploaded_at": r.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if r.uploaded_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.delete("/books/{book_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(book_name: str, user: User = Depends(get_current_user)):
    """按书删除（幂等 204，顶层计划外）：删整组 document 行 + book_id 全部向量。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(Document)
            .filter(Document.user_id == user.id, Document.book_name == book_name)
            .all()
        )
        if not rows:
            return Response(status_code=status.HTTP_204_NO_CONTENT)  # 已删/不存在，幂等
        book_id = _resolve_group_book_id(db, user.id, book_name)

        # 先删 Milvus（整书向量，无 file_name）；失败 → 500 行保留可重试
        tool = state.tool_map.get("RAG_delete_by_book_id")
        if tool and book_id:
            try:
                await tool.ainvoke({"collection_name": rows[0].milvus_collection, "book_id": book_id})
            except Exception as exc:
                logger.error("Milvus 删除失败 | book_id=%s: %s", book_id, exc, exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="删除向量失败，请稍后重试",
                )

        # 后删 MySQL 组内全部行
        db.query(Document).filter_by(user_id=user.id, book_name=book_name).delete()
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        db.close()


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: int, user: User = Depends(get_current_user)):
    """单文件删除（幂等 204，顶层计划外）：删该 document 行 + book_id+file_name 向量。"""
    db = SessionLocal()
    try:
        row = db.query(Document).filter(Document.id == doc_id).first()
        if row is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)  # 已删/不存在，幂等
        if row.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
        book_id = _resolve_group_book_id(db, user.id, row.book_name)

        # 先删 Milvus（仅该书该文件的向量）；失败 → 500 行保留可重试
        tool = state.tool_map.get("RAG_delete_by_book_id")
        if tool and book_id:
            try:
                await tool.ainvoke({
                    "collection_name": row.milvus_collection,
                    "book_id": book_id,
                    "file_name": row.file_name,
                })
            except Exception as exc:
                logger.error("Milvus 删除失败 | book_id=%s: %s", book_id, exc, exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="删除向量失败，请稍后重试",
                )

        # 后删 MySQL 该行（仅当前 document）
        db.delete(row)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        db.close()


# ================= 内置测试数据初始化（顶层计划外） =================

SEED_USER_PHONE = "12345678910"
SEED_USER_PASSWORD = "1234567"
SEED_BOOK_NAME = "示例知识库"


def _seed_dir() -> Path:
    """种子目录：SEED_DOCS_DIR 优先；缺省 <repo>/docs/start_files。"""
    env_dir = os.getenv("SEED_DOCS_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parents[3] / "docs" / "start_files"


async def seed_builtin_test_data() -> None:
    """内置测试数据初始化（顶层计划外《初始化内置测试数据-基础用户与start_files自动导入》）：
    把 docs/start_files 走真实导入路径（_process_document_upload → MCP RAG_init_collection）入库，
    全部绑定到基础用户（12345678910 / 1234567，init_db.sql 预置）。

    - 开关：SEED_DOCS_ENABLED（默认 1）
    - 幂等：该用户下 book_name=示例知识库 已有 ready 行 → 跳过；content_hash 去重兜底
    - 容错：任何异常只记日志，不崩应用；下次启动可重试
    """
    if os.getenv("SEED_DOCS_ENABLED", "1") != "1":
        logger.info("SEED_DOCS_ENABLED!=1，跳过内置测试数据初始化")
        return

    seed_dir = _seed_dir()
    if not seed_dir.is_dir():
        logger.info("种子目录不存在，跳过初始化: %s", seed_dir)
        return
    files = sorted(
        p for p in seed_dir.iterdir()
        if p.is_file() and _ext(p.name) in ALLOWED_EXTS
    )
    if not files:
        logger.info("种子目录无可入库文件，跳过初始化: %s", seed_dir)
        return
    file_names = [p.name for p in files]

    book_id: Optional[str] = None
    user_id: Optional[int] = None
    saved_paths: List[str] = []
    work_dir: Optional[Path] = None

    db = SessionLocal()
    try:
        # 1) 基础用户（init_db.sql 预置；未跑 SQL 则防御性创建）
        user = db.query(User).filter(User.phone == SEED_USER_PHONE).first()
        if user is None:
            user = User(
                phone=SEED_USER_PHONE,
                email=None,
                password_hash=hash_password(SEED_USER_PASSWORD),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info("基础用户已创建: %s (id=%s)", SEED_USER_PHONE, user.id)
        user_id = user.id

        # 2) 幂等：该用户 book_name=示例知识库 已有 ready 行 → 跳过
        done = (
            db.query(Document)
            .filter(
                Document.user_id == user.id,
                Document.book_name == SEED_BOOK_NAME,
                Document.status == "ready",
            )
            .first()
        )
        if done is not None:
            logger.info("内置测试数据已初始化（book=%s），跳过", SEED_BOOK_NAME)
            return

        # 3) 拷贝种子文件到临时目录（镜像真实上传的暂存+清理语义，源文件不动）
        work_dir = TEMP_UPLOAD_DIR / uuid.uuid4().hex[:8]
        work_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            dst = work_dir / f.name
            dst.write_bytes(f.read_bytes())
            saved_paths.append(str(dst))

        # 4) 建 documents 行（组锚点行定 book_id=doc_{uid}_{id}）
        first = Document(
            user_id=user.id, book_name=SEED_BOOK_NAME, file_name=file_names[0],
            file_type=_ext(file_names[0]), status="processing", milvus_collection="content",
        )
        db.add(first)
        db.commit()
        db.refresh(first)
        book_id = f"doc_{user.id}_{first.id}"
        first.book_id = book_id
        for fn in file_names[1:]:
            db.add(Document(
                user_id=user.id, book_name=SEED_BOOK_NAME, file_name=fn,
                file_type=_ext(fn), status="processing", milvus_collection="content",
                book_id=book_id,
            ))
        db.commit()
    except Exception as exc:
        db.rollback()
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)
        logger.error("内置测试数据初始化-准备失败: %s", exc, exc_info=True)
        return
    finally:
        db.close()

    if user_id is None or book_id is None or not saved_paths:
        return

    # 5) 复用真实 worker 入库（内部自行开 SessionLocal + 回填状态 + 清理 work_dir）
    logger.info("内置测试数据初始化开始 | user=%s book=%s files=%s", user_id, SEED_BOOK_NAME, file_names)
    try:
        await _process_document_upload(user_id, SEED_BOOK_NAME, book_id, saved_paths, work_dir)
    except Exception as exc:
        logger.error("内置测试数据初始化-入库失败: %s", exc, exc_info=True)
