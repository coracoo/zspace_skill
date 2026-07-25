"""FastAPI app 入口:create_app + middleware + 注册 router。

兼容性:
- `uvicorn app:app` 从项目根启动(原行为,app.py shim 也指向这)
- `uvicorn app.main:app` 等价
- 模板目录是项目根的 templates/,靠 cwd 解析(不要搬进 app/)

循环 import 拓扑:
    app/__init__.py → app/main.py → app/routes/*.py → app/{deps,nas_helpers,...}.py
    routes 文件互相不依赖,所以 include_router 顺序无关。
"""
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from dashboard.app.routes import auth, dashboard, files, notebook, proxy, shortcut, zvideo
from dashboard.app.zstatus import datetime_local, fmt_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zspace-poc")

# 会话签名密钥:优先从环境变量读(生产必须设);未设则每次启动随机生成并警告
# (随机生成意味着重启后所有已登录会话失效,且多 worker 部署会话不共享——
#  这是有意的"失败可见"行为,比硬编码公开常量安全)。
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)
    log.warning(
        "SESSION_SECRET 未设置,已生成临时随机密钥(重启后所有会话失效,生产请务必设置 SESSION_SECRET 环境变量)"
    )


def create_app() -> FastAPI:
    """构造 FastAPI 实例,挂 middleware + 注册所有 router + 注册 jinja filter。"""
    app = FastAPI(title="ZSpace NAS MCP PoC")
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

    # Jinja2 templates:项目根的 templates/。靠 uvicorn 启动时的 cwd 解析
    # (uvicorn app:app 是从项目根启动的,所以 templates/ 直接可见)。
    # 同时兜底用本文件位置算绝对路径,确保从其他目录启动也能找到。
    tpl_dir = Path("templates")
    if not tpl_dir.exists():
        tpl_dir = Path(__file__).resolve().parent.parent / "templates"
    templates = Jinja2Templates(directory=str(tpl_dir))
    # 注册原 app.py 里的 jinja filters(原本写在 templates.env.filters[...] = ...)
    templates.env.filters["fmt_bytes"] = fmt_bytes
    templates.env.filters["datetime_local"] = datetime_local
    templates.env.filters["safe_html"] = notebook.safe_html
    app.state.templates = templates

    # 注册 router(顺序无所谓,各文件互相不依赖)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(files.router)
    app.include_router(notebook.router)
    app.include_router(zvideo.router)
    app.include_router(shortcut.router)
    app.include_router(proxy.router)

    return app


app = create_app()
