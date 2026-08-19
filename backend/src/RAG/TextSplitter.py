import re
from abc import ABC, abstractmethod
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from markdown_it import MarkdownIt


# ====================== 分块器统一接口 ======================

class BaseDocumentSplitter(ABC):
    """
    文本分块器统一接口（镜像 BaseEmbeddingAdapter）
    所有格式分块器（NovelTextSplitter / MarkdownDocumentSplitter）实现同一入口，
    上层通过工厂 create_document_splitter 获取实例，无需感知具体分块器类。
    """
    @abstractmethod
    def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        对Document列表执行分块
        :param documents: 加载器产出的原始文档列表
        :return: 分块后的文档列表，完整继承元数据+分块信息
        """
        pass


# ====================== 小说/纯文本分块器（既有） ======================

class NovelTextSplitter(BaseDocumentSplitter):
    """
    长篇小说专属文本分块器
    支持两种模式：
    1. 章节感知两阶段分块（默认推荐）：先按章节切，再章节内细分
    2. 纯递归字符分块：无规范章节标题时使用
    """

    # 中文小说章节标题正则，覆盖绝大多数命名格式
    CHAPTER_PATTERN = re.compile(
        r"^\s*(第[一二三四五六七八九十百千万零〇0-9]+[章回节卷集部]\s*.*?)\s*$",
        re.MULTILINE
    )

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 120,
        enable_chapter_split: bool = False
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.enable_chapter_split = enable_chapter_split

        # 针对中文小说优化的递归分块器
        self._char_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            # 中文叙事文本专属分隔符优先级：段落 → 句末 → 句中 → 强制切分
            separators=["\n\n", "\n", "。", "！", "？", "，", "；", " ", ""]
        )

    def _split_by_chapter(self, doc: Document) -> List[Document]:
        """第一阶段：按章节标题粗切分"""
        text = doc.page_content
        matches = list(self.CHAPTER_PATTERN.finditer(text))

        # 没匹配到章节，直接返回原文档走二次细分
        if not matches:
            return [doc]

        chapter_docs = []
        for i, match in enumerate(matches):
            chapter_title = match.group(1).strip()
            start = match.start()
            # 下一章开头作为当前章结束位置
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chapter_content = text[start:end].strip()

            # 继承原文档元数据，新增章节信息
            chapter_meta = doc.metadata.copy()
            chapter_meta["chapter_title"] = chapter_title
            chapter_meta["chapter_index"] = i

            chapter_docs.append(Document(
                page_content=chapter_content,
                metadata=chapter_meta
            ))

        return chapter_docs

    def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        主入口：对Document列表做小说专属分块
        :param documents: 加载器产出的原始文档列表
        :return: 分块后的文档列表，完整继承元数据+分块信息
        """
        all_chunks = []

        for doc in documents:
            # 1. 先按章节粗分（开启时）
            if self.enable_chapter_split:
                chapter_docs = self._split_by_chapter(doc)
            else:
                chapter_docs = [doc]

            # 2. 对每个章节做递归字符细分
            for chapter_doc in chapter_docs:
                chunks = self._char_splitter.split_documents([chapter_doc])

                # 3. 补充分块序号元数据
                for idx, chunk in enumerate(chunks):
                    chunk.metadata["chunk_index"] = idx
                    chunk.metadata["chunk_total"] = len(chunks)
                    chunk.metadata["chunk_size"] = len(chunk.page_content)
                    chunk.metadata["content_length"] = len(chunk.page_content)
                    all_chunks.append(chunk)

        return all_chunks


# ====================== Markdown 结构感知分块器 ======================

class MarkdownDocumentSplitter(BaseDocumentSplitter):
    """
    Markdown 结构感知分块器（Spec-C 扩展）
    基于 markdown-it-py 结构 token，处理纯 txt 没有的内容类型：
    - 标题层级：开新 section，标题面包屑进 metadata（header_path → chapter_title，复用 Milvus schema）
    - 表格：小表整块保留；超长表按行边界切分并重复表头（绝不切中行）
    - 代码块：整段原子保留（content_type="code"）
    - 兜底：全文无 # 标题 → 退化为与 NovelTextSplitter 相同行为
    """

    # 参与文本流划分的 token 类型（其余结构 token 由专项分支处理）
    _FLOW_TYPES = {
        "paragraph_open", "inline", "list_item_open", "bullet_list_open",
        "ordered_list_open", "blockquote_open", "hr", "html_block",
        "code_block", "image", "softbreak",
    }

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 120
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # 不用 gfm-like 预设（其 linkify 依赖 linkify-it-py，未安装）
        self._md = MarkdownIt("commonmark").enable("table")
        # 与 NovelTextSplitter 一致的中文分隔符序列
        self._char_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", "，", "；", " ", ""]
        )

    # ---------- 主入口 ----------

    def split_documents(self, documents: List[Document]) -> List[Document]:
        """主入口：对Document列表做 markdown 结构感知分块"""
        all_chunks = []
        for doc in documents:
            blocks = self._build_blocks(doc.page_content)
            has_heading = any(b["type"] == "heading" for b in blocks)

            # 无 markdown 标题结构（纯小说/纯文本 .md）→ 兜底复用小说分块行为
            if not has_heading or not blocks:
                fallback = NovelTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    enable_chapter_split=True,
                )
                all_chunks.extend(fallback.split_documents([doc]))
                continue

            raw = self._split_structured(doc, blocks)
            if not raw:
                # 极端：有标题结构但无正文 → 兜底保证至少产出块
                fallback = NovelTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    enable_chapter_split=True,
                )
                all_chunks.extend(fallback.split_documents([doc]))
                continue

            # 统一补充分块序号元数据（与 NovelTextSplitter 一致，chunk_id 生成不变）
            for idx, chunk in enumerate(raw):
                chunk.metadata["chunk_index"] = idx
                chunk.metadata["chunk_total"] = len(raw)
                chunk.metadata["chunk_size"] = len(chunk.page_content)
                chunk.metadata["content_length"] = len(chunk.page_content)
                all_chunks.append(chunk)

        return all_chunks

    # ---------- 块构建 ----------

    def _build_blocks(self, text: str) -> List[dict]:
        """基于 markdown-it 结构 token 将全文划分为有序块：heading / table / code / text"""
        tokens = self._md.parse(text)
        lines = text.splitlines()
        n = len(lines)
        if n == 0:
            return []

        # 行类型标记：高优先级覆盖低优先级（code > heading/table > text）
        line_type = ["text"] * n
        spans = []
        for i, t in enumerate(tokens):
            if not t.map:
                continue
            s, e = t.map
            if t.type == "fence":
                spans.append((3, s, e, "code"))
            elif t.type == "heading_open":
                spans.append((2, s, e, "heading"))
            elif t.type == "table_open":
                # table_open.map 覆盖整张表（表头行 → 末行），无需依赖 table_close.map（其为 None）
                spans.append((2, s, e, "table"))
            elif t.type in self._FLOW_TYPES:
                spans.append((1, s, e, "text"))

        # 低优先级先应用，高优先级后应用并覆盖（code > heading/table > text）
        spans.sort(key=lambda x: x[0])
        for _, s, e, typ in spans:
            for k in range(s, min(e, n)):
                line_type[k] = typ

        # 合并连续同类行为一个块
        blocks = []
        for idx, typ in enumerate(line_type):
            if blocks and blocks[-1]["type"] == typ:
                blocks[-1]["end"] = idx + 1
            else:
                blocks.append({"type": typ, "start": idx, "end": idx + 1})

        # 附加 heading 标题层级 与 code 内容
        for i, t in enumerate(tokens):
            if not t.map:
                continue
            if t.type == "heading_open":
                title = tokens[i + 1].content if (i + 1 < len(tokens) and tokens[i + 1].type == "inline") else ""
                for b in blocks:
                    if b["type"] == "heading" and b["start"] == t.map[0]:
                        b["level"] = int(t.tag[1])
                        b["title"] = title
                        break
            elif t.type == "fence":
                for b in blocks:
                    if b["type"] == "code" and b["start"] == t.map[0]:
                        b["content"] = t.content
                        break

        return blocks

    # ---------- 结构化切分 ----------

    def _split_structured(self, doc: Document, blocks: List[dict]) -> List[Document]:
        """按块遍历：标题开新 section，表格/代码独立出块，文本段累积后递归细分"""
        lines = doc.page_content.splitlines()
        out: List[Document] = []
        header_path: List[str] = []
        section_index = 0
        text_buf: List[str] = []

        def flush_text() -> None:
            """当前 section 累积的文本段统一递归细分"""
            if not text_buf:
                return
            joined = "\n".join(text_buf).strip("\n")
            text_buf.clear()
            if not joined:
                return
            for piece in self._char_splitter.split_text(joined):
                out.append(self._mk_doc(doc, "text", piece, header_path, section_index))

        for b in blocks:
            btype = b["type"]
            if btype == "heading":
                flush_text()
                # 重置面包屑到对应层级，标题作为章节上下文
                header_path = header_path[: b["level"] - 1] + ["#" * b["level"] + " " + b["title"].strip()]
                section_index += 1
            elif btype == "table":
                flush_text()
                table_text = "\n".join(lines[b["start"]:b["end"]])
                for seg in self._split_table(table_text):
                    # 正常组 ≤ chunk_size 原样通过；病态超长单行（> chunk_size）按行兜底，天然不切中行
                    for piece in self._split_by_lines(seg):
                        out.append(self._mk_doc(doc, "table", piece, header_path, section_index))
            elif btype == "code":
                flush_text()
                code = b.get("content", "").strip("\n")
                if code:
                    # 与 txt 共用分块预算：≤ chunk_size 保持原子；超限按行切（不切中行）
                    for seg in self._split_by_lines(code):
                        out.append(self._mk_doc(doc, "code", seg, header_path, section_index))
            else:  # text
                seg = "\n".join(lines[b["start"]:b["end"]]).strip("\n")
                if seg:
                    text_buf.append(seg)

        flush_text()
        return out

    def _mk_doc(self, doc: Document, content_type: str, content: str,
                header_path: List[str], section_index: int) -> Document:
        """构造带结构元数据的 Document：header_path[-1] 映射为 chapter_title（复用 Milvus schema）"""
        meta = dict(doc.metadata)
        meta["content_type"] = content_type
        meta["header_path"] = list(header_path)
        meta["chapter_title"] = header_path[-1] if header_path else "无章节"
        meta["chapter_index"] = section_index
        return Document(page_content=content, metadata=meta)

    # ---------- 表格行边界切分 ----------

    def _split_table(self, table_text: str) -> List[str]:
        """表格切分：小表整块；超长按行边界分组并重复表头，绝不切中行"""
        lines = table_text.splitlines()
        if not lines:
            return []
        header = lines[0].rstrip()
        # GFM 分隔行（|---|---| 或 |:--:|）与表头区分
        sep = lines[1] if len(lines) > 1 and set(lines[1].replace(" ", "")) <= {"|", ":", "-"} else None
        rows = lines[2:] if sep else lines[1:]

        if len(table_text) <= self.chunk_size or not rows:
            return [table_text.strip("\n")]

        # 超长：按行边界分组，每组重复表头行，保证续块自包含
        head_block = header + (("\n" + sep) if sep else "")
        groups: List[List[str]] = []
        current: List[str] = []
        current_len = len(head_block)
        for row in rows:
            row = row.rstrip()
            add = len(row) + 1
            if current and current_len + add > self.chunk_size:
                groups.append(current)
                current = []
                current_len = len(head_block)
            current.append(row)
            current_len += add
        if current:
            groups.append(current)

        return [head_block + "\n" + "\n".join(g) for g in groups]

    # ---------- 行边界兜底切分（与 txt 共用分块预算） ----------

    def _split_by_lines(self, content: str) -> List[str]:
        """
        行边界兜底切分：与 txt/文本共用分块预算（chunk_size）。
        - ≤ chunk_size → 原样返回（保持代码块/表格组原子性）
        - 超限 → 按行贪婪分组，每段 ≤ chunk_size，绝不切中行；
          仅单行本身超限（病态，如压缩后的单行代码）才硬切该行。
        目的：避免超预算 chunk 被 embedding 模型（默认 512 token）静默截断。
        """
        if len(content) <= self.chunk_size:
            return [content]

        pieces: List[str] = []
        current: List[str] = []
        current_len = 0
        for line in content.splitlines():
            add = len(line) + 1  # +1 换行符（保守，段实际略小于上限）
            if current and current_len + add > self.chunk_size:
                pieces.append("\n".join(current))
                current = []
                current_len = 0
            if len(line) > self.chunk_size:
                # 单行本身超限 → 硬切为多个 ≤ chunk_size 的段（最后手段）
                if current:
                    pieces.append("\n".join(current))
                    current = []
                    current_len = 0
                for i in range(0, len(line), self.chunk_size):
                    pieces.append(line[i:i + self.chunk_size])
            else:
                current.append(line)
                current_len += add

        if current:
            pieces.append("\n".join(current))
        return pieces


# ====================== 分块器工厂 ======================

def create_document_splitter(
    file_type: str,
    chunk_size: int = 500,
    chunk_overlap: int = 120,
) -> BaseDocumentSplitter:
    """
    分块器工厂：按文件类型返回对应分块器（镜像 create_embedding_adapter）
    :param file_type: 文件类型（.md/.txt/.pdf，来自 Document metadata）
    :return: BaseDocumentSplitter 实例，业务层只调 split_documents，不感知具体分块器类
    """
    ft = (file_type or "").strip().lower()
    if ft == ".md":
        return MarkdownDocumentSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    # .txt / .pdf / 未知 → 小说分块器（保持既有 500/120 + 章节感知行为）
    return NovelTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        enable_chapter_split=True,
    )
