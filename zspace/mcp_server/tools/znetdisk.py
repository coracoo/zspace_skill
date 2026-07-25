"""百度网盘 MCP tool(28 个 /znetdisk/* 端点封装)

⚠️ 前置:必须先在 NAS 登录百度网盘账号。两种方式:
1. 跑 `zspace/scripts/netdisk_login.py` 走 OAuth CLI
2. NAS pcweb UI → 应用 → 网盘 → 添加百度网盘

未登录时所有 tool 返回 code="N001013",看到这个就引导用户登录。

端点分组(跟 API.md 6.12 节对应):
- auth(4)— 登录态检查 + token 交换 + 用户信息 + 退出
- file(4)— 云盘文件管理(list/download/upload/newdir)
- task(2)— 下载/上传任务管理
- sync(6)— NAS ↔ 云盘双向同步任务
- autobackup(7)— 自动备份(手机/电脑 → NAS → 云盘)
- share(4)— 分享链接转存(百度独有!别人发的分享 → 我的 NAS)
- fail(1)— 全局失败列表

跳过 4 个 order/membership 端点(商业化,用户场景用不到)。

body 字段标注:
- 【已实测】— 字段名/类型确认
- 【待实测】— 推测字段,登录后跑一次真实请求才能确认

源端点路径:https://<nas_ip>:5055/znetdisk/<group>/<action>
所有 POST + form-urlencoded。
"""
from zspace.mcp_server import main as _main
from zspace.mcp_server.main import mcp
from zspace.mcp_server.perf import _to_json


# ============ Auth(4)============

@mcp.tool()
async def znetdisk_auth_check() -> str:
    """检查百度网盘登录态,未登录返回 OAuth URL。

    返回:
    - 已登录:`{code:"200", data:{is_login:true, url:""}}`
    - 未登录:`{code:"200", data:{is_login:false, url:"https://openapi.baidu.com/oauth/..."}}`

    看到 is_login=false 时,把 data.url 给用户,让他在浏览器打开授权,然后调 znetdisk_auth_token 提交 code。"""
    return _to_json(await _main.nas.post("/znetdisk/auth/check", {}))


@mcp.tool()
async def znetdisk_auth_token(code: str) -> str:
    """⚠️ 写入:用百度 OAuth 授权码换 token 完成登录。

    流程:
    1. 调 znetdisk_auth_check 拿 OAuth URL
    2. 用户浏览器打开 URL → 百度登录授权 → 显示 code
    3. 用 code 调本 tool 完成登录

    code: 百度 OAuth 授权码(约 32 字符)"""
    return _to_json(await _main.nas.post("/znetdisk/auth/token", {"app": "baidu", "code": code}))


@mcp.tool()
async def znetdisk_auth_userinfo() -> str:
    """获取已登录百度网盘账号信息。

    未登录返回 code="N001013"。"""
    return _to_json(await _main.nas.post("/znetdisk/auth/userinfo", {}))


@mcp.tool()
async def znetdisk_auth_logout() -> str:
    """⚠️ 写入:退出百度网盘登录(清除 NAS 上的 refresh_token)。

    退出后所有百度网盘 tool 会返回 N001013。重新登录需要再走 OAuth。"""
    return _to_json(await _main.nas.post("/znetdisk/auth/logout", {}))


# ============ File 操作(4)============

@mcp.tool()
async def znetdisk_file_list(dir: str = "/", start: int = 0, num: int = 200) -> str:
    """列百度网盘的文件/文件夹。

    dir: 云盘绝对路径,默认 "/" 根目录(如 "/我的视频/")
    start: 分页起点(默认 0)
    num: 每页数量(默认 200,上限可能 1000)

    【待实测】dir 字段名可能是 `dir` 或 `path`,登录后跑一次才能确认。"""
    return _to_json(await _main.nas.post("/znetdisk/file/list", {
        "dir": dir, "start": start, "num": num,
    }))


@mcp.tool()
async def znetdisk_file_download(file_path: str, save_path: str) -> str:
    """⚠️ 写入:从百度云盘下载文件到 NAS。

    file_path: 云盘文件路径,多个用英文逗号分隔(如 "/movie/a.mp4,/movie/b.mp4")
    save_path: NAS 本地保存目录(如 "/sata14/my/data/百度下载/")

    【待实测】字段名可能是 `file_path`(单数 form key)或 `file_path[]`(PHP 数组),登录后实测。"""
    return _to_json(await _main.nas.post("/znetdisk/file/download", {
        "file_path": file_path, "save_path": save_path,
    }))


@mcp.tool()
async def znetdisk_file_upload(file_path: str, save_path: str) -> str:
    """⚠️ 写入:把 NAS 文件上传到百度云盘。

    file_path: NAS 本地文件路径,多个用英文逗号分隔
    save_path: 云盘保存目录(如 "/备份/NAS/")

    【待实测】字段名同 znetdisk_file_download。"""
    return _to_json(await _main.nas.post("/znetdisk/file/upload", {
        "file_path": file_path, "save_path": save_path,
    }))


@mcp.tool()
async def znetdisk_file_newdir(dir: str) -> str:
    """⚠️ 写入:在百度云盘新建文件夹。

    dir: 云盘绝对路径(如 "/备份/新文件夹")"""
    return _to_json(await _main.nas.post("/znetdisk/file/newdir", {"dir": dir}))


# ============ Task(2)============

@mcp.tool()
async def znetdisk_task_list() -> str:
    """列百度网盘的下载/上传任务(云盘 ↔ NAS 之间的传输任务)。

    返回包含任务 id、状态、进度、速率等。"""
    return _to_json(await _main.nas.post("/znetdisk/task/list", {}))


@mcp.tool()
async def znetdisk_task_action(task_ids: str, action: str) -> str:
    """⚠️ 写入:对百度网盘任务执行操作(start/stop/delete/pause 等)。

    task_ids: 任务 ID,多个用英文逗号分隔
    action: 操作类型(start/stop/delete/pause/resume)

    【待实测】action 字段的可选值,登录后实测确认。"""
    return _to_json(await _main.nas.post("/znetdisk/task/action", {
        "task_ids": task_ids, "action": action,
    }))


# ============ Sync(6)— NAS ↔ 云盘双向同步 ============

@mcp.tool()
async def znetdisk_sync_list() -> str:
    """列百度网盘的同步任务(NAS 目录 ↔ 云盘目录的镜像)。

    同步任务是持续的:NAS 文件变 → 自动上传;云盘文件变 → 自动下载。"""
    return _to_json(await _main.nas.post("/znetdisk/sync/list", {}))


@mcp.tool()
async def znetdisk_sync_add(
    local_dir: str, remote_dir: str,
    sync_direction: str = "two_way",
) -> str:
    """⚠️ 写入:创建百度网盘同步任务(NAS 目录 ↔ 云盘目录)。

    local_dir: NAS 本地目录(如 "/sata14/my/data/同步/")
    remote_dir: 云盘目录(如 "/同步/")
    sync_direction: 同步方向(two_way / nas_to_cloud / cloud_to_nas)

    【待实测】字段名 + sync_direction 取值,登录后实测。"""
    return _to_json(await _main.nas.post("/znetdisk/sync/add", {
        "local_dir": local_dir, "remote_dir": remote_dir,
        "sync_direction": sync_direction,
    }))


@mcp.tool()
async def znetdisk_sync_home() -> str:
    """百度网盘同步任务主页(汇总:总任务数/活跃/失败等)。"""
    return _to_json(await _main.nas.post("/znetdisk/sync/home", {}))


@mcp.tool()
async def znetdisk_sync_open(id: int) -> str:
    """⚠️ 写入:启用百度网盘同步任务。

    id: 同步任务 ID(从 znetdisk_sync_list 拿)"""
    return _to_json(await _main.nas.post("/znetdisk/sync/open", {"id": id}))


@mcp.tool()
async def znetdisk_sync_close(id: int) -> str:
    """⚠️ 写入:暂停百度网盘同步任务(不删除,可再 open)。"""
    return _to_json(await _main.nas.post("/znetdisk/sync/close", {"id": id}))


@mcp.tool()
async def znetdisk_sync_delete(id: int) -> str:
    """⚠️⚠️ 写入:删除百度网盘同步任务(配置删除,云盘文件保留)。"""
    return _to_json(await _main.nas.post("/znetdisk/sync/delete", {"id": id}))


# ============ AutoBackup(7)— 自动备份(手机/电脑 → NAS → 云盘)============

@mcp.tool()
async def znetdisk_autobackup_info() -> str:
    """百度网盘自动备份任务详情(手机相册备份 / 文件夹自动备份等)。"""
    return _to_json(await _main.nas.post("/znetdisk/autobackup/info", {}))


@mcp.tool()
async def znetdisk_autobackup_add(
    local_dir: str, remote_dir: str, name: str = "",
) -> str:
    """⚠️ 写入:添加百度网盘自动备份任务。

    local_dir: NAS 本地目录(如 "/sata14/my/data/相册/")
    remote_dir: 云盘目录
    name: 任务名称(可选)

    【待实测】字段名 + 完整 body,登录后实测。"""
    body = {"local_dir": local_dir, "remote_dir": remote_dir}
    if name:
        body["name"] = name
    return _to_json(await _main.nas.post("/znetdisk/autobackup/add", body))


@mcp.tool()
async def znetdisk_autobackup_start(id: int) -> str:
    """⚠️ 写入:启动百度网盘自动备份任务。"""
    return _to_json(await _main.nas.post("/znetdisk/autobackup/start", {"id": id}))


@mcp.tool()
async def znetdisk_autobackup_stop(id: int) -> str:
    """⚠️ 写入:暂停百度网盘自动备份任务。"""
    return _to_json(await _main.nas.post("/znetdisk/autobackup/stop", {"id": id}))


@mcp.tool()
async def znetdisk_autobackup_delete(id: int) -> str:
    """⚠️⚠️ 写入:删除百度网盘自动备份任务。"""
    return _to_json(await _main.nas.post("/znetdisk/autobackup/delete", {"id": id}))


@mcp.tool()
async def znetdisk_autobackup_faillist(id: int = 0, start: int = 0, num: int = 100) -> str:
    """百度网盘自动备份失败文件列表。

    id: 任务 ID(0 表示全部任务)
    start/num: 分页"""
    return _to_json(await _main.nas.post("/znetdisk/autobackup/faillist", {
        "id": id, "start": start, "num": num,
    }))


@mcp.tool()
async def znetdisk_autobackup_clear_fail_files(id: int) -> str:
    """⚠️ 写入:清除百度网盘自动备份任务的失败记录。

    id: 任务 ID"""
    return _to_json(await _main.nas.post("/znetdisk/autobackup/clear_fail_files", {"id": id}))


# ============ Share(4)— 分享链接转存(百度独有!)============

@mcp.tool()
async def znetdisk_share_verify(url: str, pwd: str = "") -> str:
    """验证百度网盘分享链接(检查链接有效性 + 提取码)。

    url: 分享链接(如 "https://pan.baidu.com/s/1aBcDeF..." 或短链)
    pwd: 提取码(4 字符,如 "ab12"),无密码留空

    返回:分享内容预览(文件数/大小/标题)。"""
    body = {"url": url}
    if pwd:
        body["pwd"] = pwd
    return _to_json(await _main.nas.post("/znetdisk/share/verify", body))


@mcp.tool()
async def znetdisk_share_filelist(url: str, pwd: str = "") -> str:
    """列百度网盘分享链接里的文件清单(用于挑哪些要转存)。

    url: 分享链接
    pwd: 提取码(无密码留空)

    返回:文件列表(fs_id / name / size / is_dir)。"""
    body = {"url": url}
    if pwd:
        body["pwd"] = pwd
    return _to_json(await _main.nas.post("/znetdisk/share/filelist", body))


@mcp.tool()
async def znetdisk_share_transfer(
    url: str, save_path: str, file_paths: str = "", pwd: str = "",
) -> str:
    """⚠️ 写入:把百度网盘分享的内容**转存到我的百度网盘**(给别人发的链接 → 我的云盘)。

    url: 分享链接
    save_path: 转存到我的云盘的哪个目录(如 "/接收的分享/")
    file_paths: 要转存哪些文件,逗号分隔的路径;留空 = 转存全部
    pwd: 提取码

    转存后云盘有了文件,再调 znetdisk_file_download 才能下载到 NAS。
    【待实测】save_path / file_paths 字段名,登录后实测。"""
    body = {"url": url, "save_path": save_path}
    if file_paths:
        body["file_paths"] = file_paths
    if pwd:
        body["pwd"] = pwd
    return _to_json(await _main.nas.post("/znetdisk/share/transfer", body))


@mcp.tool()
async def znetdisk_share_transfer_result(task_id: int) -> str:
    """查询百度网盘分享转存任务的结果(转存是异步,要查状态)。

    task_id: 从 znetdisk_share_transfer 返回的任务 ID"""
    return _to_json(await _main.nas.post("/znetdisk/share/transfer_result", {"task_id": task_id}))


# ============ Fail(1)============

@mcp.tool()
async def znetdisk_fail_list() -> str:
    """百度网盘全局失败文件列表(所有任务的失败记录汇总)。"""
    return _to_json(await _main.nas.post("/znetdisk/fail/list", {}))
