"""文件 tool 集合(12 个):5 读 + 2 标签写 + 5 文件写。

源:mcp_server.py:519-562(读) + 1007-1045(标签) + 1046-1106(文件写)

关键模式:
- `from zspace.mcp_server.main import mcp`        — mcp 永不重新赋值,from-import 安全
- `from zspace.mcp_server import main as _main`   — 通过 _main.nas 访问 _startup 后赋值的 NasClient
"""
from zspace.mcp_server import main as _main
from zspace.mcp_server.main import mcp
from zspace.mcp_server.perf import _to_json
from zspace.mcp_server.rag_hook import _rag_hook


# ---- 文件读(5)----
@mcp.tool()
async def list_files(path: str = "/sata14/my/data/") -> str:
    """列出 NAS 目录下的文件/文件夹。路径格式:/<pool>/my/<子目录>/,例如 /sata14/my/data/。
    用户只能看自己 /池名/my/ 下的内容。"""
    nas = _main.nas
    r = await nas.post("/v2/file/list", {
        "folderId": 0, "path": path, "start": 0, "num": 200,
        "sortby": "name", "order": "asc", "show_hidden": 0,
    })
    if str(r.get("code")) == "200":
        items = r.get("data", {}).get("list", [])
        summary = [{"name": i.get("name"), "is_dir": i.get("is_dir"),
                    "size": i.get("size"), "modify_time": i.get("modify_time"),
                    "path": i.get("path")} for i in items]
        return _to_json({"total": r["data"].get("total"), "items": summary})
    return _to_json(r)


@mcp.tool()
async def file_info(path: str) -> str:
    """获取单个文件/文件夹的详细元数据。"""
    return _to_json(await _main.nas.post("/v2/file/info", {"path": path}))


@mcp.tool()
async def recent_files() -> str:
    """最近访问的文件(实测约 992 项)。"""
    return _to_json(await _main.nas.post("/v2/recent/list", {}))


@mcp.tool()
async def file_categories() -> str:
    """按类型分类统计(图片/视频/文档/音频 等)。"""
    return _to_json(await _main.nas.post("/v2/file/categories", {}))


@mcp.tool()
async def list_file_labels() -> str:
    """列出 NAS 上所有文件标签(用户自建的标签体系,如 docker/课件/合同验收)。
    NAS 端点:/v2/labels/alllabels。
    返回:`data.list[{id, label_name, created_at, updated_at, top_flag, weight}]`"""
    return _to_json(await _main.nas.post("/v2/labels/alllabels", {}))


# ---- 标签写(2,⚠️ 真实落盘)----
@mcp.tool()
async def save_file_label(label_names: str, paths: str) -> str:
    """⚠️ 写入:给文件/文件夹打标签(覆盖式,非追加)。

    label_names: 标签名,**多个用英文逗号分隔**,如 docker,重要
    paths: 文件/文件夹路径,**多个用英文逗号分隔**,如 /sata14/my/data/a.yml,/sata14/my/data/b/
    行为:把指定标签集合**完整替换**到这些文件上(已有的其他标签会被清掉)
    NAS 端点:/v2/labels/savefilelabel
    ⚠️ 注意:
    - 此操作是覆盖式,会清除这些文件上之前已打的其他标签
    - 字段是 `label_names[]` + `filepaths[]` PHP 数组语法(本工具自动处理)
    - **如果 label_names 里有不存在的标签名,NAS 会自动创建**(实测验证)
      所以这个 tool 同时也是**创建新标签**的唯一入口
    - 想要纯创建标签但不打到任何文件,传 `paths="/sata14/my/data/"`(任意已有路径即可)"""
    label_list = [s.strip() for s in label_names.split(",") if s.strip()]
    path_list = [s.strip() for s in paths.split(",") if s.strip()]
    return _to_json(await _main.nas.post("/v2/labels/savefilelabel", {
        "label_names[]": label_list,
        "filepaths[]": path_list,
    }))


@mcp.tool()
async def delete_label(label_names: str) -> str:
    """⚠️⚠️ 写入:删除一个或多个用户自建标签。

    label_names: 标签名,**多个用英文逗号分隔**,如 docker,重要
    NAS 端点:/v2/labels/deletelabel
    ⚠️ 注意:
    - 字段是 `label_names[]` PHP 数组语法(本工具自动处理)
    - 删除标签后,**所有文件上打的这个标签都会被移除**(不只是解除关联)
    - 标签 ID 在 list_file_labels 里看;删除用名字,不需要先查 ID
    - NAS 没有专门的"创建标签"端点 — 用 `save_file_label` 传不存在的标签名会自动建"""
    label_list = [s.strip() for s in label_names.split(",") if s.strip()]
    return _to_json(await _main.nas.post("/v2/labels/deletelabel", {
        "label_names[]": label_list,
    }))


# ---- 文件写(5,⚠️ 真实落盘)----
@mcp.tool()
async def mkdir(parent: str, name: str) -> str:
    """⚠️ 写入:在 NAS 创建文件夹。
    parent: 父目录,无尾斜杠,如 /sata14/my/data/备份
    name: 新文件夹名,如 test
    返回新文件夹的完整 metadata(失败返回 NAS 错误码)。"""
    return _to_json(await _main.nas.post("/v2/file/newdir", {
        "parent": parent, "name": name, "rename": 0,
    }))


@mcp.tool()
async def rename(path: str, newname: str) -> str:
    """⚠️ 写入:重命名文件/文件夹。
    path: 原完整路径,如 /sata14/my/data/备份/test
    newname: 新名字(只名字,不是完整路径)"""
    # NAS 用 form 时字段是 newname,直接传 dict 会编码成 newname=...
    resp = await _main.nas.post("/v2/file/modify", {"path": path, "newname": newname})
    # 算新路径 = dirname(path) + '/' + newname
    idx = path.rfind("/")
    new_path = (path[: idx + 1] if idx >= 0 else "") + newname
    _rag_hook("rag_on_file_rename", resp, path, new_path)
    return _to_json(resp)


@mcp.tool()
async def move(paths: str, to: str) -> str:
    """⚠️ 写入:移动文件/文件夹到目标目录。
    paths: 源路径,**多个用英文逗号分隔**,如 /a/b.txt,/c/d.txt
    to: 目标目录(必须已存在),如 /sata14/my/data/目标"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    to_clean = to.rstrip("/") + "/"
    resp = await _main.nas.post("/v2/file/move", {"to": to, "paths[]": path_list})
    moves = [(p, to_clean + p.rsplit("/", 1)[-1]) for p in path_list]
    _rag_hook("rag_on_file_move", resp, moves)
    return _to_json(resp)


@mcp.tool()
async def copy(paths: str, to: str) -> str:
    """⚠️ 写入:复制文件/文件夹到目标目录。
    paths: 源路径,多个用英文逗号分隔
    to: 目标目录(必须已存在)"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    to_clean = to.rstrip("/") + "/"
    resp = await _main.nas.post("/v2/file/copy", {"to": to, "paths[]": path_list})
    new_paths = [to_clean + p.rsplit("/", 1)[-1] for p in path_list]
    _rag_hook("rag_on_file_write", resp, new_paths)
    return _to_json(resp)


@mcp.tool()
async def remove(paths: str) -> str:
    """⚠️⚠️ 写入(危险):删除文件/文件夹,**不进回收站,不可逆**!
    paths: 要删的路径,多个用英文逗号分隔
    端点名是 /v2/file/remove(不是 delete)"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    resp = await _main.nas.post("/v2/file/remove", {"paths[]": path_list})
    _rag_hook("rag_on_file_delete", resp, path_list)
    return _to_json(resp)
