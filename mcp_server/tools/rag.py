"""RAG tool(语义搜索 / 重建索引 / 索引状态)— HTTP 客户端调 NAS daemon。

NAS daemon 地址从 env `NAS_RAG_URL` 读,默认 http://<nas_ip>:8000。

路径映射:NAS daemon 返回的容器内路径(/nas_data/xxx)需要映射回
NAS 文件 API 能识别的路径。映射规则从 env `NAS_RAG_PATH_MAP` 读,
格式:容器前缀→NAS前缀,多个用 ; 分隔。
例:NAS_RAG_PATH_MAP="/nas_data/→/sata14/my/data/课程资料/课本/"

版本历史:
- v1(2026-07-25 之前):本机 embed + sqlite-vec(Path.stat 失败,弃用)
- v2(2026-07-25):HTTP 调 NAS docker daemon
"""
import json
import logging
import os

import httpx

from mcp_server.main import mcp

log = logging.getLogger("zspace-mcp")

# NAS RAG daemon 地址
NAS_RAG_URL = os.environ.get("NAS_RAG_URL", "http://<nas_ip>:8000").rstrip("/")

# 路径映射:容器路径→NAS API 路径
# 格式:"/nas_data/→/sata14/my/data/课程资料/课本/;/nas_root2/→/sata14/my/data/other/"
_PATH_MAP: list[tuple[str, str]] = []
_map_env = os.environ.get("NAS_RAG_PATH_MAP", "")
if _map_env:
    for pair in _map_env.split(";"):
        parts = pair.split("→", 1)
        if len(parts) == 2:
            _PATH_MAP.append((parts[0].strip(), parts[1].strip()))
if not _PATH_MAP:
    pass  # 用户通过 .env 的 NAS_RAG_PATH_MAP 自行配置

log.info("RAG daemon at %s, path_map=%s", NAS_RAG_URL, _PATH_MAP)


def _map_path(container_path: str) -> str:
    """容器路径 → NAS API 路径。"""
    for prefix, replacement in _PATH_MAP:
        if container_path.startswith(prefix):
            return replacement + container_path[len(prefix):]
    return container_path


@mcp.tool()
async def semantic_search(query: str, scope: str = "all", top_k: int = 10) -> str:
    """🔍 语义搜索:NAS 文件内容(RAG,bge-small-zh-v1.5)。

    跟 `notebook_search`(关键词)和 `list_files`(列路径)不同 — 这个是**理解语义**的:
    - "一年级教材" 能找到文件名不含这词但内容是小学教辅的 PDF
    - "docker swarm" 能找到 K8s 部署相关的笔记
    - "报销单" 能找到内容含"发票"的图片/文档

    返回的文件路径可以直接传给 `save_file_label`(label-manager) 打标签。

    query:  自然语言(中文效果最好)
    scope:  all / files / notebooks(默认 all)
    top_k:  返回数量(默认 10,越大越慢)

    返回:[{source_path, snippet, distance}, ...](distance 越小越相关)"""
    if scope not in ("all", "files", "notebooks"):
        scope = "all"
    url = f"{NAS_RAG_URL}/search"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json={"query": query, "scope": scope, "top_k": top_k})
            r.raise_for_status()
    except Exception as e:
        return json.dumps({"error": f"NAS RAG daemon 连不上: {e}"}, ensure_ascii=False)

    data = r.json()
    # 路径映射
    for hit in data.get("results", []):
        hit["source_path"] = _map_path(hit["source_path"])
    return json.dumps(data, ensure_ascii=False)


@mcp.tool()
async def reindex(scope: str = "files", full: bool = False) -> str:
    """🔄 重建 NAS 端 RAG 索引(调用 docker daemon,N150 限速,慢)。

    scope: all / notebooks / files(默认 files)
    full:  True=清空重建,False=增量
    """
    if scope not in ("all", "files", "notebooks"):
        scope = "files"
    url = f"{NAS_RAG_URL}/reindex"
    try:
        async with httpx.AsyncClient(timeout=3600) as client:
            r = await client.post(url, json={"scope": scope, "full": full})
            r.raise_for_status()
    except Exception as e:
        return json.dumps({"error": f"NAS RAG daemon 连不上: {e}"}, ensure_ascii=False)
    return r.text


@mcp.tool()
async def index_status() -> str:
    """📊 RAG 索引概况(调 NAS daemon /status)。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{NAS_RAG_URL}/status")
            r.raise_for_status()
    except Exception as e:
        return json.dumps({"error": f"NAS RAG daemon 连不上: {e}"}, ensure_ascii=False)
    return r.text
