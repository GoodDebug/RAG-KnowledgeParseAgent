# embedding_adapters.py
# -*- coding: utf-8 -*-
from contextlib import nullcontext
import logging
import traceback
from typing import List, Optional
import requests
from langchain_openai import  OpenAIEmbeddings
from sentence_transformers import SentenceTransformer
import torch

DEFAULT_REQUEST_TIMEOUT = (5, 60)

def ensure_openai_base_url_has_v1(url: str) -> str:
    """
    若用户输入的 url 不包含 '/v1'，则在末尾追加 '/v1'。
    """
    import re
    url = url.strip()
    if not url:
        return url
    if not re.search(r'/v\d+$', url):
        if '/v1' not in url:
            url = url.rstrip('/') + '/v1'
    return url

def guard_unloaded(func):
    """装饰器：校验实例是否已卸载，提前抛出清晰异常"""
    def wrapper(self, *args, **kwargs):
        if self._unloaded:
            raise RuntimeError(
                f"当前EmbeddingAdapter实例已调用unload()进入僵尸态，不可再执行{func.__name__}，请新建实例"
            )
        return func(self, *args, **kwargs)
    return wrapper

class BaseEmbeddingAdapter:
    """
    Embedding 接口统一基类
    """
    def __init__(self):
        # 新增状态守卫
        self._unloaded: bool = False        
    
    def __enter__(self):
        """进入with语句时返回自身实例"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """with代码块结束自动执行unload释放显存"""
        self.unload()    

    @guard_unloaded
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    @guard_unloaded
    def embed_query(self, query: str) -> List[float]:
        raise NotImplementedError
    
    @guard_unloaded
    def unload(self):
        """销毁model，同时如果调用GPU，则释放本地GPU显存资源，服务重启/销毁时调用"""     
        if self._unloaded:
            logging.warning("EmbeddingAdapter 已执行过unload，无需重复释放")   
            return
        # 安全删除
        if hasattr(self, "model"):
            del self.model
        if self.device == "cuda":
            torch.cuda.empty_cache()
        self._unloaded = True
        logging.info("EmbeddingAdapter 模型显存已释放，实例进入不可用状态")
    

class LoadEmbeddingAdapter(BaseEmbeddingAdapter):
    """
    本地SentenceTransformer嵌入模型适配器，适配BGE系列中文模型
    修复前缀污染、显存管控、类型异常、参数硬编码等全部缺陷
    """
    # ── 分块预算 ↔ embedding 模型耦合（重要约束） ──────────────────────────────
    # 默认 bge-small 系 max_seq_length = 512 tokens；TextSplitter 的分块预算
    # （chunk_size=500 字符，中文约 ~330 token）与之对齐，超预算文本会被
    # SentenceTransformer 静默截断、向量失真。
    # 如需更大分块预算：请切换 max_seq_length 更长的模型（如 BGE-M3，8192 token），
    # 并同步调大 TextSplitter 的 chunk_size —— 二者必须一致，否则检索质量劣化。
    # ─────────────────────────────────────────────────────────────────────────────
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        query_instruction : str = "为这个句子生成表示以用于检索相关文章：",
        auto_add_query_instruction: bool = True, # 新增自动开关
        batch_size: int = 8,
        show_progress_bar: bool = True,
        device: Optional[str] = None,
        normalize_embeddings: bool = True,
        load_in_8bit: bool = False,
        use_fp16: bool = True,
    ):
        super().__init__()
        
        self.model_name = model_name
        self.query_instruction = query_instruction
        self.auto_add_query_instruction = auto_add_query_instruction
        # BGE系列模型白名单关键词，精准匹配
        self._bge_keywords = {"bge-small", "bge-base", "bge-large", "bge-m3", "bge-reranker"}        
        self.batch_size = batch_size
        self.show_progress_bar = show_progress_bar
        self.normalize_embeddings = normalize_embeddings
        self.load_in_8bit = load_in_8bit
        self.use_fp16 = use_fp16
        

        # 自动设备识别，优先WSL CUDA
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        # 量化加载，低配显卡显存优化
        load_kwargs = {}
        if self.load_in_8bit and self.device == "cuda":
            # 8bit量化使用device_map自动分配，禁止手动指定device
            self.model = SentenceTransformer(
                self.model_name,
                model_kwargs={"load_in_8bit": True, "device_map": "auto"},
            )
        else:
            logging.info(f"正在加载模型 {self.model_name} 到设备 {self.device}")
            self.model = SentenceTransformer(
                self.model_name,
                device=self.device,
                **load_kwargs
            )

        
        # 探测向量维度，生成全局零向量占位
        dummy_emb = self.model.encode(["test dim"])
        self.emb_dim = dummy_emb.shape[1]
        # 缓存零向量
        self.zero_vector = [0.0 for _ in range(self.emb_dim)]
        
    def _clean_texts(self, texts: List[str]) -> List[str]:
        """
        兜底过滤空文本、纯空白无效内容
        上层文档加载器已按自然段预处理，此处仅作为异常容错防护
        """
        return list(filter(None, map(str.strip, texts)))    
    
    def _mark_valid_texts(self, texts: List[str]) -> list[tuple[int, str | None]]:
        """
        遍历原始文本，标记每条是否有效，保留原始下标
        返回：[(idx, 清洗文本/None)]，长度和输入完全一致
        """
        result = []
        for idx, raw in enumerate(texts):
            s = raw.strip()
            if s:
                result.append((idx, s))
            else:
                # 空白文本标记为None
                logging.warning(f"第{idx}条文本为纯空白，将填充零向量占位")
                result.append((idx, None))
        return result
    
    def _add_query_prefix(self, texts: List[str]) -> List[str]:
        """批量为查询文本统一添加BGE检索前缀，每条独立处理"""
        return [f"{self.query_instruction}{txt}" for txt in texts]
    
    def _encode_core(self, texts: List[str]) -> List[List[float]]:
        """底层统一编码核心，无业务预处理逻辑"""
        if not texts:
            return []
        try:
            emb = []
            # 自动上下文管理，根据设备和是否使用 fp16 选择
            autocast_ctx = (
                torch.autocast("cuda", dtype=torch.float16) 
                if (self.device == "cuda" and self.use_fp16) 
                else nullcontext()
            )
            # 禁用梯度计算，避免显存占用
            with torch.no_grad(), autocast_ctx:
                emb  =  self.model.encode(
                    inputs=texts,
                    batch_size=self.batch_size,
                    normalize_embeddings=self.normalize_embeddings,
                    show_progress_bar=self.show_progress_bar,
                    device=self.device
                )
            return emb.tolist()

        # ----- 异常处理：根据错误类型打不同日志，方便排查 -----
        # try 里面的代码一旦报错，会跳到下面某个 except；若都不匹配，再往上抛。
        except torch.cuda.OutOfMemoryError as e:
            logging.error(f"embeddings显存不足：{str(e)}")
            raise
        except ValueError as e:
            logging.error(f"embeddings参数错误：{str(e)}")
            raise
        except RuntimeError as e:
            logging.error(f"embeddings运行时错误：{str(e)}")
            raise
        except Exception as e:
            # 其他没预料到的错误都归到这里，避免程序静默崩溃
            logging.error(f"embeddings未知错误：{str(e)}")                 
            raise

    
    def embed_query(self, query: str) -> List[float]:
        """
        单条检索向量，严格遵循标准接口仅接收字符串
        :param query: 用户检索问句
        :return: 单层浮点向量
        """
        # 清洗+批量统一加前缀
        raw_text = self._clean_texts([query])
        if not raw_text:
            return []
        
        # 开启自动前缀 + 模型属于BGE系列，才追加指令
        if self.auto_add_query_instruction:
            model_lower = self.model_name.lower()
            is_bge_model = any(kw in model_lower for kw in self._bge_keywords)
            if is_bge_model:
                raw_text = self._add_query_prefix(raw_text)
        
        vec_list = self._encode_core(raw_text)
        return vec_list[0] if vec_list else self.zero_vector

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量文档向量化，不追加任何检索前缀
        :param texts: 文本列表
        :return: 二维向量列表
        
        核心痛点：
        1.输入输出长度对齐：上层调用方拿到向量列表，第 i 个向量永远对应输入第 i 段文本，不会出现文本、向量错位，Milvus 入库、MVCC 版本、RAG 检索不会匹配错乱；
        2.性能不浪费：空白文本不参与 GPU 批量编码，节省显存与推理耗时；
        3.容错友好：不用抛异常中断整个批量任务，空白文本自动填充无意义零向量，检索时零向量余弦相似度极低，不会干扰正常匹配；
        4.无侵入上层：外部调用 embed_documents 完全不用感知内部空白过滤逻辑，接口行为稳定统一。
        
        """
        # 统一转为列表
        if isinstance(texts, str):
            texts = [texts]
            

        # 标记每条文本有效性，保留原始下标
        mark_list = self._mark_valid_texts(texts)
        # 提取有效文本用于批量编码
        valid_only = [txt for _, txt in mark_list if txt is not None]
        # 初始化全零向量占位结果
        output_vecs = [self.zero_vector for _ in mark_list]
        
        if valid_only:
            batch_vecs = self._encode_core(valid_only)
            vec_iter = iter(batch_vecs)
            # 回填有效向量到对应原始下标
            for idx, txt in mark_list:
                if txt is not None:
                    output_vecs[idx] = next(vec_iter)
        
        
        return output_vecs


        
    


class OpenAIEmbeddingAdapter(BaseEmbeddingAdapter):
    """
    基于 OpenAIEmbeddings（或兼容接口）的适配器
    """
    def __init__(self, model_name: str, base_url: str, api_key: str):
        self._embedding = OpenAIEmbeddings(
            openai_api_key=api_key,
            openai_api_base=ensure_openai_base_url_has_v1(base_url),
            model=model_name
        )
        self._unloaded = False

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embedding.embed_documents(texts)

    def embed_query(self, query: str) -> List[float]:
        return self._embedding.embed_query(query)



class OllamaEmbeddingAdapter(BaseEmbeddingAdapter):
    """
    其接口路径为 /api/embeddings
    """
    def __init__(self, model_name: str, base_url: str):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self._unloaded = False

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings = []
        for text in texts:
            vec = self._embed_single(text)
            embeddings.append(vec)
        return embeddings

    def embed_query(self, query: str) -> List[float]:
        return self._embed_single(query)

    def _embed_single(self, text: str) -> List[float]:
        """
        调用 Ollama 本地服务 /api/embeddings 接口，获取文本 embedding
        """
        url = self.base_url.rstrip("/")
        if "/api/embeddings" not in url:
            if "/api" in url:
                url = f"{url}/embeddings"
            else:
                if "/v1" in url:
                    url = url[:url.index("/v1")]
                url = f"{url}/api/embeddings"

        data = {
            "model": self.model_name,
            "prompt": text
        }
        try:
            response = requests.post(url, json=data, timeout=DEFAULT_REQUEST_TIMEOUT)
            response.raise_for_status()
            result = response.json()
            if "embedding" not in result:
                raise ValueError("No 'embedding' field in Ollama response.")
            return result["embedding"]
        except requests.exceptions.RequestException as e:
            logging.error(f"Ollama embeddings request error: {e}\n{traceback.format_exc()}")
            return []




def create_embedding_adapter(
    interface_format: str,
    model_name: str,
    api_key: str = None,
    base_url: str = None,
    **kwargs
) -> BaseEmbeddingAdapter:
    """
    工厂函数：根据 interface_format 返回不同的 embedding 适配器实例
    """
    fmt = interface_format.strip().lower()
    if fmt == "load":
        return LoadEmbeddingAdapter(model_name,**kwargs)
    elif fmt == "openai":
        return OpenAIEmbeddingAdapter(model_name, base_url, api_key)
    elif fmt == "ollama":
        return OllamaEmbeddingAdapter(model_name, base_url)
    else:
        raise ValueError(f"Unknown embedding interface_format: {interface_format}")


"""

# 示例用法：

# 1. 常规手动创建
embedding = create_embedding_adapter(
    interface_format = "load", 
    model_name = "BAAI/bge-small-zh-v1.5", 
)
scores = embedding.embed_documents(["文本1", "文本2", "文本3"])
embedding.unload()

# 2. with自动释放显存（推荐小项目短生命周期场景）
with create_embedding_adapter(
    interface_format = "load", 
    model_name = "BAAI/bge-small-zh-v1.5"
) as embedding:
    res = embedding.embed_query("你的查询")
    
"""    