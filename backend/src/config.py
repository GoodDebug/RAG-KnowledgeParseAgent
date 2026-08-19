# 预期集合字段定义（修改结构直接改这里）
import os
from typing import Dict, List

from pymilvus import DataType



# 向量数据库存储结构

# ========== 文本块 ==============
# ========== 预期集合字段定义 ==========
EXPECTED_SCHEMA_CONTENT = [
    {
        "name": "chunk_id",
        "datatype": DataType.VARCHAR,
        "max_length": 100,
        "is_primary": True,
        "description": "文本块唯一主键ID"
    },
    {
        "name": "content",
        "datatype": DataType.VARCHAR,
        "max_length": 2000,
        "is_primary": False,
        "description": "文本块原文内容"
    },
    {
        "name": "file_name",
        "datatype": DataType.VARCHAR,
        "max_length": 200,
        "is_primary": False,
        "description": "所属源文件名"
    },
    {
        "name": "book_id",
        "datatype": DataType.VARCHAR,
        "max_length": 50,
        "is_primary": False,
        "description": "所属小说唯一标识"
    },
    {
        "name": "book_name",
        "datatype": DataType.VARCHAR,
        "max_length": 200,
        "is_primary": False,
        "description": "所属小说名称"
    },
    {
        "name": "content_hash",
        "datatype": DataType.VARCHAR,
        "max_length": 32,
        "is_primary": False,
        "description": "内容 MD5 指纹，用于精确去重"
    },
    {
        "name": "chapter_title",
        "datatype": DataType.VARCHAR,
        "max_length": 200,  # 章节标题预留长度
        "is_primary": False,
        "description": "所属章节标题"
    },
    {
        "name": "chapter_index",
        "datatype": DataType.INT32,
        "is_primary": False,
        "description": "所属章节序号"
    },
    {
        "name": "chunk_index",
        "datatype": DataType.INT32,
        "is_primary": False,
        "description": "文档内文本块序号"
    },
    {
        "name": "chunk_size",
        "datatype": DataType.INT32,
        "is_primary": False,
        "description": "当前文本块字符长度"
    },
    {
        "name": "file_type",
        "datatype": DataType.VARCHAR,
        "max_length": 20,
        "is_primary": False,
        "description": "文件类型后缀"
    },
    {
        "name": "uploaded_at",
        "datatype": DataType.INT64,
        "is_primary": False,
        "description": "chunk 入库时间戳(unix 毫秒，Spec-C)"
    },
    {
        "name": "embedding",
        "datatype": DataType.FLOAT_VECTOR,
        "dim": 512,
        "is_primary": False,
        "description": "文本向量表征"
    }
]
# ========== 向量索引配置 ==========
INDEX_CONFIG_CONTENT = {
    "field_name": "embedding",
    "index_type": "IVF_FLAT",
    "metric_type": "COSINE",
    "params": {"nlist": 128}
}




# ========== 集合注册表 ==========
# 每新增一个集合，只需在这里加一项

COLLECTION_REGISTRY: Dict[str, dict] = {
    "content": {
        "collection_name": os.getenv("CONTENT_COLLECTION_NAME") or "content_knowledge",
        "description": os.getenv("CONTENT_COLLECTION_DESC") or "文档内容知识库",
        "schema": EXPECTED_SCHEMA_CONTENT,  # 使用下方 EXPECTED_SCHEMA_CONTENT
        "index_config": INDEX_CONFIG_CONTENT,  # 使用下方 INDEX_CONFIG_CONTENT
    },
    # 后续集合示例：
    # "entity": {
    #     "collection_name": "entity_knowledge",
    #     "description": "实体关系知识库",
    #     "schema": [...],
    #     "index_config": {...},
    # },
}

# 可用于 LLM 发现的可用集合名列表
COLLECTION_NAMES: List[str] = list(COLLECTION_REGISTRY.keys())