"""极影视 tool 集合(8 个):6 读 + 2 写。

源:mcp_server.py:610-696(读) + 1108-1166(写)
"""
from mcp_server import main as _main
from mcp_server.main import mcp
from mcp_server.perf import _to_json


# ---- 极影视读(6)----
@mcp.tool()
async def list_video_classes() -> str:
    """极影视所有分类(电影/电视剧/动画/test 等)。

    返回结构:
      data: NAS 原始数组(每个含 is_system / is_enable / collection_count 等)
      summary: 状态摘要 — enabled/disabled 计数 + 禁用分类 ID 列表
        - 如果有 disabled,把名字打印出来(很可能是用户主动关的,挪 collection 别挪过去)
    """
    r = await _main.nas.post("/zvideo/classification/list", {})
    if not isinstance(r, dict) or str(r.get("code")) != "200":
        return _to_json(r)
    classes = r.get("data") or []
    enabled = [c for c in classes if c.get("is_enable") != 0]
    disabled = [c for c in classes if c.get("is_enable") == 0]
    system = [c for c in classes if c.get("is_system") == 1]
    user = [c for c in classes if c.get("is_system") != 1]
    summary = {
        "total": len(classes),
        "enabled_count": len(enabled),
        "disabled_count": len(disabled),
        "system_count": len(system),
        "user_count": len(user),
        "disabled_ids": [c.get("id") for c in disabled],
        "disabled_names": [c.get("name") for c in disabled],
        "warning": None,
    }
    if disabled:
        sys_disabled = [c.get("name") for c in disabled if c.get("is_system") == 1]
        user_disabled = [c.get("name") for c in disabled if c.get("is_system") != 1]
        bits = []
        if sys_disabled:
            bits.append(f"系统内置 {sys_disabled} 已被关闭")
        if user_disabled:
            bits.append(f"用户分类 {user_disabled} 已禁用")
        summary["warning"] = "; ".join(bits) + " — 操作这些分类前先确认是不是故意的"
    return _to_json({"data": classes, "summary": summary})


@mcp.tool()
async def get_video_classification_state(classification_id: str) -> str:
    """查单个极影视分类的状态(UUID → 详情)。

    返回: 原始 classification dict + 一个 ok 字段(校验该 ID 是否存在且 is_enable 状态)
    用法: LLM 在调 `link_folder_to_classification` 前先确认目标分类没被禁用
    """
    r = await _main.nas.post("/zvideo/classification/list", {})
    if not isinstance(r, dict) or str(r.get("code")) != "200":
        return _to_json({"error": f"NAS list failed: {r.get('code')} {r.get('msg')}"})
    classes = r.get("data") or []
    target = next((c for c in classes if c.get("id") == classification_id), None)
    if not target:
        return _to_json({"error": f"classification_id={classification_id} not found in NAS ({len(classes)} classes total)"})
    return _to_json({
        "ok": True,
        "data": target,
        "is_enable": target.get("is_enable"),
        "is_system": target.get("is_system"),
        "warning": "⚠️ 该分类已被禁用(is_enable=0) — 不要把目录关联到这个分类" if target.get("is_enable") == 0 else None,
    })


@mcp.tool()
async def latest_movies() -> str:
    """极影视最新入库合集(首页"最新",20 部)。"""
    return _to_json(await _main.nas.post("/zvideo/home/collection/latest", {}))


@mcp.tool()
async def suggested_movies() -> str:
    """极影视推荐合集(首页"推荐",20 部)。"""
    return _to_json(await _main.nas.post("/zvideo/home/collection/suggested", {}))


@mcp.tool()
async def random_movies() -> str:
    """极影视随机推荐(12 部,每次结果不同,适合"不知道看啥")。"""
    return _to_json(await _main.nas.post("/zvideo/video/randomlist", {}))


@mcp.tool()
async def list_video_dirs() -> str:
    """极影视源目录(扫描影视内容的源文件夹)。"""
    return _to_json(await _main.nas.post("/zvideo/classification/dirs", {}))


# ---- 极影视写(2,⚠️ 真实落盘)----
@mcp.tool()
async def add_video_classification(
    name: str, file_path: str = "", not_scrape: int = 1
) -> str:
    """⚠️ 写入:在极影视新建一个分类(如"动漫""纪录片")。
    name: 分类名,如 test
    file_path: 关联目录(可选,实测 NAS 不会真的关联,需要单独调 link_folder_to_classification)
    not_scrape: 1=不刮削(推荐测试用,避免 NAS 跑去 TMDB 查询);0=刮削"""
    form = {
        "classification_name": name,
        "share_users": "[]",
        "not_scrape": not_scrape,
    }
    if file_path:
        form["file_path"] = file_path
    return _to_json(await _main.nas.post("/zvideo/classification/add", form))


@mcp.tool()
async def link_folder_to_classification(
    classification_id: str, file_path: str
) -> str:
    """⚠️ 写入:把目录关联到极影视分类(让分类扫描该目录的影片)。
    classification_id: 分类 UUID(从 list_video_classes 拿)
    file_path: 要关联的目录路径,如 /sata14/my/data/备份/test
    关键:字段名是 file_path[](PHP 数组语法),这里自动处理。
    ⚠️ **状态校验**:目标分类 is_enable=0 时直接拒绝,不发请求到 NAS。
      用 `get_video_classification_state(classification_id)` 先确认状态。
    返回 N120019 = 已经关联过(也算成功)。"""
    # 状态校验:目标分类被禁用 → 拒绝;无法校验 → 默认拒绝(fail-closed)
    list_resp = await _main.nas.post("/zvideo/classification/list", {})
    if not (isinstance(list_resp, dict) and str(list_resp.get("code")) == "200"):
        return _to_json({
            "error": "无法校验分类状态,拒绝执行写入(避免关联到禁用分类)",
            "hint": "稍后重试;若持续失败,检查 NAS 连接或 token",
            "list_resp": list_resp,
        })
    target = next(
        (c for c in (list_resp.get("data") or [])
         if c.get("id") == classification_id),
        None,
    )
    if target is None:
        return _to_json({
            "error": f"classification_id={classification_id} 不存在",
            "hint": "调 list_video_classes 拿有效 ID",
        })
    if target.get("is_enable") == 0:
        return _to_json({
            "error": f"分类 '{target.get('name')}' 已被禁用(is_enable=0),不接受关联",
            "hint": "这是用户主动关的。要恢复关联请先在 pcweb UI 把它打开。",
            "classification": target,
        })
    # 字段名带 [],直接传 dict(NAS PHP 解析为数组)
    return _to_json(await _main.nas.post("/zvideo/classification/increase", {
        "classification_id": classification_id,
        "file_path[]": file_path,
    }))
