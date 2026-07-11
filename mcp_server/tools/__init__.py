"""MCP server tool 集合(按域分文件)。

按域拆成 7 个文件 + 1 个可选 RAG:
- files    — 文件读写 + 标签(12)
- storage  — 存储池 + 监控 + whoami(8)
- zvideo   — 极影视(8)
- media    — 音乐 + 相册(3)
- shares   — 下载 + 分享 + 共享服务(7)
- notebook — 记事本(17)
- proxy    — 远程访问代理(4)
- rag      — 可选,rag 包未装时 import 失败被 main.py 吞掉

import 本包并不触发注册(那要靠 mcp_server/main.py 在 mcp 定义后 import 子模块)。
"""
