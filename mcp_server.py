"""薄入口 shim — 真正的实现已搬到 mcp_server/ 包。

保留这个文件是为了:
1. 外部 mcp.json 的 args: ["$ROOT/mcp_server.py"] 不需改
2. 旧的 `python mcp_server.py` 启动方式不变
"""
from mcp_server.main import main

if __name__ == "__main__":
    main()
