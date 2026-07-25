"""Dashboard tab 路由(4 个 GET tab)。

搬迁自 app.py:525-682(`dashboard_root`/`tab_overview`/`tab_storage`/`tab_zvideo`)
+ 1520-1571(`tab_notebook`)。

注意:tab_notebook 是 GET 渲染 tab 的,归 dashboard;笔记 CRUD 的 /action/notebook-*
归 routes/notebook.py。
"""
import asyncio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dashboard.app.deps import common_ctx, require_login
from dashboard.app.nas_helpers import append_common_query, nas_get, nas_post
from dashboard.app.perf import get_perf_cached
from dashboard.app.zstatus import build_breadcrumb, parse_zstatus

from nas import NAS_BASE

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_root(request: Request):
    """旧入口重定向到 overview tab。"""
    return RedirectResponse("/dashboard/overview", status_code=303)


@router.get("/dashboard/overview", response_class=HTMLResponse)
async def tab_overview(request: Request):
    """总览 tab:监控(zstatus)+ 性能快照(SSH /proc)。"""
    cookies, redirect = require_login(request)
    if redirect: return redirect

    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        monitor_html, = await asyncio.gather(
            _g(client.get(append_common_query(f"{NAS_BASE}/zstatus"))),
        )

    perf = get_perf_cached()
    return _templates(request).TemplateResponse(
        request,
        "tab_overview.html",
        {
            **common_ctx(request, cookies),
            "active_tab": "overview",
            "monitor": parse_zstatus(monitor_html.text),
            "perf": perf,
        },
    )


@router.get("/dashboard/storage", response_class=HTMLResponse)
async def tab_storage(request: Request):
    """存储 tab:存储池 + 文件夹浏览 + 文件写测试。"""
    cookies, redirect = require_login(request)
    if redirect: return redirect

    file_path = request.query_params.get("path") or "/sata14/my/data/"
    if not file_path.startswith("/"):
        file_path = "/" + file_path
    if not file_path.endswith("/"):
        file_path = file_path + "/"
    breadcrumb = build_breadcrumb(file_path)

    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        zspool_info, zspool_hw, file_resp = await asyncio.gather(
            _g(nas_get(client, "/zspool/info")),
            _g(nas_get(client, "/zspool/hardware/info")),
            _g(nas_post(client, "/v2/file/list", {
                "folderId": 0,
                "path": file_path,
                "start": 0,
                "num": 200,
                "sortby": "name",
                "order": "asc",
                "show_hidden": 0,
            })),
        )

    # 检查 test 文件夹是否存在(写测试状态)
    test_dir_exists = False
    if file_resp.get("code") == "200":
        for it in (file_resp.get("data", {}).get("list") or []):
            if it.get("name") == "test" and it.get("is_dir") == "1":
                if it.get("path", "").endswith("/备份/test"):
                    test_dir_exists = True
                    break
    if not test_dir_exists:
        async with httpx.AsyncClient(timeout=8, cookies=cookies) as client:
            bak_resp = await nas_post(client, "/v2/file/list", {
                "folderId": 0,
                "path": "/sata14/my/data/备份/",
                "start": 0, "num": 50,
                "sortby": "name", "order": "asc",
                "show_hidden": 0,
            })
        if bak_resp.get("code") == "200":
            for it in (bak_resp.get("data", {}).get("list") or []):
                if it.get("name") == "test":
                    test_dir_exists = True
                    break

    return _templates(request).TemplateResponse(
        request,
        "tab_storage.html",
        {
            **common_ctx(request, cookies),
            "active_tab": "storage",
            "zspool_info": zspool_info,
            "zspool_hw": zspool_hw,
            "file_path": file_path,
            "file_resp": file_resp,
            "breadcrumb": breadcrumb,
            "test_dir_exists": test_dir_exists,
        },
    )


@router.get("/dashboard/zvideo", response_class=HTMLResponse)
async def tab_zvideo(request: Request):
    """极影视 tab:分类列表 + 源目录 + 影视写测试。"""
    cookies, redirect = require_login(request)
    if redirect: return redirect

    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        zvideo_classes, zvideo_dirs = await asyncio.gather(
            _g(nas_post(client, "/zvideo/classification/list", {})),
            _g(nas_post(client, "/zvideo/classification/dirs", {})),
        )

    test_class_exists = False
    if zvideo_classes.get("code") == "200":
        for c in (zvideo_classes.get("data") or []):
            if c.get("name") == "test":
                test_class_exists = True
                break

    return _templates(request).TemplateResponse(
        request,
        "tab_zvideo.html",
        {
            **common_ctx(request, cookies),
            "active_tab": "zvideo",
            "zvideo_classes": zvideo_classes,
            "zvideo_dirs": zvideo_dirs,
            "test_class_exists": test_class_exists,
        },
    )


@router.get("/dashboard/notebook", response_class=HTMLResponse)
async def tab_notebook(request: Request):
    """记事本 tab:总览 metric + 分类侧栏 + 笔记列表 + 写测试区。

    默认取:
    - totalsize (总占用)
    - allclassify (含嵌套的分类树,给侧栏用)
    - list?classify_id=0 (全部笔记,默认视图)
    """
    cookies, redirect = require_login(request)
    if redirect:
        return redirect

    sem = asyncio.Semaphore(1)  # 关键:串行,保 N150
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        totalsize, allclassify, trashcount = await asyncio.gather(
            _g(nas_post(client, "/v2/file/notepad/totalsize", {"location": 2})),
            _g(nas_post(client, "/v2/file/notepad/allclassify", {"location": 2})),
            _g(nas_post(client, "/v2/file/notepad/list", {
                "classify_id": -1, "start": 0, "num": 1, "location": 2,
            })),
        )
        notelist = await nas_post(client, "/v2/file/notepad/list", {
            "classify_id": 0, "start": 0, "num": 50, "location": 2,
        })

    classify_tree = ((allclassify.get("data") or {}).get("list") or []) if str(allclassify.get("code")) == "200" else []
    trash_n = ((trashcount.get("data") or {}).get("total") or 0) if str(trashcount.get("code")) == "200" else 0

    return _templates(request).TemplateResponse(
        request, "tab_notebook.html",
        {
            **common_ctx(request, cookies),
            "active_tab": "notebook",
            "totalsize": totalsize,
            "allclassify_resp": allclassify,
            "classify_tree": classify_tree,
            "trash_count": trash_n,
            "notelist": notelist,
            "current_classify_id": 0,
        },
    )
