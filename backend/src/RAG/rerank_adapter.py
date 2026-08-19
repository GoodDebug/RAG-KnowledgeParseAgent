from contextlib import nullcontext
import logging
import numpy as np
import torch

from typing import List, Optional
from sentence_transformers import CrossEncoder



def guard_unloaded(func):
    """装饰器：校验实例是否已卸载，提前抛出清晰异常"""
    def wrapper(self, *args, **kwargs):
        if self._unloaded:
            raise RuntimeError(
                f"当前RerankerAdapter实例已调用unload()进入僵尸态，不可再执行{func.__name__}，请新建实例"
            )
        return func(self, *args, **kwargs)
    return wrapper




class BaseRerankAdapter:
    """基础 Rerank 适配器类"""
    
    def __init__(self):
        # 新增状态守卫
        self._unloaded = False
        
    def __enter__(self):
        """进入with语句时返回自身实例"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """with代码块结束自动执行unload释放显存"""
        self.unload()

    @guard_unloaded
    def rerank(self, query: str, docs: List[str]) -> List[float]:
        raise NotImplementedError
    
    @guard_unloaded
    def unload(self):
        """安全释放模型与GPU显存，支持重复调用幂等防护"""
        if self._unloaded:
            logging.warning("RerankAdapter 已执行unload，无需重复释放")
            return

        if hasattr(self, "rerank_model"):
            del self.rerank_model

        if self.device == "cuda":
            torch.cuda.empty_cache()

        self._unloaded = True
        logging.info("RerankAdapter 显存释放完成，实例进入不可用僵尸态")

    
class LoadRerankAdapter(BaseRerankAdapter):
    """
    本地 Rerank 重排序适配器
    默认模型：BAAI/bge-reranker-v2-m3
    适配：CUDA/CPU、FP16半精度、8bit权重量化、显存自动释放
    :param load_in_8bit: 开启8bit量化，4G显存显卡推荐开启
    :param use_fp16: CUDA下启用半精度推理，降低显存占用、提速
    :param truncate: 超长输入自动截断，避免模型输入超限报错
    :param show_progress_bar: 是否打印推理进度条
    """    
    def __init__(
        self, 
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: Optional[str] = None,
        load_in_8bit: bool = False,
        use_fp16: bool = True,
        truncate: bool = True,
        show_progress_bar: bool = False    
    ):
        super().__init__()
        
        self.model_name = model_name
        self.load_in_8bit = load_in_8bit
        self.use_fp16 = use_fp16
        self.truncate = truncate
        self.show_progress_bar = show_progress_bar
        
        # 自动设备识别，优先WSL CUDA
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        
        
        # 加载重排模型，捕获加载异常
        try:
            if self.load_in_8bit and self.device == "cuda":
                # 8bit量化使用device_map自动分配，禁止手动指定device
                self.rerank_model = CrossEncoder(
                    self.model_name,
                    model_kwargs={"load_in_8bit": True, "device_map": "auto"},
                )
            else:
                # 普通精度加载，指定运行设备
                self.rerank_model = CrossEncoder(self.model_name, device=self.device)
            logging.info(f"Rerank模型 {self.model_name} 加载完成，运行设备：{self.device}")
        except Exception as e:
            logging.error(f"Rerank模型加载失败：{str(e)}", exc_info=True)
            raise


    def rerank(self, query: str, docs: List[str]) -> List[float]:
        if not docs:
            logging.warning("Rerank 输入文档列表为空，直接返回空列表")
            return []

        try:        
            # 构造 query-doc 配对
            pairs = [[query, doc] for doc in docs]
            
            # 统一空上下文逻辑（复用Embedding代码）
            autocast_ctx = (
                torch.autocast("cuda", dtype=torch.float16)
                if (self.device == "cuda" and self.use_fp16)
                else nullcontext()
            )

            with torch.no_grad(), autocast_ctx:
                scores = self.rerank_model.predict(
                    pairs,
                    truncate=self.truncate,
                    show_progress_bar=self.show_progress_bar
                )
            
            # 统一输出为标准浮点列表，兼容numpy各类数值
            if isinstance(scores, (float, np.floating)):
                return [float(scores)]
            if isinstance(scores, np.ndarray):
                return [float(s) for s in scores]
            return list(scores)
    
        # ----- 异常处理：根据错误类型打不同日志，方便排查 -----
        # try 里面的代码一旦报错，会跳到下面某个 except；若都不匹配，再往上抛。
        except torch.cuda.OutOfMemoryError as e:
            logging.error(f"Rerank推理显存不足：{str(e)}", exc_info=True)
            raise
        except ValueError as e:
            logging.error(f"Rerank参数输入错误：{str(e)}", exc_info=True)
            raise
        except RuntimeError as e:
            logging.error(f"Rerank运行时异常：{str(e)}", exc_info=True)
            raise
        except Exception as e:
            logging.error(f"Rerank未知推理异常：{str(e)}", exc_info=True)
            raise
    

def create_rerank_adapter(
    interface_format: str,
    **kwargs
) -> BaseRerankAdapter:
    """
    工厂函数：创建对应Rerank适配器实例
    :param interface_format: 适配器类型，仅支持 load（本地模型）
    """
    fmt = interface_format.strip().lower()
    if fmt == "load":
        return LoadRerankAdapter(**kwargs)
    else:
        raise ValueError(f"不支持的Rerank适配器类型：{interface_format}")

"""

# 示例用法：

# 1. 常规手动创建
reranker = create_rerank_adapter("load", "BAAI/bge-reranker-v2-m3", load_in_8bit=True)
scores = reranker.rerank("金丹飞行高度上限", ["文本1", "文本2", "文本3"])
reranker.unload()

# 2. with自动释放显存（推荐小项目短生命周期场景）
with create_rerank_adapter("load", "BAAI/bge-reranker-v2-m3") as rerank:
    res = rerank.rerank("你的查询", ["段落1", "段落2"])
    
"""    