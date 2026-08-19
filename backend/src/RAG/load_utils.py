import logging
from itertools import groupby

from typing import  List, Tuple
from langchain_core.documents import Document

from .DocumentLoader import  DocumentLoadConfig , RAG_Document_Loader
from .TextSplitter import create_document_splitter




logger = logging.getLogger(__name__)


def load_and_split_novels(
    file_paths: List[str],
    config: DocumentLoadConfig = None
) -> Tuple[bool, List[Document]]:
    """
    文档完整流水线：加载 → 清洗 → 分块
    按 file_type 分组，分块器由工厂 create_document_splitter 创建（镜像 create_embedding_adapter 哲学）；
    业务层（routers/MCP）只调本函数，不感知具体分块器类。
    """
    # 1. 复用你已有的批量加载能力

    success, docs = RAG_Document_Loader(file_paths, config)
    if not success:
        logger.error("❌ 文档加载失败")
        return False, []

    logger.info(f"文档加载完成，原始文档{len(docs)}个")

    # 2. 按 file_type 分组，工厂取分块器（.md → MarkdownDocumentSplitter；其余 → NovelTextSplitter）
    try:
        def _type_key(doc: Document) -> str:
            return doc.metadata.get("file_type", "")

        chunked_docs: List[Document] = []
        # 分块预算 500/120 与默认 embedding 模型（bge-small 系 max_seq_length=512 token）对齐；
        # 需更大分块请切换 embedding_adapters 的长上下文模型并同步调大此处 chunk_size（见其注释）
        for file_type, group in groupby(sorted(docs, key=_type_key), key=_type_key):
            splitter = create_document_splitter(
                file_type,
                chunk_size=500,
                chunk_overlap=120,
            )
            group_chunks = splitter.split_documents(list(group))
            chunked_docs.extend(group_chunks)

        logger.info(f"分块完成，共 {len(chunked_docs)} 个文本块")
    except Exception as e:
        logger.error(f"分块失败：{e}", exc_info=True)
        return False, []

    logger.info(f"文档加载完成，原始文档{len(docs)}个，分块后共{len(chunked_docs)}个文本块")


    return True, chunked_docs
