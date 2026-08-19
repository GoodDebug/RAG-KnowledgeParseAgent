"""
【案例】环境检查：LangChain 版本与安装路径

对应教程章节：第 10 章 - LangChain 快速上手与 HelloWorld → 3、安装依赖

知识点速览：
本脚本用于确认当前 Python 环境中 LangChain、langchain_community 的版本与安装路径，
便于排查「装错版本」「没进虚拟环境」或「解释器不是当前项目那一个」等问题。无需 API Key，可直接运行。
"""

import langchain  # 核心框架（Chain、Agent、Memory 等）
import langchain_community  # 社区扩展（部分集成、第三方工具等）
import sys  # 获取 Python 解释器信息

# LangChain 核心包版本号（需与教程/文档要求的版本区间一致）
print("langchainVersion:  " + langchain.__version__)
# 社区扩展包版本号
print("langchain_communityVersion:  " + langchain_community.__version__)
# LangChain 实际安装路径（可确认是否来自当前虚拟环境）
print("langchainfile:" + langchain.__file__)

# 当前 Python 解释器版本（如 3.10.x），用于确认运行环境
print(sys.version)
# 当前 Python 可执行文件路径；当你怀疑“包装到了 A 环境，但运行却走了 B 环境”时尤其有用
print("pythonExecutable:" + sys.executable)

"""
【输出示例】
langchainVersion:  1.3.13
langchain_communityVersion:  0.4.2
langchainfile:/home/asus/miniconda3/envs/env_agent001/lib/python3.11/site-packages/langchain/__init__.py
3.11.15 (main, Jun 11 2026, 15:20:16) [GCC 14.3.0]
pythonExecutable:/home/asus/miniconda3/envs/env_agent001/bin/python
"""
