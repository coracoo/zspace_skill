"""📒 记事本 tool 集合(17 个):8 读 + 9 写。

源:mcp_server.py:785-1005

关键坑(保留原注释):
  - body 必须以 <h1>{title}</h1> 开头(否则 NAS 不存内容,实测)
  - 删除用 ids[] PHP 数组语法(批量:ids[]=3&ids[]=4)
  - pin 字段是 pin_flag 不是 is_top(但两个 NAS 都接受)
  - classify_id=-1 = "最近删除"(trash);0 = "全部";>0 = 叶子分类(不递归父分类)

location=2 = 主菜单的独立记事本(平级于保险箱);location=1 是保险箱备忘录,需要开保险箱。
"""
from mcp_server import main as _main
from mcp_server.main import mcp
from mcp_server.perf import _to_json
from mcp_server.rag_hook import _rag_hook


# ============ 笔记读(8)============
@mcp.tool()
async def notebook_list(classify_id: int = 0, num: int = 50, start: int = 0) -> str:
    """列出笔记。
    classify_id 语义:
      0  → "全部笔记"(active + 未分类)
      >0 → 指定分类 id(必须是笔记**直属**分类 id,不递归子分类)
      -1 → "最近删除"(trash)
    num: 每页条数(默认 50)
    start: 分页偏移(默认 0)
    返回 list + total。
    NAS 端点:/v2/file/notepad/list"""
    return _to_json(await _main.nas.post("/v2/file/notepad/list", {
        "classify_id": classify_id, "start": start, "num": num, "location": 2,
    }))


@mcp.tool()
async def notebook_info(id: int) -> str:
    """单条笔记详情(含 body HTML、title、分类、标签、更新时间)。
    id: 笔记 id(从 notebook_list 拿)
    NAS 端点:/v2/file/notepad/info"""
    return _to_json(await _main.nas.post("/v2/file/notepad/info", {
        "id": id, "location": 2,
    }))


@mcp.tool()
async def notebook_search(keyword: str, num: int = 50) -> str:
    """搜索笔记(标题/正文/in_brief 全文匹配)。
    keyword: 关键词
    num: 返回条数上限(默认 50)
    NAS 端点:/v2/file/notepad/searchnotepad"""
    return _to_json(await _main.nas.post("/v2/file/notepad/searchnotepad", {
        "keyword": keyword, "num": num, "location": 2,
    }))


@mcp.tool()
async def notebook_allclassify() -> str:
    """完整分类树(含嵌套,每个节点带 child[] 数组)。
    笔记 → 叶子分类绑定:note.classify_id 等于**叶子**分类 id,不是父级。
    pcweb 的"分类1"父级视图是前端聚合(遍历树 + 每个叶子调 notebook_list(classify_id=leaf.id))。
    NAS 端点:/v2/file/notepad/allclassify"""
    return _to_json(await _main.nas.post("/v2/file/notepad/allclassify", {"location": 2}))


@mcp.tool()
async def notebook_classifylist() -> str:
    """顶层分类列表(只列 parent_id=0 的顶层,带 child_num 计数)。
    不如 notebook_allclassify 完整(无嵌套),只是顶层概览。
    NAS 端点:/v2/file/notepad/classifylist"""
    return _to_json(await _main.nas.post("/v2/file/notepad/classifylist", {
        "start": 0, "num": 50, "location": 2,
    }))


@mcp.tool()
async def notebook_totalsize() -> str:
    """笔记总占用大小(字节)。
    NAS 端点:/v2/file/notepad/totalsize"""
    return _to_json(await _main.nas.post("/v2/file/notepad/totalsize", {"location": 2}))


@mcp.tool()
async def notebook_getconfig() -> str:
    """记事本配置(自动保存时间等)。
    返回 list[{id, scope, config_key, config_value, ...}]。
    NAS 端点:/v2/file/notepad/getconfig"""
    return _to_json(await _main.nas.post("/v2/file/notepad/getconfig", {"location": 2}))


@mcp.tool()
async def notebook_historyinfo(id: int, history_id: int = 0) -> str:
    """单个历史版本详情(从历史版本拿 body)。
    id: 笔记 id
    history_id: 历史版本 id(从 historylist 拿;historylist 字段未破,
              当前直接传 history_id=0 也能拿到笔记的"当前版本"快照)
    NAS 端点:/v2/file/notepad/historyinfo"""
    return _to_json(await _main.nas.post("/v2/file/notepad/historyinfo", {
        "id": id, "history_id": history_id, "location": 2,
    }))


# ============ 笔记写(9,⚠️ 真实落盘)============
# h1 前缀坑:body 必须以 `<h1>{title}</h1>` 开头,否则 NAS 不存内容
# 删两次坑:同 id 第一次删移到 trash(classify_id=-1),第二次永久删除,不可恢复

_H1_TITLE_RE = None


def _ensure_h1_prefix(title: str, body: str) -> str:
    """确保 body 以 `<h1>{title}</h1>` 开头,容忍标签带属性/空白。
    若开头已是匹配的 h1(任意属性)则原样返回,否则补一个。"""
    global _H1_TITLE_RE
    if _H1_TITLE_RE is None:
        import re as _re
        _H1_TITLE_RE = _re.compile(r"^\s*<h1\b[^>]*>", _re.IGNORECASE)
    if _H1_TITLE_RE.match(body):
        return body  # 已有 h1 开头(容忍属性),不重复补
    return f"<h1>{title}</h1>\n{body}"


@mcp.tool()
async def notebook_new(title: str, body: str, classify_id: int = 0) -> str:
    """⚠️ 写入:新建笔记。
    title: 标题
    body: HTML 正文,**必须以 `<h1>{title}</h1>` 开头**(自动加,不用手动拼)
    classify_id: 目标**叶子**分类 id(0=未分类,不是父级)
    返回新笔记 id。
    NAS 端点:/v2/file/notepad/new"""
    # 自动加 h1 前缀防"body 字段对但 NAS 存空"的坑
    body = _ensure_h1_prefix(title, body)
    resp = await _main.nas.post("/v2/file/notepad/new", {
        "title": title, "body": body, "classify_id": classify_id, "location": 2,
    })
    new_id = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
    if new_id is not None:
        _rag_hook("rag_on_notebook_write", resp, new_id, title, body)
    return _to_json(resp)


@mcp.tool()
async def notebook_modify(id: int, title: str, body: str) -> str:
    """⚠️ 写入:修改笔记。
    id: 笔记 id
    title: 新标题
    body: 新正文(必须以 `<h1>{title}</h1>` 开头,自动加)
    NAS 端点:/v2/file/notepad/modify"""
    body = _ensure_h1_prefix(title, body)
    resp = await _main.nas.post("/v2/file/notepad/modify", {
        "id": id, "title": title, "body": body, "location": 2,
    })
    _rag_hook("rag_on_notebook_write", resp, id, title, body)
    return _to_json(resp)


@mcp.tool()
async def notebook_delete(ids: str) -> str:
    """⚠️ 写入:删除笔记(支持批量,**进 trash**)。
    ids: 笔记 id,**多个用英文逗号分隔**,如 `3,4,5`
    第一次删:移到 trash(classify_id=-1);第二次同 id:永久删除不可恢复
    批量用 ids[] PHP 数组语法(httpx 自动编码)
    NAS 端点:/v2/file/notepad/delete"""
    id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
    resp = await _main.nas.post("/v2/file/notepad/delete", {
        "ids[]": id_list, "location": 2,
    })
    _rag_hook("rag_on_notebook_delete", resp, id_list)
    return _to_json(resp)


@mcp.tool()
async def notebook_pin(id: int, pin_flag: int) -> str:
    """⚠️ 写入:置顶 / 取消置顶。
    id: 笔记 id
    pin_flag: 1=置顶, 0=取消
    NAS 字段名是 pin_flag(也接受 is_top,但 pin_flag 是官方)
    NAS 端点:/v2/file/notepad/pin"""
    return _to_json(await _main.nas.post("/v2/file/notepad/pin", {
        "id": id, "pin_flag": pin_flag, "location": 2,
    }))


@mcp.tool()
async def notebook_updatelabel(id: int, label: str) -> str:
    """⚠️ 写入:更新笔记标签。
    id: 笔记 id
    label: 标签,**逗号分隔**(如 `工作,dashboard`);**空字符串 = 清空所有标签**
    NAS 端点:/v2/file/notepad/updatelabel"""
    return _to_json(await _main.nas.post("/v2/file/notepad/updatelabel", {
        "id": id, "label": label, "location": 2,
    }))


@mcp.tool()
async def notebook_movenotepad(id: int, classify_id: int) -> str:
    """⚠️ 写入:移动笔记到分类。
    id: 笔记 id
    classify_id: 目标**叶子**分类 id(子分类优先;不能用父级 id)
    NAS 端点:/v2/file/notepad/movenotepad"""
    return _to_json(await _main.nas.post("/v2/file/notepad/movenotepad", {
        "id": id, "classify_id": classify_id, "location": 2,
    }))


@mcp.tool()
async def notebook_newclassify(name: str, parent_id: int = 0) -> str:
    """⚠️ 写入:新建分类。
    name: 分类名
    parent_id: 父分类 id(0=顶级;>0=父分类的 id 实现嵌套)
    NAS 端点:/v2/file/notepad/newclassify"""
    return _to_json(await _main.nas.post("/v2/file/notepad/newclassify", {
        "name": name, "parent_id": parent_id, "location": 2,
    }))


@mcp.tool()
async def notebook_deleteclassify(classify_id: int) -> str:
    """⚠️ 写入:删除分类。**分类下的笔记会被 NAS 处理**(进 trash 或变 classify_id=0,实测未明)。
    classify_id: 要删的分类 id
    NAS 端点:/v2/file/notepad/deleteclassify"""
    return _to_json(await _main.nas.post("/v2/file/notepad/deleteclassify", {
        "classify_id": classify_id, "location": 2,
    }))


@mcp.tool()
async def notebook_updateclassify(classify_id: int, new_name: str) -> str:
    """⚠️ 写入:重命名分类。
    classify_id: 要改的分类 id
    new_name: 新名字
    NAS 端点:/v2/file/notepad/updateclassify"""
    return _to_json(await _main.nas.post("/v2/file/notepad/updateclassify", {
        "classify_id": classify_id, "new_name": new_name, "location": 2,
    }))
