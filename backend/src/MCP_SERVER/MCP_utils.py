import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from langchain_mcp_adapters.sessions import create_session

# 默认 mcp.json 路径（与本文件同目录）
_MCP_JSON_PATH = Path(__file__).resolve().parent / "mcp.json"

logger = logging.getLogger(__name__)


# ==========================工具函数=========================

# 加载 MCP 服务器配置
async def mcp_load_servers(file_path: str | Path | None = None) -> dict:
    """
    加载 MCP 服务器配置。
    :param file_path: 配置文件路径，默认使用同目录下的 mcp.json
    :return: 完整配置字典，如 {"mcpServers": {"weather": {...}, "fetch": {...}}}

    这里读取的是"客户端如何连接服务"的约定配置，而不是协议本体。
    """
    path = Path(file_path) if file_path else _MCP_JSON_PATH
    if not path.exists():
        logger.warning(f"未找到 mcp 配置文件: {path}")
        return {"mcpServers": {}}
    config_dir = path.resolve().parent
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 解析相对路径：将 args 中以 ./ 开头的路径解析为 mcp.json 所在目录的相对路径
    servers = config.get("mcpServers", {})
    for name, server in servers.items():
        if server.get("transport") == "stdio" and "args" in server:
            resolved_args = []
            for arg in server["args"]:
                if arg.startswith("./") or arg.startswith(".\\"):
                    resolved = config_dir / arg
                    resolved_args.append(str(resolved))
                else:
                    resolved_args.append(arg)
            server["args"] = resolved_args

    logger.info(
        f"已加载 mcp 配置: {path}，共 {len(servers)} 个服务"
    )
    return config


# ========================== 持久会话（方案 A，顶层计划外）==========================

def _parse_call_result(result) -> Any:
    """解析 MCP CallToolResult → 真实 Python 值（与 MCPTool.ainvoke 一致/更准）。

    优先取首个 TextContent.text 做 json.loads 还原（如 '[10,0]' → [10,0]、
    SearchResult 列表 JSON → dict 列表）；非 JSON 回退原文；无 text 用 structuredContent。
    """
    if result is None:
        return None
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    return None


class PersistentMCPSession:
    """持有单个持久 stdio 会话，所有工具调用复用同一 MCP 子进程（方案 A）。

    一个子进程常驻：main() 只跑一次 → Milvus / embedding / reranker 只加载一次。
    并发调用经 asyncio.Lock 串行排队（省模型加载换取排队，体感收益更大）。
    """

    def __init__(self, connection: dict):
        self.connection = connection
        self._session = None          # ClientSession
        self._cm = None               # create_session 的 async CM
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """进入 create_session 一次并保持（子进程启动、模型加载一次）。"""
        if self._session is not None:
            return
        self._cm = create_session(self.connection)
        self._session = await self._cm.__aenter__()
        await self._session.initialize()
        logger.info("✅ 持久 MCP 会话已建立（子进程常驻，模型只加载一次）")

    async def list_tools(self) -> list:
        """从持久会话列工具 schema（替代 get_tools 的按次会话）。"""
        if self._session is None:
            await self.open()
        result = await self._session.list_tools()
        return list(result.tools)

    async def call(self, name: str, args: dict) -> Any:
        """复用持久会话执行工具；异常时重建会话并重试一次。"""
        async with self._lock:
            try:
                return await self._call_once(name, args)
            except Exception as exc:
                logger.warning("MCP 调用异常，重建会话重试一次 | tool=%s err=%s", name, exc)
                await self.close()
                await self.open()
                return await self._call_once(name, args)

    async def _call_once(self, name: str, args: dict) -> Any:
        result = await self._session.call_tool(name, args)
        return _parse_call_result(result)

    async def close(self) -> None:
        """退出上下文：子进程退出、释放 GPU 显存。"""
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("持久会话关闭异常: %s", exc)
            self._cm = None
        self._session = None


class PersistentMCPTool:
    """复用持久会话的 MCP 工具包装：保持 tool.ainvoke 调用面不变。

    业务侧（documents.py / chat.py）仍调 tool.ainvoke(...)，经本包装走持久会话。
    """

    def __init__(self, name: str, session: PersistentMCPSession):
        self.name = name
        self._session = session

    async def ainvoke(self, args: dict) -> Any:
        return await self._session.call(self.name, args)


async def create_persistent_mcp_session(
    config_path: str | Path | None = None,
) -> Optional[PersistentMCPSession]:
    """创建持久 MCP 会话（取 mcp.json 中第一个 server 的 connection）。"""
    config = await mcp_load_servers(config_path)
    servers = config.get("mcpServers", {})
    if not servers:
        logging.warning("mcp.json 中未配置任何服务，无法创建持久 MCP 会话")
        return None
    name, connection = next(iter(servers.items()))
    logger.info(f"创建持久 MCP 会话 | server={name}")
    return PersistentMCPSession(connection)
