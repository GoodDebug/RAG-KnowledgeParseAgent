import os
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Tuple

from charset_normalizer import from_bytes
from langchain_core.documents import Document

# ====================== 全局初始化（仅执行一次） ======================
logger = logging.getLogger(__name__)


# ====================== 统一配置类 ======================
@dataclass
class DocumentLoadConfig:
    """文档加载统一配置：所有加载器共享，收口所有可调节参数"""
    default_encoding: str = "utf-8"
    encoding_confidence_threshold: float = 0.7
    min_content_length: int = 10
    enable_text_clean: bool = True
    encoding_try_order: List[str] = None
    encoding_detection_bytes: int = 4096

    @classmethod
    def from_env(cls) -> "DocumentLoadConfig":
        """从环境变量初始化配置，支持缺省兜底"""
        encoding_try_order_str = os.getenv("ENCODING_TRY_ORDER")
        if encoding_try_order_str:
            try:
                custom_order: List[str] = json.loads(encoding_try_order_str)
            except (json.JSONDecodeError, TypeError):
                logger.warning("ENCODING_TRY_ORDER 格式错误，使用默认编码序列")
                custom_order = None
        else:
            custom_order = None

        return cls(
            default_encoding=os.getenv("DEFAULT_ENCODING", "utf-8"),
            encoding_confidence_threshold=float(os.getenv("ENCODING_CONFIDENCE", "0.7")),
            min_content_length=int(os.getenv("MIN_CONTENT_LENGTH", "10")),
            encoding_try_order = custom_order,
            encoding_detection_bytes=int(os.getenv("ENCODING_DETECTION_BYTES", "4096")),
        )


# ====================== 抽象基类（模板方法模式） ======================
class BaseDocumentLoader(ABC):
    """
    文档加载器抽象基类
    采用模板方法模式：固定加载主流程，子类仅需实现核心解析逻辑
    """

    # 子类必须声明自身支持的文件后缀
    SUPPORTED_EXT: str = ""

    def __init__(self, file_path: str, config: DocumentLoadConfig):
        self.file_path = os.path.abspath(file_path)
        self.config = config
        self.logger = logger
        self._file_ext = os.path.splitext(self.file_path)[1].lower()

    def _validate_file(self) -> bool:
        """文件有效性校验，返回是否合法，不抛异常"""
        if not os.path.isfile(self.file_path):
            self.logger.error(f"文件不存在：{self.file_path}")
            return False
        return True

    @staticmethod
    def _clean_text_content(text: str) -> str:
        """静态工具：基础文本清洗，所有格式复用"""
        # 先去BOM再去首尾空白，逻辑更合理
        text = text.strip("\ufeff").strip()
        lines = [line.strip() for line in text.splitlines()]

        # 合并连续空行，保留单空行作为段落边界
        cleaned = []
        prev_empty = False
        for line in lines:
            if not line:
                if not prev_empty:
                    cleaned.append("")
                    prev_empty = True
            else:
                cleaned.append(line)
                prev_empty = False
        return "\n".join(cleaned)

    def _detect_encoding(self) -> str:
        """通用编码检测：charset-normalizer 自动检测（3.x API）+ 配置兜底（TXT/Markdown 复用）"""
        try:
            with open(self.file_path, "rb") as f:
                raw_bytes = f.read(self.config.encoding_detection_bytes)

            # charset_normalizer 3.x：best() 可能为 None；percent_chaos 越低越可信，映射回 0~1 置信度
            result = from_bytes(raw_bytes).best()
            if result is not None and result.encoding:
                confidence = max(0.0, 1.0 - result.percent_chaos / 100.0)
                if confidence >= self.config.encoding_confidence_threshold:
                    if result.bom and result.encoding.lower().replace("_", "-") == "utf-8":
                        return "utf-8-sig"
                    return result.encoding
            return self.config.default_encoding
        except Exception as e:
            self.logger.warning(f"编码检测失败，使用兜底编码：{str(e)}")
            return self.config.default_encoding

    def _inject_base_metadata(self, docs: List[Document], used_encoding: str = "") -> None:
        """统一注入元数据，所有格式复用，直接使用实例属性无需传参"""
        file_size = os.path.getsize(self.file_path)
        modify_time = datetime.fromtimestamp(os.path.getmtime(self.file_path)).isoformat()

        for doc in docs:
            doc.metadata.update({
                "file_name": os.path.basename(self.file_path),
                "file_path": self.file_path,
                "file_type": self._file_ext,
                "detected_encoding": used_encoding,
                "file_size_bytes": file_size,
                "file_modify_time": modify_time,
                "content_length": len(doc.page_content)
            })

    @abstractmethod
    def _parse_content(self) -> Tuple[bool, List[Document], str]:
        """
        子类必须实现的核心解析方法
        :return: (是否成功, 文档列表, 实际使用的编码/格式标识)
        """
        pass

    def load(self) -> Tuple[bool, List[Document], str]:
        """
        模板方法：固定加载主流程，子类无需重复实现
        统一契约：成功返回 True+文档列表，失败返回 False+空列表，不外抛异常
        """
        try:
            # 1. 文件校验
            if not self._validate_file():
                return False, [],'文件不合法或不存在'

            # 2. 子类核心解析
            success, docs, used_flag = self._parse_content()
            if not success or not docs:
                return False, [], used_flag

            # 3. 文本清洗（统一开关控制）
            if self.config.enable_text_clean:
                for doc in docs:
                    doc.page_content = self._clean_text_content(doc.page_content)

            # 4. 空内容过滤
            valid_docs = []
            for doc in docs:
                if len(doc.page_content.strip()) >= self.config.min_content_length:
                    valid_docs.append(doc)
            if not valid_docs:
                self.logger.warning(f"文件无有效内容，跳过：{self.file_path}")
                return False, [], f"文件无有效内容，跳过：{self.file_path}"

            # 5. 统一注入元数据
            self._inject_base_metadata(valid_docs, used_encoding=used_flag)

            return True, valid_docs,'加载成功'

        except Exception as e:
            self.logger.error(f"加载文件失败：{self.file_path}，原因：{str(e)}", exc_info=True)
            return False, [], f"加载文件失败：{self.file_path}，原因：{str(e)}"


# ====================== TXT 格式加载器实现 ======================
class TxtDocumentLoader(BaseDocumentLoader):
    SUPPORTED_EXT = ".txt"

    # 类常量：TXT专属编码兜底序列，不污染全局
    _DEFAULT_ENCODING_ORDER = ["utf-8", "gb18030", "utf-16", "latin-1"]

    def _parse_content(self) -> Tuple[bool, List[Document], str]:
        """实现TXT专属解析逻辑：编码检测+多级兜底"""
        detected_encoding = self._detect_encoding()

        # 构造尝试序列：检测结果优先，默认序列去重跟进
        try_encodings = [detected_encoding]
        for enc in self._DEFAULT_ENCODING_ORDER:
            if enc.lower() != detected_encoding.lower():
                try_encodings.append(enc)

        docs: List[Document] = []
        used_encoding = None
        last_error = None
        
        for enc in try_encodings:
            try:
                # 已被LangChain 官方弃用
                # loader = TextLoader(self.file_path, encoding=enc)
                # docs = loader.load()


                # 原生 Python 读取，完全替代 TextLoader，零社区依赖
                with open(self.file_path, "r", encoding=enc) as f:
                    content = f.read()
                # 复用 langchain_core 的 Document 数据结构，保持下游兼容
                docs = [Document(page_content=content, metadata={})]
                used_encoding = enc
                break
            except UnicodeDecodeError as e:
                last_error = e
                continue

        # 所有编码均失败
        if not docs:
            self.logger.error(
                f"TXT文件编码解析失败：{self.file_path}，尝试序列：{try_encodings}，最后错误：{str(last_error)}"
            )
            return False, [], f"TXT文件编码解析失败：{self.file_path}，尝试序列：{try_encodings}，最后错误：{str(last_error)}"


        return True, docs, used_encoding


class MarkdownDocumentLoader(BaseDocumentLoader):
    """Markdown 文档加载器（Spec-C）：编码检测（复用基类）+ utf-8/gb18030 兜底读全文。"""

    SUPPORTED_EXT = ".md"

    @staticmethod
    def _clean_text_content(text: str) -> str:
        """markdown 安全清洗：仅去 BOM/首尾空白，保留代码块/列表缩进与空行结构（覆盖基类逐行 strip）"""
        return text.strip("\ufeff").strip()

    def _parse_content(self) -> Tuple[bool, List[Document], str]:
        """实现Markdown专属解析：编码检测优先，utf-8/gb18030 兜底，记录实际使用的编码"""
        detected = self._detect_encoding()
        try_order = [detected, "utf-8", "gb18030"]

        content = None
        used_encoding = None
        last_error = None
        seen = set()
        for enc in try_order:
            if enc in seen:
                continue
            seen.add(enc)
            try:
                with open(self.file_path, "r", encoding=enc) as f:
                    content = f.read()
                used_encoding = enc
                break
            except UnicodeDecodeError as e:
                last_error = e
                continue
            except Exception as e:
                return False, [], f"Markdown 文件解析失败：{self.file_path}：{e}"

        if content is None:
            return False, [], f"Markdown 文件解析失败：{self.file_path}：{last_error}"
        return True, [Document(page_content=content, metadata={})], used_encoding


class PdfDocumentLoader(BaseDocumentLoader):
    """PDF 文档加载器（Spec-C）：pdfplumber 逐页提取文本。"""

    SUPPORTED_EXT = ".pdf"

    def _parse_content(self) -> Tuple[bool, List[Document], str]:
        import pdfplumber  # 懒 import（仅 PDF 路径需要）

        try:
            pages_text = []
            with pdfplumber.open(self.file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    if text.strip():
                        pages_text.append(text)
            content = "\n".join(pages_text)
            if not content.strip():
                return False, [], f"PDF 无可提取文本：{self.file_path}"
            return True, [Document(page_content=content, metadata={})], "pdf"
        except Exception as e:
            return False, [], f"PDF 解析失败：{self.file_path}：{e}"


# ====================== 批量加载器（与基类无缝配合） ======================
def RAG_Document_Loader(file_paths: List[str], config: DocumentLoadConfig = None) -> Tuple[bool, List[Document]]:
    """
    RAG 批量文档加载器：支持多文件加载，失败文件跳过并记录日志，与抽象基类配合，完全符合开闭原则
    新增格式只需：1. 新建子类 2. 注册到映射表，主逻辑一行不改
    :param file_paths: 文件路径列表
    :param default_encoding: 编码检测失败时的兜底编码，默认 utf-8
    :return: (是否至少有一个加载成功, 全部成功加载的文档列表)
    """
    if config is None:
        config = DocumentLoadConfig.from_env()

    all_docs: List[Document] = []
    failed_files: List[str] = []

    # 1. 前置校验：判空 + 规范化 + 去重 + 有效性过滤
    if not file_paths:
        logger.error("输入文件路径列表为空")
        return False, []
    logger.info(f"文件列表判空通过，共 {len(file_paths)} 个文件")

    valid_paths = []
    for fp in file_paths:
        abs_fp = os.path.abspath(fp)
        # 规范化路径为绝对路径，且文件大小为 0 的空文件，完全可以在校验阶段直接跳过，不用走到编码检测、解析流程，减少无效计算。将有效的文件路径保留，无效的加入 failed_files
        if os.path.getsize(abs_fp) == 0:
            logger.warning(f"文件大小为 0，跳过：{fp}")
            failed_files.append(fp)
            continue
        if os.path.isfile(abs_fp):
            valid_paths.append(abs_fp)
        else:
            logger.error(f"文件不存在/不是有效文件，跳过：{fp}")
            failed_files.append(fp)

    if not valid_paths:
        logger.error("无有效文件，加载终止")
        return False, [] 
    
    logger.info(f"有效文件路径列表，共 {len(valid_paths)} 个文件")

    # 去重避免重复加载, fromkeys保持原始顺序,符合最小惊讶原则
    unique_paths = list(dict.fromkeys(valid_paths))
    
    logger.info(f"去重后有效文件路径列表，共 {len(unique_paths)} 个文件")

    # 2. 格式-加载器映射表：新增格式只加这里
    loader_map = {
        ".txt": TxtDocumentLoader,
        ".md": MarkdownDocumentLoader,
        ".pdf": PdfDocumentLoader,
    }

    # 3. 批量分发
    for file_path in unique_paths:
        try:
            file_ext = os.path.splitext(file_path)[1].lower()
            loader_cls = loader_map.get(file_ext)

            if not loader_cls:
                logger.warning(f"不支持的文件格式 {file_ext}，跳过：{file_path}")
                failed_files.append(file_path)
                continue

            # 统一调用格式，完全不感知子类差异
            loader = loader_cls(file_path, config)
            success, docs, err_msg = loader.load()

            if success:
                all_docs.extend(docs)
                logger.info(f"加载成功：{file_path}，文档数：{len(docs)}")
            else:
                failed_files.append(file_path)
        except Exception as e:
            logger.error(f"加载失败：{file_path}，错误日志：{err_msg}，原因：{str(e)}", exc_info=True)
            failed_files.append(file_path)
            continue
        
    # 4. 统一返回
    if not all_docs:
        logger.error("全部文件加载失败")
        return False, []

    if failed_files:
        logger.warning(f"部分文件加载失败，共 {len(failed_files)} 个：{failed_files}")

    return True, all_docs