from asyncio.log import logger
import os

import httpx
import json
import sys
import logging

from fastmcp import FastMCP
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Dict, List, Optional, Callable, Tuple

# 将 FASTAPI/ 加入 sys.path，确保子进程运行时能找到 RAG/ 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ⚠️ 必须在任何 HuggingFace 相关 import 之前加载 .env，否则 HF_HUB_OFFLINE 不生效
# 容器内环境变量已由 Docker Compose 的 env_file 注入，无需再加载 .env
if not os.path.exists("/.dockerenv"):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from RAG.rerank_adapter import (
    BaseRerankAdapter,
    create_rerank_adapter
    )
from RAG.embedding_adapters import (
    BaseEmbeddingAdapter,
    create_embedding_adapter
    )
from pymilvus import MilvusClient
from RAG.RAG_Milvus_utils import (
    SearchResult,
    create_Milvus_client,
    delete_by_book_id,
    ensure_collection_ready,
    init_Milvus_Collection,
    search_Milvus
    )
from RAG.DocumentLoader import  DocumentLoadConfig

from config import (
    COLLECTION_REGISTRY, 
    COLLECTION_NAMES, 
    EXPECTED_SCHEMA_CONTENT, 
    INDEX_CONFIG_CONTENT
    )

# ====================== 环境变量类型转换工具 ======================

def _env_bool(key: str, default: str = "False") -> bool:
    """将 .env 字符串转为布尔值，处理 'true'/'false'/'1'/'0'/'yes'/'no'"""
    val = os.getenv(key, default)
    return val.strip().lower() in ("true", "1", "yes")

def _env_str_or_none(key: str, default: str = None) -> Optional[str]:
    """读取字符串，但将 .env 中的 'None' 字面量转回 Python None"""
    val = os.getenv(key, default)
    if val is not None and val.strip().lower() == "none":
        return None
    return val

def _env_int(key: str, default: str = None) -> Optional[int]:
    """读取整数，缺省时返回 None"""
    val = os.getenv(key, default)
    if val is None:
        return None
    return int(val.strip())

def _env_float(key: str, default: str = None) -> Optional[float]:
    """读取浮点数，缺省时返回 None"""
    val = os.getenv(key, default)
    if val is None:
        return None
    return float(val.strip())

# ====================== 配置区（修改结构/参数只改这里） ======================

# https://dev.qweather.com/ 和风天气API

WEATHER_API_KEY = _env_str_or_none("WEATHER_API_KEY")
WEATHER_API_URL = _env_str_or_none("WEATHER_API_URL")

# Embedding 模型配置
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME")
VEC_DIM = _env_int("VEC_DIM")  # 必须和模型输出维度、schema中向量维度三者一致
EMBEDDING_DEVICE = _env_str_or_none("EMBEDDING_DEVICE")  # None=自动检测，可选 "cpu"/"cuda"
QUERY_PREFIX = os.getenv("QUERY_PREFIX")  # BGE中文查询前缀

# 问答检索 Top-K（Spec-B：默认值收口到环境变量）
TOP_K_RETRIEVE = _env_int("TOP_K_RETRIEVE") or 50
TOP_K_RERANK = _env_int("TOP_K_RERANK") or 5

# 检索质量门控（默认 0.0 = 关闭；>0 时按 1-vector_score 过滤低相似度结果）
RAG_MIN_COSINE_SIM = _env_float("RAG_MIN_COSINE_SIM") or 0.0


def _apply_min_cosine_sim(results):
    """按余弦相似度门控过滤；threshold<=0 直接返回（默认关闭）。

    SearchResult.vector_score = Milvus COSINE 距离 = 1 - 余弦相似度（越小越相似），
    故门控表达式为 `1 - vector_score >= RAG_MIN_COSINE_SIM`。
    全部被过滤则返回 [] —— 上层 chat.py 的 _is_empty_result 会触发空检索兜底。
    """
    if not results or RAG_MIN_COSINE_SIM <= 0.0:
        return results
    kept = [r for r in results if (1.0 - float(r.vector_score)) >= RAG_MIN_COSINE_SIM]
    if not kept:
        logger.info("质量门控：全部 %d 条低于阈值 %.3f，返回空", len(results), RAG_MIN_COSINE_SIM)
    return kept


CONTENT_COLLECTION_NAME = os.getenv("CONTENT_COLLECTION_NAME")  # 内容集合名
# ================初始化配置=============================

# 全局依赖（在 main 中初始化）
milvus_client: Optional[MilvusClient] = None
embed_model: Optional[BaseEmbeddingAdapter] = None
reranker: Optional[BaseRerankAdapter] = None

# MCP 服务器配置
mcp = FastMCP(
    "ServerSSE"
)


os.environ["FASTMCP_BANNER"] = "0"

fastmcp_logger = logging.getLogger("fastmcp")
fastmcp_logger.setLevel(logging.WARNING)

# 日志
def log_info(msg: str):
    fastmcp_logger.info(msg)



# ================工具函数===============================

# 加载 Embedding 模型
def create_embedding_model():
    try:
        logger.info(f"\n正在加载 Embedding 模型：{EMBEDDING_MODEL_NAME}")
        embed_model = create_embedding_adapter(
            interface_format="load",
            model_name=EMBEDDING_MODEL_NAME
        )
        logger.info(f"模型加载完成，输出维度：%d", embed_model.emb_dim)
        return embed_model
    except Exception as e:
        logger.error(f"❌ 模型加载失败：{e}")
        exit(1)
        
# 初始化 Rerank 模型
def create_reranker_model():
    try:
        reranker_model = create_rerank_adapter(
            interface_format="load"
        )
        logger.info("✅ Rerank 模型加载成功")
        return reranker_model
    except Exception as e:
        logger.error(f"❌ Rerank 模型加载失败：{e}")
        exit(1)

# =============== 外挂RAG知识库操作工具 =====================================


# =============== 语义搜索工具 ===============
    
_COLLECTION_DESC = (
    "Milvus集合名称：只能填写左侧key，禁止使用括号中的描述。可选列表：{}"
).format(", ".join(
    f"{k}({v['description']})" for k, v in COLLECTION_REGISTRY.items()
))

class SearchQuery(BaseModel):
    """语义搜索查询参数"""
    query: str = Field(description="RAG系统接受的输入检索文本")
    collection_name: str = Field(description=_COLLECTION_DESC)
    book_id: Optional[str] = Field(default=None, description="所属小说ID，为空时检索全部小说")
    top_k_retrieve: int = Field(description="向量召回数量", default=TOP_K_RETRIEVE, gt=0, le=200)
    top_k_rerank: int = Field(description="重排序返回数量", default=TOP_K_RERANK, gt=0)

def _do_search(
    info: SearchQuery,
    real_collection: str,
    book_id: str,
) -> List[SearchResult]:
    """精确检索（带 book_id 过滤 + 重排序）"""
    return search_Milvus(
        query=info.query,
        collection_name=real_collection,
        milvus_client=milvus_client,
        embed_model=embed_model,
        reranker=reranker,
        book_id=book_id,
        top_k_retrieve=info.top_k_retrieve,
        top_k_rerank=info.top_k_rerank,
    )


@mcp.tool()
def RAG_search_by_query(
    info: SearchQuery
):
    """
    语义搜索（支持自动书籍识别）
    如果未指定 book_id，先粗召回统计内容来源，识别后精确检索。
    """
    # 检查 collection_name 是否在注册表中
    _DEFAULT_COLLECTION = next(iter(COLLECTION_REGISTRY))  # "content"

    target_key = info.collection_name
    if target_key not in COLLECTION_REGISTRY:
        logger.warning(
            f"集合参数异常：传入非法collection key=[{target_key}]，"
            f"自动回退默认key=[{_DEFAULT_COLLECTION}]"
        )
        item = COLLECTION_REGISTRY[_DEFAULT_COLLECTION]
    else:
        item = COLLECTION_REGISTRY[target_key]

    real_collection = item["collection_name"]


    if info.book_id:
        # 用户/LLM 明确指定了书籍，直接精确检索
        results = _do_search(info, real_collection, info.book_id)

    else:
        # 第一阶段：不限书籍粗召回，让内容投票
        raw = search_Milvus(
            query=info.query,
            collection_name=real_collection,
            milvus_client=milvus_client,
            embed_model=embed_model,
            reranker=None,
            top_k_retrieve=100,
            top_k_rerank=100,
        )

        book_counter: Dict[str, int] = {}
        book_names: Dict[str, str] = {}
        for r in raw:
            bid = r.metadata.get("book_id", "unknown")
            if bid != "unknown":
                book_counter[bid] = book_counter.get(bid, 0) + 1
                book_names[bid] = r.metadata.get("book_name", bid)

        if book_counter:
            best_id = max(book_counter, key=book_counter.get)
            logging.info("🔍 自动识别书籍: %s（命中 %d/%d 条）",
                         book_names.get(best_id, best_id), book_counter[best_id], len(raw))
            results = _do_search(info, real_collection, best_id)
        else:
            results = raw

    # 检索质量门控（Spec：RAG_MIN_COSINE_SIM，默认关闭；全滤返回 [] 触发上层空检索兜底）
    return _apply_min_cosine_sim(results)

# ================= 文件入库工具 =================

@mcp.tool()
def RAG_init_collection(
    collection_name: str = Field(description=_COLLECTION_DESC),
    file_paths: List[str] = Field(description="文本文件路径列表"),
    book_name: str = Field(description="文档/书目标题（如 售后政策）"),
    book_id: Optional[str] = Field(
        default=None,
        description="文档书ID（Spec-C 多用户隔离必须传 doc_{user_id}_{doc_id}；缺省雪花生成）",
    ),
) -> Tuple[int, int, Dict[str, int]]:
    """
    初始化集合（文档入库；Spec-C：支持显式 book_id 追加）
    :param collection_name: 逻辑集合名（自动映射到真实 Milvus 集合）
    :param file_paths: 文本文件路径列表
    :param book_name: 文档/书目标题
    :param book_id: 文档书ID（缺省由 init_Milvus_Collection 生成/按 book_name 复用）
    :return: (插入文档数量, 索引数量, {file_name: 逐文件 chunk 数})——逐文件计数供回填，规避可见性竞争。
        顶层计划外《入库去重按书隔离与重复上传治理》：per_file 覆盖全部输入文件，
        本次全被去重的文件值为 0（调用方可据此区分「0=已去重」与「缺失=未上报」）。
    """
    _DEFAULT_COLLECTION = next(iter(COLLECTION_REGISTRY))  # "content"

    target_key = collection_name
    if target_key not in COLLECTION_REGISTRY:
        target_key = _DEFAULT_COLLECTION

    real_collection = COLLECTION_REGISTRY[target_key]["collection_name"]

    return init_Milvus_Collection(
        collection_name = real_collection,
        embed_model = embed_model,
        milvus_client = milvus_client,
        file_paths = file_paths,
        book_name = book_name,
        book_id = book_id,
    )


@mcp.tool()
def RAG_delete_by_book_id(
    collection_name: str = Field(description=_COLLECTION_DESC),
    book_id: str = Field(description="要删除的文档书ID"),
    file_name: Optional[str] = Field(
        default=None,
        description="可选：指定文件则只删该书该文件的向量（单文件删除，顶层计划外）",
    ),
) -> int:
    """按 book_id 删除向量（Spec-C：幂等，无匹配也成功；可选 file_name 限单文件）。"""
    target_key = collection_name
    if target_key not in COLLECTION_REGISTRY:
        target_key = next(iter(COLLECTION_REGISTRY))
    real_collection = COLLECTION_REGISTRY[target_key]["collection_name"]
    return delete_by_book_id(real_collection, book_id, milvus_client, file_name=file_name)

# =============天气查询工具函数===============================

# Pydantic 模型：定义“天气查询工具参数接口”，字段 description 会进入工具参数 schema
class WeatherByCityInfo(BaseModel):
    """定义天气查询所需的参数结构"""
    city: str = Field(description="城市名称")

async def lookup_city(location: str, API_KEY: str, BASE_URL: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/geo/v2/city/lookup",
            params={"location": location, "key": API_KEY}
        )
        return resp.json()["location"][0]["id"]

async def get_weather_real(location_id: str, API_KEY: str, BASE_URL: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/v7/weather/now",
            params={"location": location_id, "key": API_KEY}
        )
        return json.dumps(resp.json()["now"],ensure_ascii=False)

# 工具函数标记async
@mcp.tool()
async def get_weather(info: WeatherByCityInfo):
    """查询指定城市的天气"""
    city_id = await lookup_city(info.city, WEATHER_API_KEY, WEATHER_API_URL)
    weather = await get_weather_real(city_id, WEATHER_API_KEY, WEATHER_API_URL)
    return weather

# ================其它函数===============================



# 主函数

if __name__ == "__main__":

    # ========== 0. 配置日志 ==========
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # ========== 1. 初始化 Milvus 客户端 ==========
    milvus_client = create_Milvus_client()

    # ========== 2. 加载 Embedding 模型 ==========
    embed_model = create_embedding_model()
    
    # ========== 3. 初始化 Rerank 模型 ==========
    reranker_model = create_reranker_model()    
    
    # host、port 在 run() 时传入，不是构造函数。
    # 这里启动后，mcp.json 中的 ServerSSE 服务就可以按约定地址连到它。
    mcp.run(transport="stdio")