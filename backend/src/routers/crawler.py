"""
爬虫工具路由

POST /api/crawler/fetch          → 单篇爬取
POST /api/crawler/batch          → 批量爬取
POST /api/crawler/novel/chapters → 解析小说章节列表
POST /api/crawler/novel/crawl    → 爬取指定章节
"""

import asyncio
import logging
import random
from urllib.parse import urljoin

import requests

from typing import List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from UTILS.crawler_utils import (
    fetch_page_dynamic,
    fetch_page_via_intercept,
    extract_title,
    extract_chapter_list,
    extract_chapter_content,
    clean_text,
    sanitize_filename,
)

router = APIRouter(tags=["crawler"])
logger = logging.getLogger(__name__)


class CrawlRequest(BaseModel):
    url: str
    mode: str = "dynamic"  # "dynamic" | "intercept"
    api_pattern: str = "conapi.php"
    timeout: int = 15


class BatchCrawlRequest(BaseModel):
    urls: List[str]
    mode: str = "dynamic"
    api_pattern: str = "conapi.php"
    timeout: int = 15


@router.post("/fetch")
async def crawl_single(req: CrawlRequest):
    """单篇爬取：URL → 抓取 → 返回内容 + 文件名，供前端下载"""
    try:
        loop = asyncio.get_event_loop()

        if req.mode == "intercept":
            title, content = await loop.run_in_executor(
                None, fetch_page_via_intercept, req.url, req.api_pattern, req.timeout
            )
        else:
            html = await loop.run_in_executor(None, fetch_page_dynamic, req.url, req.timeout)
            title = await loop.run_in_executor(None, extract_title, html)
            content = await loop.run_in_executor(None, clean_text, html)

        content = await loop.run_in_executor(None, clean_text, content)
        filename = sanitize_filename(title) + ".txt"

        return {
            "success": True,
            "title": title,
            "content": content,
            "filename": filename,
            "content_length": len(content),
        }
    except Exception as e:
        logger.error("爬取失败: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/batch")
async def crawl_batch(req: BatchCrawlRequest):
    """批量爬取：多个 URL 逐个爬取，返回结果列表"""
    results = []
    for url in req.urls:
        try:
            loop = asyncio.get_event_loop()
            if req.mode == "intercept":
                title, content = await loop.run_in_executor(
                    None, fetch_page_via_intercept, url, req.api_pattern, req.timeout
                )
            else:
                html = await loop.run_in_executor(None, fetch_page_dynamic, url, req.timeout)
                title = await loop.run_in_executor(None, extract_title, html)
                content = await loop.run_in_executor(None, clean_text, html)

            content = await loop.run_in_executor(None, clean_text, content)
            results.append({
                "url": url, "success": True, "title": title,
                "content": content, "filename": sanitize_filename(title) + ".txt",
            })
        except Exception as e:
            results.append({"url": url, "success": False, "error": str(e)})

    return {"results": results, "total": len(req.urls), "success_count": sum(1 for r in results if r["success"])}


_REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 全局 Session：复用 TCP 连接，避免每次请求都三次握手
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.headers.update(_REQ_HEADERS)


def _fetch_html(url: str, timeout: int = 15) -> str:
    """同步 requests 获取 HTML，复用全局 Session"""
    resp = _HTTP_SESSION.get(url, timeout=timeout)
    resp.encoding = "utf-8"
    return resp.text


# ====================== 小说站点爬取 ======================


class NovelChaptersRequest(BaseModel):
    novel_url: str
    timeout: int = 15


class NovelCrawlRequest(BaseModel):
    chapters: List[dict]
    base_url: str = ""
    timeout: int = 15


@router.post("/novel/chapters")
async def novel_chapters(req: NovelChaptersRequest):
    """获取小说全部章节列表"""
    try:
        html = await asyncio.to_thread(_fetch_html, req.novel_url, req.timeout)
        chapters = await asyncio.to_thread(extract_chapter_list, html)
        return {"success": True, "total": len(chapters), "chapters": chapters}
    except Exception as e:
        logger.error("获取章节列表失败: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def _crawl_one(ch: dict, base_url: str, timeout: int) -> dict:
    """爬取单章（可被 gather 并发调用）"""
    try:
        # 随机延迟 0.3~1.5s，错开请求避免触发反爬
        await asyncio.sleep(0.3 + random.random() * 1.2)
        url = ch["url"]
        if not url.startswith("http"):
            url = urljoin(base_url, url)
        html = await asyncio.to_thread(_fetch_html, url, timeout)
        content = await asyncio.to_thread(extract_chapter_content, html)
        return {
            "success": True,
            "title": ch["title"],
            "content": content,
            "filename": sanitize_filename(ch["title"]) + ".txt",
        }
    except Exception as e:
        return {"success": False, "title": ch.get("title", ""), "error": str(e)}


@router.post("/novel/crawl")
async def novel_crawl(req: NovelCrawlRequest):
    """批量爬取指定章节（并发 20 章）"""
    coros = [_crawl_one(ch, req.base_url, req.timeout) for ch in req.chapters]
    results = await asyncio.gather(*coros, return_exceptions=True)
    # 将异常/非 dict 结果转为标准格式
    final = []
    for ch, r in zip(req.chapters, results):
        if isinstance(r, dict):
            final.append(r)
        elif isinstance(r, Exception):
            final.append({"success": False, "title": ch.get("title", ""), "error": str(r)})
        else:
            final.append({"success": False, "title": ch.get("title", ""), "error": "未知错误"})

    return {"results": final, "total": len(req.chapters), "success_count": sum(1 for r in final if r.get("success"))}
