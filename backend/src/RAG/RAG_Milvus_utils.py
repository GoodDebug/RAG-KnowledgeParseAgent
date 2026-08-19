import functools
import logging
import os
import time
import hashlib
from collections import Counter

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from langchain_core.documents import Document
from pymilvus import  DataType, MilvusClient, MilvusException

from RAG.embedding_adapters import BaseEmbeddingAdapter
from RAG.DocumentLoader import  DocumentLoadConfig
from RAG.load_utils import load_and_split_novels
from RAG.rerank_adapter import BaseRerankAdapter
from UTILS.WSL_utils import add_milvus_host_to_no_proxy, get_wsl_windows_host_ip
from UTILS.snowflake import snowflake






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


# ====================== 配置区（修改结构/参数只改这里） ======================


# 初始化日志系统（必须在创建logger前配置）
logger = logging.getLogger(__name__)

# ====================== 自定义异常体系 ======================

class MilvusDataError(Exception):
    """Milvus 数据写入/校验失败"""

class DocumentLoadError(Exception):
    """文档加载失败"""

class EmbeddingError(Exception):
    """向量生成失败"""

# ====================== 检索结果数据类 ======================

@dataclass
class SearchResult:
    """
    单条检索结果，携带向量得分与可选的重新排序得分。

    :param content:      文档正文
    :param chunk_id:     主键标识
    :param vector_score: 向量检索原始距离（越小越相似，或越大越相似取决于 metric_type）
    :param rerank_score: 重排序得分（越大越相关），None 表示未执行重排序
    :param metadata:     额外字段（file_name、chapter_title 等）
    """
    content: str
    chunk_id: str = ""
    vector_score: float = 0.0
    rerank_score: Optional[float] = None
    metadata: Dict = field(default_factory=dict)



# ====================== 重试工具 ======================

def retry_on_failure(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (MilvusException,),
):
    """指数退避重试装饰器，用于 Milvus 等网络操作"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "%s 第 %d/%d 次失败：%s，%.1fs 后重试",
                        func.__name__, attempt, max_attempts, e, delay,
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator




# ====================== 集合 Schema/索引管理 ======================


def is_schema_matched(
    client: MilvusClient,
    collection_name: str,
    expected_schema: List[Dict],
    coll_desc: str,
    auto_id: bool = False,
    enable_dynamic_field: bool = False,
    strict_desc: bool = True
) -> tuple[bool, Optional[str]]:
    """
    校验现有集合的schema是否与预期完全一致
    :return: (是否匹配, 失败原因字符串)
    """
    try:
        desc = client.describe_collection(collection_name)
    except MilvusException as e:
        return False, f"集合查询异常: {str(e)}"
    
    logger.info(f"集合完整描述：{desc}")
    
    # 0. 集合顶层描述校验
    if desc.get("description", "") != coll_desc:
        return False, f"集合描述不匹配，预期：{coll_desc}，实际：{desc.get('description', '')}"
    if desc.get("auto_id", False) != auto_id:
        return False, f"auto_id配置不匹配，预期：{auto_id}，实际：{desc.get('auto_id')}"
    if desc.get("enable_dynamic_field", False) != enable_dynamic_field:
        return False, f"动态字段配置不匹配，预期：{enable_dynamic_field}"    

    existing_fields = desc["fields"]
    # 1. 字段总数校验
    if len(existing_fields) != len(expected_schema):
        return False, f"字段数量不匹配，预期{len(expected_schema)}个，实际{len(existing_fields)}个"
    
    expected_map = {f["name"]: f for f in expected_schema}
    actual_map = {f["name"]: f for f in existing_fields}
   
    # 2. 双向校验：预期字段必须全部存在，实际字段不能多于预期
    # 2.1 正向校验：预期字段必须全部存在且属性一致
    for exp_name, exp_field in expected_map.items():
        if exp_name not in actual_map:
            return False, f"缺失预期字段: {exp_name}"
        real_field = actual_map[exp_name]

        # 1. # 数据类型对比（使用底层数值，规避枚举对象不等）
        if real_field["type"].value != exp_field["datatype"].value:
            return False, f"字段{exp_name}类型不匹配"
        
        # 2. 主键修正key
        real_pk  = real_field.get("is_primary", False)
        exp_pk = exp_field.get("is_primary", False)
        if real_pk != exp_pk:
            return False, f"字段{exp_name}主键标记不一致"

        # 3. VARCHAR长度（顶层字段，不在params）
        if exp_field["datatype"] == DataType.VARCHAR:
            real_max_len = real_field.get("params", {}).get("max_length")
            if real_max_len != exp_field["max_length"]:
                return False, f"字段{exp_name}字符串长度不一致"
            
        # 4. 向量维度：库返回dimension，预期是dim，做映射对比
        if exp_field["datatype"] == DataType.FLOAT_VECTOR:
            real_dim = real_field.get("params", {}).get("dim")
            if real_dim != exp_field["dim"]:
                return False, f"向量字段{exp_name}维度不一致，预期{exp_field['dim']}"
            
        # 5. 描述严格校验开关
        if strict_desc:
            real_desc = real_field.get("description", "")
            exp_desc = exp_field.get("description", "")
            if real_desc != exp_desc:
                return False, f"字段{exp_name}描述不一致"         
               
    # 2.2 反向校验：实际不存在多余字段
    for real_name in actual_map.keys():
        if real_name not in expected_map:
            return False, f"集合存在多余字段: {real_name}"
        
    return True, "schema完全匹配"

    
def get_index_detail(
    client: MilvusClient,
    collection_name: str,
    field_name: str
) -> Optional[Dict]:
    """获取指定字段的索引详情，无索引则返回None"""
    try:
        index_names = client.list_indexes(collection_name)
        for idx_name in index_names:
            idx_info = client.describe_index(collection_name, index_name=idx_name)
            if idx_info.get("field_name") == field_name:
                return idx_info
        return None
    except MilvusException as e:
        if "collection not exist" in str(e).lower():
            return None
        raise
    
def is_index_matched(client: MilvusClient, collection_name: str, index_cfg: Dict) -> Tuple[bool, str]:
    """
    校验指定向量字段的字段索引是否存在且参数完全匹配
    :return: (是否匹配, 提示信息)
    """
    target_field = index_cfg["field_name"]
    idx_meta = get_index_detail(client, collection_name, target_field)
    
    logger.info(f"索引详情：{idx_meta}")
    
    if not idx_meta:
        return False, f"字段 {target_field} 无索引"    
    
    # 1. 校验索引类型
    if idx_meta["index_type"] != index_cfg["index_type"]:
        return False, f"索引类型不匹配，预期{index_cfg['index_type']}，实际{idx_meta['index_type']}"
    
    # 2. 校验距离度量方式
    if idx_meta["metric_type"] != index_cfg["metric_type"]:
        return False, f"距离度量不匹配，预期{index_cfg['metric_type']}，实际{idx_meta['metric_type']}"
    
    # 3. 校验索引超参：逐个键对比，同时处理类型对齐（返回值多为字符串）
    expected_params = index_cfg.get("params", {})
    for key, exp_val in expected_params.items():
        if key not in idx_meta:
            return False, f"索引缺少超参：{key}"
        real_val = idx_meta[key]
        # 类型对齐：预期是数字就转成同类型对比，避免字符串/整数不等
        if isinstance(exp_val, (int, float)):
            try:
                real_val = type(exp_val)(real_val)
            except (ValueError, TypeError):
                pass
        if real_val != exp_val:
            return False, f"索引超参 {key} 不匹配，预期{exp_val}，实际{real_val}"
    
    return True, "索引参数完全匹配"


def ensure_collection_ready(
    client: MilvusClient,
    collection_name: str,
    force_rebuild: bool,
    coll_desc: str,
    expected_schema: List[Dict],
    index_config: Dict,
    auto_id: bool = False,
    enable_dynamic_field: bool = False    
) -> None:
    """
    Milvus 集合初始化自愈逻辑：校验Schema、自动重建、修复索引
    :param client: Milvus 客户端实例
    :param collection_name: 目标集合名
    :param force_rebuild: 是否强制全量重建
    :param coll_desc: 集合顶层描述
    :param expected_schema: 预期字段结构配置
    :param index_config: 向量索引配置
    :param auto_id: 是否开启自动ID生成
    :param enable_dynamic_field: 是否开启动态字段添加
    """
    logger.info(f"\n正在检查集合 [{collection_name}] 状态...")
    collection_exists = client.has_collection(collection_name)
    need_rebuild = force_rebuild

    # 集合已存在且不强制重建：校验Schema一致性
    if collection_exists and not force_rebuild:
        schema_ok, match_msg = is_schema_matched(
            client=client,
            collection_name=collection_name,
            expected_schema=expected_schema,
            coll_desc = coll_desc,
            auto_id=auto_id,
            enable_dynamic_field=enable_dynamic_field,
            strict_desc=False
        )
        if not schema_ok:
            logger.warning("集合Schema校验失败，原因：%s，即将执行重建", match_msg)
            need_rebuild = True
        else:
            logger.info("集合Schema校验通过，无需重建：%s", match_msg)
    elif force_rebuild:
        logger.info("已开启FORCE_REBUILD强制重建开关，直接执行重建")
        need_rebuild = True
    else:
        logger.info("集合不存在，需要新建集合")
        need_rebuild = True

    if need_rebuild:
        try:
            if collection_exists:
                logger.info(f"正在删除旧集合 [{collection_name}]...")
                # drop_collection会自动释放内存，无需前置release，避免未加载时报错
                client.drop_collection(collection_name)
                logger.info("旧集合已删除")

            logger.info("正在创建新集合与向量索引...")
            schema = client.create_schema(
                auto_id=auto_id, 
                enable_dynamic_field=enable_dynamic_field,
                description=coll_desc
            )
            for field_conf in expected_schema:
                field_params = {}
                if field_conf["datatype"] == DataType.VARCHAR:
                    field_params["max_length"] = field_conf["max_length"]
                if field_conf["datatype"] == DataType.FLOAT_VECTOR:
                    field_params["dim"] = field_conf["dim"]

                schema.add_field(
                    field_name=field_conf["name"],
                    datatype=field_conf["datatype"],
                    is_primary=field_conf["is_primary"],
                    description=field_conf.get("description", ""),
                    **field_params
                )

            # 绑定索引参数创建集合
            index_params = client.prepare_index_params()
            index_params.add_index(**index_config)
            client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params
            )
            client.load_collection(collection_name)
            logger.info(f"集合 [{collection_name}] 创建完成，向量索引已绑定并加载")
        except MilvusException as e:
            logger.error("重建集合失败：%s", str(e), exc_info=True)
            raise
    else:
        # Schema正常，仅校验修复索引
        index_ok, index_msg = is_index_matched(client, collection_name, index_config)
        if not index_ok:
            logger.warning("⚠️  检测到向量索引配置不匹配/缺失，正在补建...")
            # 先删除旧索引（若存在），再创建新索引
            old_index = get_index_detail(client, collection_name, index_config["field_name"])
            if old_index:
                client.drop_index(collection_name, index_name=old_index["index_name"])            
            client.create_index(collection_name=collection_name, **index_config)
            client.load_collection(collection_name)
            logger.info("向量索引补建完成")
        else:
            logger.info("向量索引配置校验通过，无需操作")
            # 确保集合已加载
            if not client.get_load_state(collection_name)["state"] == "Loaded":
                client.load_collection(collection_name)
                
                
# ====================== 客户端创建 ======================
                
def create_Milvus_client() -> MilvusClient:
    # ========== 0. 区分环境 + 加载 .env ==========
    env_mode = "docker" if os.path.exists("/.dockerenv") else os.getenv("ENV_MODE", "wsl")
    if env_mode == "wsl":
        load_dotenv(override=False)

    logger.info("========== create_Milvus_client 环境 ==========")
    logger.info(f"PID={os.getpid()}, ENV_MODE={env_mode}")
    logger.info(f"MILVUS_HOST={os.getenv('MILVUS_HOST')}")
    logger.info(f"MILVUS_PORT={os.getenv('MILVUS_PORT')}")
    logger.info(f"AUTO_GET_WIN_HOST_IP={os.getenv('AUTO_GET_WIN_HOST_IP')}")
    logger.info(f"MANUAL_MILVUS_HOST={os.getenv('MANUAL_MILVUS_HOST')}")

    # ========== 1. 实时读取环境变量 ==========
    milvus_host = os.getenv("MILVUS_HOST")
    auto_detect = _env_bool("AUTO_GET_WIN_HOST_IP", "True")
    manual_host = os.getenv("MANUAL_MILVUS_HOST")
    milvus_port = os.getenv("MILVUS_PORT")
    milvus_user = os.getenv("MILVUS_USER") or ""
    milvus_pwd = os.getenv("MILVUS_PASSWORD") or ""

    # 容器模式强制关闭 WSL 自动探测
    if env_mode == "docker":
        auto_detect = False
        if not milvus_host:
            milvus_host = "milvus"  # MCP 子进程不继承 compose 变量，使用服务名默认值
            logger.info("✅ Docker 模式默认使用服务名 milvus")
        if not milvus_port:
            milvus_port = "19530"

    # ========== 2. 解析 Milvus 连接地址 ==========
    if milvus_host:
        logger.info(f"✅ 使用环境变量 MILVUS_HOST={milvus_host}")
    elif auto_detect:
        try:
            milvus_host = get_wsl_windows_host_ip()
            logger.info(f"✅ 自动获取Windows宿主机IP：{milvus_host}")
        except RuntimeError as e:
            logger.warning(f"⚠️  自动获取宿主机IP失败：{e}，回退到手动配置地址")
            milvus_host = manual_host
    else:
        milvus_host = manual_host

    milvus_uri = f"http://{milvus_host}:{milvus_port}"
    logger.info(f"最终 milvus_host={milvus_host}")
    logger.info(f"最终 milvus_uri={milvus_uri}")

    # ========== 关键：动态将宿主机IP加入代理白名单 ==========
    add_milvus_host_to_no_proxy(milvus_host)

    connect_kwargs = {}
    if milvus_user and milvus_pwd:
        connect_kwargs["token"] = f"{milvus_user}:{milvus_pwd}"
        
    # ========== 1. 初始化 Milvus 客户端 ==========
    logger.info(f"正在连接 Milvus 服务：{milvus_uri}")
    try:
        # 如有鉴权可补充：user="root", password="Milvus"
        # 初始化客户端时增加超时参数，超时后直接抛异常而不是卡死
        milvus_client = MilvusClient(
            uri=milvus_uri,
            timeout=10.0,  # 连接+请求总超时 10 秒
            # 双重保险：显式指定不使用代理（HTTP模式下生效）
            proxies={"http": None, "https": None},
            **connect_kwargs
        )
        milvus_client.list_collections()  # 探活，触发真实连接
        logger.info("✅ Milvus 连接成功")
        return milvus_client
    
    except MilvusException as e:
        logger.error(f"❌ Milvus 连接失败：{e}")
        logger.error("请检查：1. Milvus 服务是否启动 2. 地址端口是否正确 3. 防火墙是否放行19530端口 ")
        raise e
    except Exception as e:
        logger.error(f"❌ 未知连接错误：{e}")
        raise e
    
    
# ====================== 检索与重排序 ======================

def _hit_to_result(
    hit: Dict,
    output_fields: Tuple[str, ...],
) -> SearchResult:
    """
    将 Milvus search 返回的单条 hit 转为 SearchResult。
    hit 结构: {"id": ..., "distance": ..., "entity": {"content": ..., ...}}
    """
    entity = hit.get("entity") or hit
    return SearchResult(
        content=entity.get("content", ""),
        chunk_id=entity.get("chunk_id", ""),
        vector_score=hit.get("distance", 0.0),
        metadata={
            f: entity.get(f, "")
            for f in output_fields
            if f not in ("content", "chunk_id")
        },
    )


def search_Milvus(
    query: str,
    collection_name: str,
    embed_model: BaseEmbeddingAdapter,
    milvus_client: MilvusClient,
    *,
    book_id: Optional[str] = None,
    reranker: Optional[BaseRerankAdapter] = None,
    top_k_retrieve: int = 20,
    top_k_rerank: int = 5,
    search_params: Optional[Dict] = None,
    output_fields: Optional[List[str]] = None,
) -> List[SearchResult]:
    """
    两阶段 RAG 检索：向量召回 → 可选重排序。

    **第一阶段（向量检索）**：
      Bi-Encoder 将 query 编码为向量，在 Milvus 中执行 ANN 搜索，
      召回 top_k_retrieve 条候选结果。

    **第二阶段（重排序，条件执行）**：
      若传入 reranker 实例，则将 (query, doc_text) 配对送入
      Cross-Encoder 重新打分，按 rerank_score 降序取 top_k_rerank。
      若无 reranker，则直接按 vector_score 排序截取。

    :param query:           查询文本
    :param collection_name: Milvus 集合名
    :param embed_model:     Embedding 模型（Bi-Encoder）
    :param milvus_client:   Milvus 客户端实例
    :param reranker:        Cross-Encoder 重排序适配器（None = 跳过重排序）
    :param top_k_retrieve:  向量检索召回条数（默认 20，重排序候选需要足够多）
    :param top_k_rerank:    最终返回条数（默认 5）
    :param search_params:   Milvus 搜索参数，默认 {"metric_type":"COSINE","params":{"nprobe":10}}
    :param output_fields:   返回字段列表，默认 ["content", "chunk_id"]
    :return:                按相关性降序排列的 SearchResult 列表
    :raises MilvusException: 向量检索失败
    """
    # ========== 参数默认值 ==========
    if search_params is None:
        search_params = {"metric_type": "COSINE", "params": {"nprobe": 10}}
    if output_fields is None:
        # 顶层计划外《引用卡片-展示book名与文档名》：默认带回引用字段，供 _extract_source_refs 使用
        output_fields = ["content", "chunk_id", "file_name", "book_name", "chapter_title"]
    out_fields_tuple = tuple(output_fields)

    # ========== 第一阶段：向量检索 ==========
    logger.info("检索阶段：query=%s | top_k_retrieve=%d | params=%s",
                query, top_k_retrieve, search_params)

    query_vec = embed_model.embed_query(query)
    logger.info("查询向量维度：%d", len(query_vec))

    try:
        # 如果指定了 book_id，添加过滤条件隔离其他小说
        filter_expr = f'book_id == "{book_id}"' if book_id else None
        raw_results = milvus_client.search(
            collection_name=collection_name,
            data=[query_vec],
            anns_field="embedding",
            search_params=search_params,
            limit=top_k_retrieve,
            output_fields=output_fields,
            filter=filter_expr,
        )
    except MilvusException as e:
        logger.error("向量检索失败：%s", e)
        raise

    # hits 来自第一个 query（此接口仅单 query 查询）
    hits = raw_results[0] if raw_results else []
    candidates = [_hit_to_result(h, out_fields_tuple) for h in hits]
    logger.info("向量召回 %d 条", len(candidates))

    if not candidates:
        return []

    # ========== 第二阶段：重排序（条件） ==========
    if reranker is not None:
        logger.info("重排序阶段：使用 %s", getattr(reranker, "model_name", "未知模型"))
        texts = [r.content for r in candidates]

        try:
            scores = reranker.rerank(query, texts)
        except Exception as e:
            logger.error("重排序失败，回退至向量排序：%s", e)
            scores = None

        if scores and len(scores) == len(candidates):
            for r, s in zip(candidates, scores):
                r.rerank_score = float(s)

            # 按 rerank_score 降序
            candidates.sort(key=lambda r: r.rerank_score or 0.0, reverse=True)
            logger.info("重排序完成，前 %d 条：%s",
                        min(top_k_rerank, len(candidates)),
                        [f"{r.chunk_id}={r.rerank_score:.4f}" for r in candidates[:top_k_rerank]])
        # scores 为空或长度不匹配时，走 vector_score 排序
        else:
            candidates.sort(key=lambda r: r.vector_score, reverse=True)
    else:
        # 无重排序：向量得分直接取 top_k_rerank
        # COSINE 距离越小越相似，取最小值；但用户可能用内积（越大越相似），
        # 这里保持 Milvus 返回顺序（已按 distance 升序排列），取前 top_k_rerank
        logger.info("跳过重排序，按向量得分取 top-%d", top_k_rerank)

    # 截取最终返回条数
    results = candidates[:top_k_rerank]
    logger.info("最终返回 %d 条结果", len(results))
    return results
        
        
# ====================== 默认 metadata 映射器 ======================

def default_metadata_mapper(doc: Document) -> Dict:
    """
    默认的 Document → Milvus record 字段映射。
    将 LangChain Document 转换为 Milvus upsert 所需的字典结构。
    可替换为自定义 mapper 以适应不同数据源。
    """
    file_name = doc.metadata.get("file_name", "unknown")
    # 获取文件名前几个字符作为前缀
    file_front = os.path.splitext(file_name)[0][:10]
    file_hash = hashlib.md5(file_name.encode()).hexdigest()
    return {
        "chunk_id": (
            f"{file_front}"
            f"_ch{doc.metadata.get('chapter_index', 0)}"
            f"_{doc.metadata['chunk_index']:04d}"
            f"{file_hash}"
        ),
        "content": doc.page_content,
        "content_hash": hashlib.md5(doc.page_content.encode("utf-8")).hexdigest(),
        "file_name": doc.metadata.get("file_name", ""),
        "book_id": doc.metadata.get("book_id", "unknown"),
        "book_name": doc.metadata.get("book_name", "未知书籍"),
        "chapter_title": doc.metadata.get("chapter_title", "无章节"),
        "chapter_index": doc.metadata.get("chapter_index", 0),
        "chunk_index": doc.metadata.get("chunk_index", 0),
        "chunk_size": doc.metadata.get("chunk_size", len(doc.page_content)),
        "file_type": doc.metadata.get("file_type", "."),
        "uploaded_at": int(time.time() * 1000),   # chunk 入库时间戳（Spec-C）
    }


# ====================== 书籍标识解析 ======================

def resolve_book_id(
    book_name: Optional[str],
    milvus_client: MilvusClient,
    collection_name: str,
) -> str:
    """
    根据 book_name 查找已存在的 book_id，未找到则生成新雪花 ID。
    用于支持多次上传同一文章的不同部分时复用同一 book_id。
    """
    if not book_name:
        return str(snowflake.generate())

    try:
        rows = milvus_client.query(
            collection_name=collection_name,
            output_fields=["book_id"],
            filter=f'book_name == "{book_name}"',
            limit=1,
        )
        if rows:
            bid = rows[0].get("book_id")
            logger.info("复用已存在的 book_id=%s（book_name=%s）", bid, book_name)
            return bid
    except Exception as e:
        logger.warning("查询已存在 book_name 失败，将生成新 ID: %s", e)

    return str(snowflake.generate())


def delete_by_book_id(
    collection_name: str,
    book_id: str,
    milvus_client: MilvusClient,
    file_name: Optional[str] = None,
) -> int:
    """按 book_id 删除向量（顶层计划外：书分组与单文件删除——扩展可选 file_name）。

    - file_name=None → 整书向量（book_id==X）
    - file_name=某文件 → 单文件向量（book_id==X and file_name==Y）
    幂等：无匹配也视为成功（删除 0 条）。
    """
    filter_expr = f'book_id == "{book_id}"'
    if file_name:
        filter_expr += f' and file_name == "{file_name}"'
    result = milvus_client.delete(collection_name=collection_name, filter=filter_expr)
    if isinstance(result, dict):
        return int(result.get("delete_count", 0) or 0)
    return int(result or 0)


# ====================== 入库前去重 ======================

def dedup_pre_insert(
    records: List[Dict],
    milvus_client: MilvusClient,
    collection_name: str,
    *,
    book_id: Optional[str] = None,
    enable_vector_dedup: bool = True,
    similarity_threshold: float = 0.95,
    batch_size: int = 500,
) -> Tuple[List[Dict], int]:
    """
    入库前两阶段去重。

    阶段一 — content_hash 精确去重（按 book_id 隔离，顶层计划外《入库去重按书隔离与重复上传治理》）：
      查 Milvus 中该 book_id 下是否已有相同 content_hash，过滤完全一致的文本；
      跨书相同文本不再被丢弃，允许在各自 book_id 下重新入库。

    阶段二（可选）— 批量向量语义去重：
      将剩余 records 的向量组织为 query_vectors，一次批量检索 Milvus，
      过滤掉与存量数据余弦相似度超过 threshold 的条目。

    :param records: 预备入库的 record 列表（必需含 embedding + content_hash）
    :param book_id: 本次入库的 book_id（doc_{user_id}_{doc_id}）；None 时不按书隔离
    :return: (过滤后的 records, 被过滤总数)
    """
    if not records:
        return records, 0

    dropped = 0

    # ── 阶段一：content_hash 精确去重（按 book_id 隔离） ──
    hash_list = [r.get("content_hash", "") for r in records if r.get("content_hash")]
    if hash_list:
        try:
            # 手动构造 IN 过滤表达式；book_id 形如 doc_{user}_{doc_id}，无需转义
            hash_str = ", ".join(f'"{h}"' for h in hash_list)
            base_expr = f"content_hash in [{hash_str}]"
            filter_expr = f'{base_expr} and book_id == "{book_id}"' if book_id else base_expr
            existing = milvus_client.query(
                collection_name=collection_name,
                output_fields=["content_hash"],
                filter=filter_expr,
                limit=len(hash_list),
            )
            existing_set = {row["content_hash"] for row in existing if row.get("content_hash")}
            phase1_result = [r for r in records if r.get("content_hash", "") not in existing_set]
            dropped += len(records) - len(phase1_result)
        except Exception as e:
            logger.warning("content_hash 精确去重失败，跳过阶段一: %s", e)
            phase1_result = list(records)
    else:
        phase1_result = list(records)

    # ── 阶段二：批量向量语义去重 ──
    if not enable_vector_dedup or not phase1_result:
        return phase1_result, dropped

    vectors = [r["embedding"] for r in phase1_result]
    filtered = []
    for start in range(0, len(vectors), batch_size):
        end = min(start + batch_size, len(vectors))
        batch_vecs = vectors[start:end]
        try:
            results = milvus_client.search(
                collection_name=collection_name,
                data=batch_vecs,
                anns_field="embedding",
                limit=1,
                output_fields=[],
            )
            for i, hits in enumerate(results):
                if hits and (1 - hits[0]["distance"]) > similarity_threshold:
                    dropped += 1
                else:
                    filtered.append(phase1_result[start + i])
        except Exception as e:
            logger.warning("批量向量去重失败，该批次所有记录将保留: %s", e)
            filtered.extend(phase1_result[start:end])

    logger.info("去重完成: 输入 %d 条, 过滤 %d 条, 输出 %d 条",
                len(records), dropped, len(filtered))
    return filtered, dropped


# ====================== 流水线函数：加载 → 向量化 → 写入 ======================

def load_documents(
    file_paths: List[str],
    config: Optional[DocumentLoadConfig] = None,
) -> List[Document]:
    """
    加载并分块文档。

    :param file_paths: 文档文件路径列表
    :param config: 文档加载配置，默认使用 DocumentLoadConfig.from_env()
    :return: 分块后的 Document 列表
    :raises DocumentLoadError: 文档加载或分块失败时
    """
    logger.info("正在加载文档...")
    cfg = config or DocumentLoadConfig.from_env()
    success, documents = load_and_split_novels(file_paths=file_paths, config=cfg)

    if not success:
        raise DocumentLoadError(f"文档加载失败: {file_paths}")

    logger.info("文档加载成功，共 %d 条", len(documents))
    return documents


def prepare_embeddings(
    documents: List[Document],
    embed_model: BaseEmbeddingAdapter,
    *,
    metadata_mapper: Callable[[Document], Dict] = default_metadata_mapper,
    batch_size: int = 64,
) -> Generator[List[Dict], None, None]:
    """
    分批生成 embedding 并组装为 Milvus record。

    逐批 yield 的好处：
    - 调用方可以边生成边写入，无需等待全部 Embedding 完成
    - 大文档集不占满内存

    :param documents: 已分块的 Document 列表
    :param embed_model: Embedding 模型适配器
    :param metadata_mapper: 从 Document 提取 Metadata 字段的回调，可替换
    :param batch_size: 每批处理的文档数
    :yields: 每批组装好的 Milvus record 字典列表
    :raises EmbeddingError: 向量生成失败时
    """
    total = len(documents)
    logger.info("正在分批生成 %d 条向量（batch_size=%d）...", total, batch_size)

    for start in range(0, total, batch_size):
        batch_docs = documents[start : start + batch_size]
        texts = [d.page_content for d in batch_docs]

        try:
            vectors = embed_model.embed_documents(texts)
        except Exception as e:
            raise EmbeddingError(
                f"第 {start}～{start + len(batch_docs)} 条 Embedding 生成失败: {e}"
            ) from e

        batch_records = [
            {**metadata_mapper(doc), "embedding": vec}
            for doc, vec in zip(batch_docs, vectors)
        ]

        logger.info(
            "Embedding 进度: %d/%d (%.0f%%)",
            min(start + batch_size, total), total,
            min((start + batch_size) / total * 100, 100),
        )
        yield batch_records


def upsert_to_milvus(
    client: MilvusClient,
    collection_name: str,
    records_iter: Generator[List[Dict], None, None],
    *,
    batch_size: int = 128,
    auto_flush: bool = False,
) -> Tuple[int, int]:
    """
    分批将 records 写入 Milvus，支持指数退避重试。

    :param client: Milvus 客户端
    :param collection_name: 目标集合名
    :param records_iter: record 批次的生成器（由 prepare_embeddings 产出）
    :param batch_size: 单次 upsert 的数据条数
    :param auto_flush: 是否在每批写入后强制 flush（默认 False 以提升性能）
    :return: (成功写入条数, 失败条数)
    :raises MilvusDataError: 全部批次重试耗尽后仍失败时
    """
    total_success = 0
    total_fail = 0
    batch_records: List[Dict] = []

    def _flush_one_batch(records: List[Dict]) -> int:
        """单次 upsert 内部函数，带重试"""
        if not records:
            return len(records)

        @retry_on_failure(max_attempts=3, exceptions=(MilvusException,))
        def _do_upsert(data: List[Dict]) -> None:
            client.upsert(collection_name=collection_name, data=data)
            if auto_flush:
                client.flush(collection_name)

        _do_upsert(records)
        return len(records)

    for batch in records_iter:
        batch_records.extend(batch)

        # 攒够 batch_size 或已无更多数据时，触发写入
        while len(batch_records) >= batch_size:
            chunk = batch_records[:batch_size]
            batch_records = batch_records[batch_size:]
            try:
                n = _flush_one_batch(chunk)
                total_success += n
                logger.info("写入进度: 成功 %d 条", total_success)
            except Exception as e:
                total_fail += len(chunk)
                logger.error("批次写入失败（%d 条）: %s", len(chunk), e)

    # 处理剩余不足一批的数据
    if batch_records:
        try:
            n = _flush_one_batch(batch_records)
            total_success += n
        except Exception as e:
            total_fail += len(batch_records)
            logger.error("尾批写入失败（%d 条）: %s", len(batch_records), e)

    logger.info(
        "写入完成：成功 %d 条，失败 %d 条", total_success, total_fail
    )
    return total_success, total_fail


# ====================== 编排函数（原 init_Milvus_Collection） ======================

def init_Milvus_Collection(
    collection_name: str,
    embed_model: BaseEmbeddingAdapter,
    milvus_client: MilvusClient,
    file_paths: List[str],
    doc_config: Optional[DocumentLoadConfig] = None,
    *,
    book_id: Optional[str] = None,
    book_name: Optional[str] = None,
    metadata_mapper: Callable[[Document], Dict] = default_metadata_mapper,
    embed_batch_size: int = 64,
    upsert_batch_size: int = 128,
    auto_flush: bool = False,
) -> Tuple[int, int, Dict[str, int]]:
    """
    完整数据入库流水线：加载 → 向量化 → 写入 Milvus。

    此函数为上层便捷入口；若需单独控制各步骤，也可直接调用
    ``load_documents`` / ``prepare_embeddings`` / ``upsert_to_milvus``。

    :param collection_name: Milvus 目标集合名
    :param embed_model: Embedding 模型适配器
    :param milvus_client: Milvus 客户端实例
    :param file_paths: 待入库的文档文件路径列表
    :param doc_config: 文档加载配置，默认使用 DocumentLoadConfig.from_env()
    :param metadata_mapper: 自定义 Document → Milvus record 映射回调
    :param embed_batch_size: 每批 Embedding 的文档数
    :param upsert_batch_size: 每批 upsert 的数据条数
    :param auto_flush: 是否写入后强制 flush
    :return: (成功写入条数, 失败条数)
    :raises DocumentLoadError: 文档加载失败
    :raises EmbeddingError: 向量生成失败
    """
    documents = load_documents(file_paths, doc_config)

    # 确定 book_id：已传入则直接用；否则按 book_name 查库复用或生成新 ID
    final_book_id = book_id or resolve_book_id(book_name, milvus_client, collection_name)

    # 注入书籍标识到每个文档的 metadata 中
    for doc in documents:
        doc.metadata["book_id"] = final_book_id
        if book_name:
            doc.metadata["book_name"] = book_name

    records_iter = prepare_embeddings(
        documents,
        embed_model,
        metadata_mapper=metadata_mapper,
        batch_size=embed_batch_size,
    )

    # 收集全部批次 → 去重 → 重新喂给 upsert
    all_records: List[Dict] = []
    for batch in records_iter:
        all_records.extend(batch)

    deduped_records, dropped = dedup_pre_insert(
        all_records,
        milvus_client,
        collection_name,
        book_id=final_book_id,  # 顶层计划外《入库去重按书隔离》：仅同 book_id 内精确去重
        enable_vector_dedup=False,  # 默认关闭向量去重，精确去重已覆盖 90% 场景
    )
    if dropped:
        logger.info("去重过滤 %d 条重复数据", dropped)

    def _deduped_iter() -> Generator[List[Dict], None, None]:
        yield deduped_records

    success, fail = upsert_to_milvus(
        milvus_client,
        collection_name,
        _deduped_iter(),
        batch_size=upsert_batch_size,
        auto_flush=auto_flush,
    )
    # 顶层计划外《入库 chunk 数回填优化》：返回逐文件 upsert（去重后）计数，
    # 供 documents.py 回填 chunk_count，不再二次查询 Milvus（规避可见性竞争）
    # 顶层计划外《入库去重按书隔离与重复上传治理》：per_file 覆盖全部输入文件，未写入文件显式 0
    #（=本次全被去重），使调用方能区分「0=已去重」与「None=工具未上报」
    per_file = dict.fromkeys((os.path.basename(p) for p in file_paths), 0)
    per_file.update(Counter(r.get("file_name", "") for r in deduped_records))
    return success, fail, per_file