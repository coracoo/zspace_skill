"""记事本 CRUD 路由(24 个 /action/notebook-* + _safe_html helper)。

搬迁自 app.py:1491-1519(`_safe_html`)+ 1572-2045(24 个 notebook 端点)。

注意:`tab_notebook`(GET /dashboard/notebook 渲染 tab)归 routes/dashboard.py,
本文件只管 /action/notebook-* 数据端点。
"""
import logging
import re
from typing import Any, Dict

import bleach
import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response

from dashboard.app.nas_helpers import append_common_query, nas_post

from zspace.nas import NAS_BASE

log = logging.getLogger("zspace-poc")
router = APIRouter()


def safe_html(html: str) -> str:
    """清理笔记 content 字段(可能含富文本),白名单标签 + 属性 + 协议,strip 危险内容。

    注册成 jinja filter "safe_html"(在 main.py 里注册)。
    """
    if not html:
        return ""
    return bleach.clean(
        html,
        tags=[
            "p", "br", "b", "strong", "i", "em", "u", "s", "del",
            "ul", "ol", "li",
            "h1", "h2", "h3", "h4", "h5", "h6",
            "blockquote", "code", "pre",
            "img", "a", "span", "div", "hr",
            "table", "thead", "tbody", "tr", "th", "td",
        ],
        attributes={
            "a": ["href", "title", "target", "rel"],
            "img": ["src", "alt", "width", "height", "title"],
            "span": ["style"],
            "div": ["style"],
            "p": ["style"],
        },
        protocols=["http", "https", "data", "mailto"],
        strip=True,
    )


# ---- 读 action(GET,前端 fetch 用)----
# notebook-list 在 §6.3.2 "完整化" 区里有带 start 参数的版本(L1159),这里不重复定义


@router.get("/action/notebook-info")
async def action_notebook_info(request: Request, id: int):
    """笔记详情(content 字段在 data.content,可能含富文本)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/info", {
            "id": id, "location": 2,
        })


@router.get("/action/notebook-search")
async def action_notebook_search(request: Request, keyword: str, num: int = 50):
    """搜索笔记。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/searchnotepad", {
            "keyword": keyword, "num": num, "location": 2,
        })


@router.get("/action/notebook-history")
async def action_notebook_history(request: Request, id: int):
    """历史版本列表。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/historylist", {
            "id": id, "location": 2,
        })


@router.get("/action/notebook-getconfig")
async def action_notebook_getconfig(request: Request):
    """读配置。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/getconfig", {"location": 2})


@router.get("/action/notebook-classifylist")
async def action_notebook_classifylist(request: Request, start: int = 0, num: int = 50):
    """分类列表(供前端刷新侧栏)。默认只列顶层(parent_id=0);带 parent_id=N 查直接子。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/classifylist", {
            "start": start, "num": num, "location": 2,
        })


@router.get("/action/notebook-allclassify")
async def action_notebook_allclassify(request: Request):
    """完整分类树(含嵌套)。每个节点字段:
    {id, name, parent_id, child: [...]}。
    客户端要做"分类1 下"聚合时:遍历该树,递归对每个叶子调 list?classify_id=leaf.id。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/allclassify", {
            "location": 2,
        })


@router.get("/action/notebook-totalsize")
async def action_notebook_totalsize(request: Request):
    """总占用(供前端刷新 metric)"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/totalsize", {"location": 2})


# ---- 写 action(POST,字段名全部已确认)----
@router.post("/action/notebook-new")
async def action_notebook_new(request: Request,
                                title: str = Form(...),
                                body: str = Form(""),
                                classify_id: int = Form(0)):
    """⚠️ 关键:
    - body 字段(不是 content!)+ in_brief + classify_id + location
    - body 必须以 `<h1>{title}</h1>` 开头,否则 NAS 不存内容(实测)
    - in_brief 从 body 去 HTML 后截前 100 字符
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    # 自动加 h1 标题前缀(参考 pcweb HAR 抓包:body 必以 <h1>{title}</h1> 开头)
    body = body or ""
    h1_prefix = f"<h1>{title}</h1>"
    if not body.lstrip().startswith("<h1>"):
        body = h1_prefix + body
    plain = re.sub(r"<[^>]+>", " ", body).strip()
    in_brief = plain[:100]
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/new", {
            "title": title, "body": body, "in_brief": in_brief,
            "classify_id": classify_id, "location": 2,
        })
    log.info("notebook-new title=%r body=%d chars → code=%s",
             title[:40], len(body), res.get("code"))
    return res


@router.post("/action/notebook-modify")
async def action_notebook_modify(request: Request,
                                   id: int = Form(...),
                                   title: str = Form(...),
                                   body: str = Form("")):
    """⚠️ body 必以 `<h1>{title}</h1>` 开头,否则 NAS 不存内容(实测)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    body = body or ""
    h1_prefix = f"<h1>{title}</h1>"
    if not body.lstrip().startswith("<h1>"):
        body = h1_prefix + body
    plain = re.sub(r"<[^>]+>", " ", body).strip()
    in_brief = plain[:100]
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/modify", {
            "id": id, "title": title, "body": body, "in_brief": in_brief, "location": 2,
        })
    log.info("notebook-modify id=%s title=%r body=%d chars → code=%s",
             id, title[:40], len(body), res.get("code"))
    return res


@router.post("/action/notebook-delete")
async def action_notebook_delete(request: Request, id: int = Form(...)):
    """单删笔记:NAS /v2/file/notepad/delete,字段是 `ids[]`(pcweb HAR 抓包确认)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/notepad/delete")
        # NAS 字段名是 `ids[]`(PHP 数组),即使单删也是
        r = await client.post(url, data={"ids[]": [id], "location": "2"})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("notebook-delete id=%s → code=%s", id, res.get("code"))
    return res


@router.post("/action/notebook-delete-batch")
async def action_notebook_delete_batch(request: Request, ids: str = Form(...)):
    """批量删除:NAS /v2/file/notepad/delete 接 PHP 数组 `ids[]`(pcweb HAR 抓包确认)。
    ids 用逗号分隔(如 "16,17,18"),服务端会展开成 ids[]=16&ids[]=17&ids[]=18。
    NAS 端行为未验证:本端点**只做透传**,原样返回 NAS response。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if not id_list:
        return {"error": "no valid ids"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/notepad/delete")
        # 关键字段名:`ids[]`(pcweb HAR 抓包,带 s)
        r = await client.post(url, data={"ids[]": id_list, "location": "2"})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("notebook-delete-batch ids=%s → code=%s", id_list, res.get("code"))
    return res


@router.post("/action/notebook-pin")
async def action_notebook_pin(request: Request, id: int = Form(...), is_top: int = Form(1)):
    """is_top: 1=置顶, 0=取消置顶。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/pin", {
            "id": id, "is_top": is_top, "location": 2,
        })
    log.info("notebook-pin id=%s is_top=%s → code=%s", id, is_top, res.get("code"))
    return res


@router.post("/action/notebook-updatelabel")
async def action_notebook_updatelabel(request: Request,
                                        id: int = Form(...),
                                        label: str = Form("")):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/updatelabel", {
            "id": id, "label": label, "location": 2,
        })
    log.info("notebook-updatelabel id=%s label=%r → code=%s", id, label, res.get("code"))
    return res


@router.post("/action/notebook-movenotepad")
async def action_notebook_movenotepad(request: Request,
                                       id: int = Form(...),
                                       classify_id: int = Form(...)):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/movenotepad", {
            "id": id, "classify_id": classify_id, "location": 2,
        })
    log.info("notebook-movenotepad id=%s → classify=%s → code=%s",
             id, classify_id, res.get("code"))
    return res


@router.post("/action/notebook-newclassify")
async def action_notebook_newclassify(request: Request,
                                       name: str = Form(...),
                                       parent_id: int = Form(0)):
    """parent_id=0 表示顶级分类。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/newclassify", {
            "name": name, "parent_id": parent_id, "location": 2,
        })
    log.info("notebook-newclassify name=%r parent=%s → code=%s",
             name, parent_id, res.get("code"))
    return res


@router.post("/action/notebook-deleteclassify")
async def action_notebook_deleteclassify(request: Request,
                                          classify_id: int = Form(...)):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/deleteclassify", {
            "classify_id": classify_id, "location": 2,
        })
    log.info("notebook-deleteclassify classify_id=%s → code=%s",
             classify_id, res.get("code"))
    return res


@router.post("/action/notebook-updateclassify")
async def action_notebook_updateclassify(request: Request,
                                          classify_id: int = Form(...),
                                          new_name: str = Form(...)):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/notepad/updateclassify", {
            "classify_id": classify_id, "new_name": new_name, "location": 2,
        })
    log.info("notebook-updateclassify classify_id=%s new_name=%r → code=%s",
             classify_id, new_name, res.get("code"))
    return res


# ============================================================================
# 完整化:MCP 用的端点,把 NAS notepad/* 全暴露
# ============================================================================

# ---- list 加 start 参数(分页) ----
@router.get("/action/notebook-list")
async def action_notebook_list(request: Request, classify_id: int = 0, num: int = 50, start: int = 0):
    """切换"分类"视图拉笔记列表(支持分页)。classify_id 语义(实测):
    - 0  → "全部" (active + 未分类聚合)
    - >0 → 指定分类 id(必须是**笔记直属**分类 id,不递归子分类)
    - -1 → "最近删除"(trash,NAS 用 -1 作为统一 trash 桶,**没有独立 recycle 端点**)
    其他值(我试过 -2/-99/-100/-999):返回 0,不会列别的。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/list", {
            "classify_id": classify_id, "start": start, "num": num, "location": 2,
        })


# ---- 历史版本(别名,canonical 名 historylist)----
@router.get("/action/notebook-historylist")
async def action_notebook_historylist(request: Request, id: int, num: int = 50):
    """历史版本列表(/v2/file/notepad/historylist,正式名)。

    ⚠️ NAS 这个端点对所有合理字段名都返回 N001212 参数有误,实测过:
       id / note_id / nid / noteId / noteid / ids[] 全部 N001212。
       不带 location → N001603 保险箱未打开(说明确实进了端点逻辑)。
       唯一可能是字段名还有别的(比如 pcweb 私有的 X-CSRF-Token 头),
       暂时没法破。要用就在 UI 上抓包看 pcweb 发啥。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/historylist", {
            "id": id, "num": num, "location": 2,
        })


@router.get("/action/notebook-historyinfo")
async def action_notebook_historyinfo(request: Request, id: int, history_id: int = 0):
    """单个历史版本详情(从 historylist 拿 history_id 后用这个查内容)。
    字段:id(=笔记 id) + history_id(=历史版本 id,historylist 返回的) + location。
    返回 data.content 是历史版本的 body(可能含 HTML,经 bleach 清理)。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    body: Dict[str, Any] = {"id": id, "location": 2}
    if history_id:
        body["history_id"] = history_id
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/historyinfo", body)


# ---- 搜索(别名,canonical 名 searchnotepad)----
@router.get("/action/notebook-searchnotepad")
async def action_notebook_searchnotepad(request: Request, keyword: str, num: int = 50):
    """搜索笔记(/v2/file/notepad/searchnotepad,正式名)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/searchnotepad", {
            "keyword": keyword, "num": num, "location": 2,
        })


# ---- 写配置(读有 getconfig,写也要有)----
@router.post("/action/notebook-setconfig")
async def action_notebook_setconfig(request: Request):
    """写配置(/v2/file/notepad/setconfig)。
    请求体传整个配置 JSON。读出 getconfig 看现有结构,改完再用 setconfig 写回。
    ⚠️ 字段名待精确(实测可能包含 key/value 或 json 整体)— 用先用缺字段 422 验 form 解析,
    真要写先在 UI 试。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    try:
        body = await request.json()
    except Exception:
        form = await request.form()
        body = dict(form)
    body.setdefault("location", 2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, "/v2/file/notepad/setconfig", body)


# ---- 分类树拖拽保存 ----
@router.post("/action/notebook-save-classify-tree")
async def action_notebook_save_classify_tree(request: Request, tree: str = Form(...)):
    """保存分类树(pcs 拖拽后调用)。
    ⚠️ 字段名 `tree` 暂定(实测有可能是 `classify_tree` 或 JSON 整体)— 还没在 UI 验过。
    body: tree='[{...}, ...]' 整个树 JSON 字符串,服务端原样转发。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    # 这个端点 NAS 期望可能是 JSON body 或 form,先按 form 透传
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/notepad/save_classify_tree")
        # tree 是 JSON 字符串,location=2 必带
        r = await client.post(url, data={"tree": tree, "location": "2"})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("save-classify-tree → code=%s", res.get("code"))
    return res


# ---- 笔记内嵌附件下载 ----
@router.get("/action/notebook-downloadfile")
async def action_notebook_downloadfile(request: Request, file_id: int):
    """下载笔记内嵌附件(/v2/file/notepad/downloadfile)。
    GET 形式,NAS 直接回二进制文件。透传回去,Content-Type 用 NAS 的。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return Response(content=b"not logged in", status_code=401)
    async with httpx.AsyncClient(timeout=30, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/notepad/downloadfile")
        r = await client.get(url, params={"file_id": file_id, "location": 2})
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": r.headers.get("content-disposition",
                     f'attachment; filename="notepad_{file_id}"')},
    )


# ---- 笔记 Word 导出 ----
@router.get("/action/notebook-downloadocx")
async def action_notebook_downloadocx(request: Request, id: int):
    """导出笔记为 Word(.docx)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return Response(content=b"not logged in", status_code=401)
    async with httpx.AsyncClient(timeout=30, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/notepad/downloadocx")
        r = await client.get(url, params={"id": id, "location": 2})
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={"Content-Disposition": r.headers.get("content-disposition",
                     f'attachment; filename="notepad_{id}.docx"')},
    )


# ---- 笔记纯文本导出 ----
@router.get("/action/notebook-downloadt")
async def action_notebook_downloadt(request: Request, id: int):
    """导出笔记为纯文本(.txt)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return Response(content=b"not logged in", status_code=401)
    async with httpx.AsyncClient(timeout=30, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/notepad/downloadt")
        r = await client.get(url, params={"id": id, "location": 2})
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "text/plain; charset=utf-8"),
        headers={"Content-Disposition": r.headers.get("content-disposition",
                     f'attachment; filename="notepad_{id}.txt"')},
    )


# ---- 笔记内嵌附件上传 ----
@router.post("/action/notebook-uploadfile")
async def action_notebook_uploadfile(request: Request, file: UploadFile = File(...)):
    """上传笔记内嵌附件/图片(/v2/file/notepad/uploadfile,POST octet-stream)。
    multipart 表单字段名 `file`,转发 NAS 时 NAS 期望 location=2 + file 二进制。
    ⚠️ 字段精确名待测(可能是 `file`/`data`/`content`),先用缺字段测 422。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    blob = await file.read()
    async with httpx.AsyncClient(timeout=60, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/notepad/uploadfile")
        # NAS 上传是 multipart: location 在 form 字段,file 在 file 字段
        r = await client.post(
            url,
            data={"location": "2"},
            files={"file": (file.filename or "upload.bin", blob, file.content_type or "application/octet-stream")},
        )
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_raw": r.text[:300]}
