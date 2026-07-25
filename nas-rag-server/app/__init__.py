"""nas-rag-server — NAS 端 RAG 服务(bge-small-zh-v1.5 + sqlite-vec + FastAPI)。

跑在 NAS docker container,直接读 NAS 文件系统;本机 MCP 工具 HTTP 调用消费。

详见 docs/03-API.md。
"""
__version__ = "0.1.0"