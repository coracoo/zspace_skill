"""`python -m zspace.mcp_server` 入口。

默认 stdio 传输(本地 Claude Code 用)。
加 `--http` 切到 streamable-http,默认 bind 0.0.0.0:8765 + Bearer 鉴权
(鉴权由 main.py 的 _configure_http 处理)。
"""
import sys

from zspace.mcp_server.main import main

if __name__ == "__main__":
    if "--http" in sys.argv:
        main(transport="streamable-http", host="0.0.0.0", port=8765)
    else:
        main()  # stdio,默认参数