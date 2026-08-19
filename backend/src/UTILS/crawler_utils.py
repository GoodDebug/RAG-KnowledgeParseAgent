# crawler_utils.py
# -*- coding: utf-8 -*-
"""
网页爬虫工具类。

提供浏览器管理、网页抓取、内容提取与保存功能。
复用 story_pachong/src/scraper.py 的核心逻辑，适配项目适配器风格。
"""

import atexit
import json
import logging
import os
import re
import threading

from typing import Optional, Tuple

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Playwright 可选导入 ──
_PLAYWRIGHT_AVAILABLE = True
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    _PLAYWRIGHT_AVAILABLE = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ====================== Playwright 浏览器单例 ======================

class PlaywrightBrowserManager:
    """Playwright 浏览器单例，全局复用同一 Chromium 实例。"""

    _lock = threading.Lock()
    _pw = None
    _browser = None

    @classmethod
    def _ensure_browser(cls):
        if cls._pw is not None and cls._browser is not None and cls._browser.is_connected():
            return
        with cls._lock:
            if cls._pw is None:
                if sync_playwright is None:
                    raise ImportError("Playwright 未安装: pip install playwright && playwright install chromium")
                cls._pw = sync_playwright().start()
            if cls._browser is None or not cls._browser.is_connected():
                cls._browser = cls._pw.chromium.launch(headless=True, args=["--no-sandbox"])

    @classmethod
    def get_page(cls, **kwargs):
        cls._ensure_browser()
        return cls._browser.new_page(**kwargs)

    @classmethod
    def release_page(cls, page):
        try:
            page.close()
        except Exception:
            pass

    @classmethod
    def shutdown(cls):
        with cls._lock:
            if cls._browser is not None:
                try:
                    cls._browser.close()
                except Exception:
                    pass
                cls._browser = None
            if cls._pw is not None:
                try:
                    cls._pw.stop()
                except Exception:
                    pass
                cls._pw = None

atexit.register(PlaywrightBrowserManager.shutdown)


# ====================== 爬取函数 ======================

def fetch_page_dynamic(url: str, timeout: int = 15) -> str:
    """使用 Playwright 渲染 JS 后返回完整 HTML。"""
    page = PlaywrightBrowserManager.get_page(
        user_agent=HEADERS["User-Agent"],
        locale="zh-CN",
        extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
    )
    try:
        page.goto(url, timeout=timeout * 1000)
        page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        return page.content()
    finally:
        PlaywrightBrowserManager.release_page(page)


def fetch_page_via_intercept(url: str, api_pattern: str = "conapi.php", timeout: int = 15) -> Tuple[str, str]:
    """拦截 API 响应，返回 (title, content)。"""
    page = PlaywrightBrowserManager.get_page(
        user_agent=HEADERS["User-Agent"],
        locale="zh-CN",
        extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
    )
    try:
        with page.expect_response(lambda resp: api_pattern in resp.url, timeout=timeout * 1000) as response_info:
            page.goto(url, timeout=timeout * 1000)
        response = response_info.value
        if not response.ok:
            raise RuntimeError(f"API 请求失败，状态码: {response.status}")
        json_data = json.loads(response.text())
        content = json_data["content"]
        title = page.title() or "untitled"
        return title, content
    finally:
        PlaywrightBrowserManager.release_page(page)


# ====================== 内容提取 ======================

def extract_title(html: str) -> str:
    """从 HTML 中提取文章标题。"""
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    if og and og.get("content", "").strip():
        return og["content"].strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True)
    return "untitled"


def clean_text(text: str) -> str:
    """清洗文本：合并空白、统一换行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 章节标题格式（修复：biquge2345 等站用数字前缀"001 初生"/"111【意志燃烧】"而非"第X章"，
# 故正文章节 = 「第X章」或「行首数字前缀」二选一；【】是正文标题的一部分，不是番外标记）
_CHAPTER_RE = re.compile(r"(?:第[0-9〇零一二两三四五六七八九十百千]+章|^\s*\d{1,})")
# extra = 番外/外传/作者公告类（请假/感言/活动/书评/新书/合作/上线等）
_EXTRA_RE = re.compile(r"外传|番外|同人|活动|书评|公告|感言|请假|漫画|新书|合作|上线")


def extract_chapter_list(html: str) -> list[dict]:
    """从小说主页 HTML 提取章节列表，返回 [{title, url, type}, ...]。

    正文章节支持两种标题格式：①「第X章」（第一章 山边小村）；②数字前缀（001 初生 / 1270 新版本…）。
    type："chapter"=正文；"extra"=番外/外传/作者公告；两者都不匹配的链接跳过。
    """
    soup = BeautifulSoup(html, "lxml")
    ul = soup.find("ul", class_="fen_4")
    if not ul:
        raise ValueError("未找到章节列表容器 ul.fen_4")
    chapters = []
    for a in ul.find_all("a"):
        href = a.get("href", "").strip()
        title = a.get_text(strip=True)
        if not href or not title:
            continue
        if _CHAPTER_RE.search(title):
            ch_type = "chapter"
        elif _EXTRA_RE.search(title):
            ch_type = "extra"
        else:
            continue
        chapters.append({"title": title, "url": href, "type": ch_type})
    if not chapters:
        raise ValueError("未找到符合格式的章节")
    return chapters


def extract_chapter_content(html: str) -> str:
    """从章节页 HTML 提取正文，保留自然段结构（按 <br/> 分割）。"""
    soup = BeautifulSoup(html, "lxml")
    content_div = soup.find("div", id="txt")
    if not content_div:
        raise ValueError("未找到正文内容 div#txt")

    # 按 <br/> 分割段落
    parts = []
    for child in content_div.children:
        if child.name == "br":
            continue
        text = child.get_text(strip=True) if hasattr(child, "get_text") else str(child).strip()
        if not text:
            continue
        # 跳过站点 header 广告（biquge2345.com 的推广提示）
        if "笔趣阁" in text or "biquge" in text.lower():
            continue
        parts.append(clean_text(text))

    return "\n\n".join(parts)


def sanitize_filename(name: str) -> str:
    """将标题转为合法文件名。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip().rstrip(".")
    return name or "untitled"


def save_article(title: str, content: str, output_dir: str = "output", fmt: str = "txt") -> str:
    """保存文章到本地文件，返回文件路径。"""
    os.makedirs(output_dir, exist_ok=True)
    filename = sanitize_filename(title) + f".{fmt}"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("已保存: %s", filepath)
    return filepath
