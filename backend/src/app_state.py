"""
全局应用状态。
与 main_fastapi.py 分离，避免 routers 导入时的循环依赖。
"""


class AppState:
    llm_client = None
    milvus_client = None
    MCP_client = None
    mcp_session = None          # 持久 MCP 会话（方案 A：一个子进程常驻，模型加载一次）
    tools = []
    openai_tools = []
    tool_map = {}
    str_tools = ""


state = AppState()
