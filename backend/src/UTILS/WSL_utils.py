import os
import logging

from typing import Optional
from dotenv import load_dotenv

load_dotenv()

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

# ====================== 自动化适配WSL2环境 ===========================

def is_wsl_environment() -> bool:
    """判断当前是否运行在WSL环境中"""
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            version_content = f.read().lower()
        return "microsoft" in version_content
    except (FileNotFoundError, PermissionError):
        return False

def get_wsl_windows_host_ip() -> str:
    """自动获取WSL2中Windows宿主机的IP，等效于 grep -m1 nameserver /etc/resolv.conf | awk '{print $2}'"""
    if not is_wsl_environment():
        raise RuntimeError("当前不是WSL环境，无法自动获取宿主机IP")    
    
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return parts[1]
        raise RuntimeError("未在 resolv.conf 中找到 nameserver 配置")
    except FileNotFoundError:
        raise RuntimeError("未找到 /etc/resolv.conf，当前环境可能不是 WSL")
    except Exception as e:
        raise RuntimeError(f"读取宿主机IP失败：{str(e)}")
    
    
    
def add_milvus_host_to_no_proxy(milvus_host: str) -> None:
    """将Milvus主机IP添加到no_proxy环境变量中，避免Milvus连接失败"""

    # ========== 关键：动态将宿主机IP加入代理白名单 ==========
    # 读取当前 no_proxy，兼容大小写两种环境变量
    no_proxy = os.environ.get('no_proxy', os.environ.get('NO_PROXY', ''))
    # 避免重复添加
    if milvus_host not in no_proxy.split(','):
        if no_proxy:
            no_proxy += f',{milvus_host}'
        else:
            no_proxy = milvus_host
        os.environ['no_proxy'] = no_proxy
        os.environ['NO_PROXY'] = no_proxy
        logger.debug(f"已将宿主机IP {milvus_host} 加入代理白名单")    