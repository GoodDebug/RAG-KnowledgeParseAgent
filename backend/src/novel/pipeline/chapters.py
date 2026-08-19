# -*- coding: utf-8 -*-
"""
本地章节切分与 novel_chapter 原文入库（子任务 01）—— 解构流水线的"入口"。

职责：把上传的 TXT/MD 文件在 FastAPI 主进程**按章节切分**，章节原文幂等落 `novel_chapter` 表，
并附带全局章节序号、字符偏移、场景切分计数等元数据 —— 成为后续 LangGraph 逐章解构的数据源。

关键设计（为什么这里"不碰 chunk"）：
  - chunk 只属于**入库路**（MCP 子进程把章节切成 500/120 块 → embed → Milvus）；
  - 本模块只产出**章节原文**（章节级溯源），LLM 解构读的是章节原文，不是 chunk。
  为保证与 Milvus 分章边界一致，这里**只读复用** `RAG.TextSplitter` 的章节检测逻辑，不改其代码。

主入口 `extract_and_persist` 流程：
  加载文件 → 切章（txt/md/无标记兜底）→ 分配全局 chapter_index → 场景切分 → 幂等 upsert。
"""
import hashlib
import logging
import os
import re

from langchain_core.documents import Document
from sqlalchemy import text

from novel.config import novel_chapter_max_chars
from novel.persistence.repositories import get_max_chapter_index
from novel.persistence.upsert import upsert_novel_chapter
from RAG.DocumentLoader import DocumentLoadConfig, RAG_Document_Loader
from RAG.TextSplitter import MarkdownDocumentSplitter, NovelTextSplitter

logger = logging.getLogger("novel.chapters")


class ChapterSplitError(Exception):
    """章节切分失败（文件加载失败等）。"""


# ====================== 确定性 ID ======================


def _make_chapter_id(book_id: str, file_name: str, chapter_index_in_file: int) -> str:
    """确定性 chapter_id = nch_{sha1(book_id|file_name|chapter_index_in_file)[:20]}。

    用哈希而非自增 ID：同一 (书, 文件, 文件内章节号) 永远得到同一个 ID →
    跨重启、跨重跑都稳定，可作为幂等 upsert 的稳定标识（不依赖数据库自增）。
    """
    raw = f"{book_id}|{file_name}|{chapter_index_in_file}"
    return "nch_" + hashlib.sha1(raw.encode()).hexdigest()[:20]


# ====================== 章节切分（TXT / MD） ======================


def _txt_chapters(doc: Document) -> list[dict]:
    """TXT/PDF：按 `NovelTextSplitter.CHAPTER_PATTERN` 切章。

    复用既有正则（`第X章/回/节/卷/集/部`），切片边界与 `NovelTextSplitter._split_by_chapter`
    完全一致 —— 保证"解构侧的章节"与"MCP 入库侧的章节"边界一致。
    章节原文 = 标题行到下一章标题之间的文本（含标题行）；无章节标记 → 整文件 1 章。
    """
    text = doc.page_content
    file_name = doc.metadata.get("file_name", "unknown")
    matches = list(NovelTextSplitter.CHAPTER_PATTERN.finditer(text))

    if not matches:
        # 无章节标记兜底：整文件当作 1 章（chapter_title = 文件名）
        return [{
            "file_name": file_name,
            "chapter_title": file_name,
            "chapter_text": text,
            "char_offset_start": 0,
            "char_offset_end": len(text),
        }]

    chapters = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)  # 下一章标题即本章结尾
        chapters.append({
            "file_name": file_name,
            "chapter_title": m.group(1).strip(),
            "chapter_text": text[start:end].strip(),
            "char_offset_start": start,   # 本章在清洗后文档文本中的字符偏移起点
            "char_offset_end": end,       # 偏移终点（供前端"跳转原文"定位）
        })
    return chapters


def _char_offset(lines: list[str], line_idx: int) -> int:
    """第 line_idx 行在 join(lines, '\\n') 中的字符偏移（近似，换行记 1 字符）。"""
    return sum(len(lines[i]) + 1 for i in range(min(line_idx, len(lines))))


def _md_chapters(doc: Document) -> list[dict]:
    """MD：按 `#` 标题结构切章（复用 `MarkdownDocumentSplitter._build_blocks` 识别结构块）。

    用结构块而非简单行扫描的原因：`_build_blocks` 能识别代码围栏/表格，
    保证代码块里的 `#` 行不会被误判为标题。
    每个标题起新章；无标题 → 整文件 1 章（chapter_title = 文件名）。
    """
    text = doc.page_content
    file_name = doc.metadata.get("file_name", "unknown")
    blocks = MarkdownDocumentSplitter()._build_blocks(text)  # 复用：识别 heading/table/code/text 块
    lines = text.splitlines()

    has_heading = any(b.get("type") == "heading" for b in blocks)
    if not has_heading:
        return [{
            "file_name": file_name,
            "chapter_title": file_name,
            "chapter_text": text,
            "char_offset_start": 0,
            "char_offset_end": len(text),
        }]

    chapters: list[dict] = []
    current_title = "无章节"
    current_start = 0
    current_end = 0
    current_lines: list[str] = []

    def flush() -> None:
        """把当前累计的块文本收成一章。"""
        nonlocal current_title
        body = "\n".join(current_lines).strip("\n")
        if body.strip():
            chapters.append({
                "file_name": file_name,
                "chapter_title": current_title,
                "chapter_text": body,
                "char_offset_start": current_start,
                "char_offset_end": current_end,
            })
        current_lines.clear()

    for b in blocks:
        if b.get("type") == "heading":
            flush()                            # 遇新标题：先收掉上一章
            current_title = (b.get("title") or "无章节").strip() or "无章节"
            current_start = _char_offset(lines, b["start"])
            current_end = _char_offset(lines, b["start"] + 1)
            current_lines.append(lines[b["start"]])   # 包含标题源行（如 "# 第一章 xxx"）
        else:
            current_lines.extend(lines[b["start"]:b["end"]])  # 表格/代码/文本块并入本章
            current_end = _char_offset(lines, b["end"])
    flush()
    return chapters


# ====================== 场景切分（超长章节） ======================


def _split_scenes(chapter_text: str, max_chars: int | None = None) -> list[str]:
    """超长章节按段落贪心切场景（每 scene ≤ 阈值）；未超长返回 [整章]。

    为什么需要场景切分：单次喂给 LLM 的文本有长度上限（`NOVEL_CHAPTER_MAX_CHARS`，
    约 8k token）。一章可能上万字 → 切场景后，8 个解构 Agent 各自**循环场景**抽取。
    scenes 只在返回/State 中使用（graph 的 chapter_prepare），**不落库**。
    """
    limit = max_chars if max_chars is not None else novel_chapter_max_chars()
    if len(chapter_text) <= limit:
        return [chapter_text]

    scenes: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in chapter_text.split("\n\n"):      # 以段落为基本单位贪心拼装
        para = para.strip()
        if not para:
            continue
        if len(para) > limit:
            # 单段落本身超限：先落空当前场景，再按句硬切
            if current:
                scenes.append("\n\n".join(current))
                current, current_len = [], 0
            scenes.extend(_split_sentence(para, limit))
            continue
        if current and current_len + len(para) + 2 > limit:
            scenes.append("\n\n".join(current))  # 当前场景放不下 → 收尾开新场景
            current, current_len = [], 0
        current.append(para)
        current_len += len(para) + 2              # +2 近似两段间的换行
    if current:
        scenes.append("\n\n".join(current))
    return scenes


def _split_sentence(text: str, limit: int) -> list[str]:
    """超长段落：按句界（。！？）贪心组装为 ≤limit 段；超长单句硬切。"""
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])", text) if s.strip()]
    if not sentences:
        sentences = [text]

    out: list[str] = []
    current = ""
    for s in sentences:
        if len(s) > limit:
            # 单句也超限（病态超长句）→ 硬切为 ≤limit 的块
            if current:
                out.append(current)
                current = ""
            for i in range(0, len(s), limit):
                out.append(s[i:i + limit])
            continue
        if current and len(current) + len(s) > limit:
            out.append(current)
            current = ""
        current += s
    if current:
        out.append(current)
    return out


# ====================== 单文件切章 ======================


def _existing_index_map(db, book_id: str) -> dict[tuple[str, int], int]:
    """该书已入库章节的 (file_name, chapter_index_in_file) → chapter_index 映射。

    用途：重传/追加文件时，已有章节保留原全局序号、新章节从 MAX+1 递增。
    """
    rows = db.execute(
        text(
            "SELECT file_name, chapter_index_in_file, chapter_index "
            "FROM novel_chapter WHERE book_id = :b"
        ),
        {"b": book_id},
    ).mappings().all()
    return {(r["file_name"], r["chapter_index_in_file"]): r["chapter_index"] for r in rows}


def _load_and_split_file(file_path: str) -> list[dict]:
    """加载单个文件并按章节切分。

    复用 `RAG_Document_Loader`（含编码检测/文本清洗）；每文件一个 Document（整文件文本），
    再按文件类型走 md 或 txt 的章节切分。
    :return: 每章 {file_name, chapter_title, chapter_text, char_offset_start, char_offset_end}
    """
    file_type = os.path.splitext(file_path)[1].lower()
    config = DocumentLoadConfig.from_env()
    success, docs = RAG_Document_Loader([file_path], config)
    if not success or not docs:
        raise ChapterSplitError(f"文件加载失败：{file_path}")
    doc = docs[0]  # 每文件一个 Document（整文件文本）
    if file_type == ".md":
        return _md_chapters(doc)
    return _txt_chapters(doc)


# ====================== 主入口 ======================


def extract_and_persist(
    file_paths: list[str],
    book_id: str,
    book_name: str,
    user_id: int,
    db,
) -> list[dict]:
    """主入口：加载文件 → 切章 → 分配全局索引 → 场景切分 → 幂等落库（不碰 chunk）。

    :param file_paths: 待处理的源文件路径列表（temp/uploads 暂存）
    :param book_id: doc_{user_id}_{doc_id}
    :param book_name: 书名分组
    :param user_id: 归属用户（仅日志）
    :param db: SQLAlchemy Session
    :return: 每章元数据 [{chapter_id, book_id, book_name, file_name, chapter_index,
        chapter_index_in_file, chapter_title, char_offset_start, char_offset_end, scene_count}]
    """
    # 1. 逐文件切章（章节原文 + 偏移；chapter_index_in_file 文件内 0-based）
    all_chapters: list[dict] = []
    for fp in file_paths:
        chapters = _load_and_split_file(fp)
        for i, ch in enumerate(chapters):
            ch["chapter_index_in_file"] = i       # 文件内章节序号（对齐 Milvus chunk）
            all_chapters.append(ch)
        logger.info("切章完成 | file=%s chapters=%d", os.path.basename(fp), len(chapters))

    if not all_chapters:
        logger.warning("无有效章节 | book=%s files=%s", book_id, file_paths)
        return []

    # 2. 全局章节序号
    #    已存在的章节（同 book_id + file_name + chapter_index_in_file）保留原 chapter_index；
    #    新章节从 base = MAX+1 起递增分配（uk 幂等，重复上传不重编号）。
    existing = _existing_index_map(db, book_id)
    base = get_max_chapter_index(db, book_id)
    next_idx = base + 1
    for ch in all_chapters:
        key = (ch["file_name"], ch["chapter_index_in_file"])
        if key in existing:
            ch["chapter_index"] = existing[key]   # 重传：保留原全局序号（锚点稳定）
        else:
            ch["chapter_index"] = next_idx
            next_idx += 1

    # 3. 场景切分（scenes 文本不落库，仅统计 scene_count 供图/状态使用）
    for ch in all_chapters:
        ch["scene_count"] = len(_split_scenes(ch["chapter_text"]))

    # 4. 幂等落库（chapter_index 不更新：保留原全局索引）
    results: list[dict] = []
    for ch in all_chapters:
        row = {
            "chapter_id": _make_chapter_id(book_id, ch["file_name"], ch["chapter_index_in_file"]),
            "book_id": book_id,
            "book_name": book_name,
            "file_name": ch["file_name"],
            "chapter_index": ch["chapter_index"],
            "chapter_index_in_file": ch["chapter_index_in_file"],
            "chapter_title": ch["chapter_title"],
            "chapter_text": ch["chapter_text"],
            "char_offset_start": ch["char_offset_start"],
            "char_offset_end": ch["char_offset_end"],
            "scene_count": ch["scene_count"],
        }
        affected = upsert_novel_chapter(db, row)
        results.append({k: row[k] for k in (
            "chapter_id", "book_id", "book_name", "file_name", "chapter_index",
            "chapter_index_in_file", "chapter_title", "char_offset_start",
            "char_offset_end", "scene_count",
        )})
        logger.debug("章节落库 | book=%s ch=%d/%s affected=%d",
                     book_id, row["chapter_index"], row["chapter_id"], affected)

    logger.info("切章入库完成 | book=%s user=%s chapters=%d", book_id, user_id, len(results))
    return results
