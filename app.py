"""薄入口 shim — 真正的实现已搬到 app/ 包。

保留是为了:
1. uvicorn app:app 启动方式不变(start.sh 用的就是它)
2. 避免任何旧 import `from app import xxx` 挂掉
"""
from app.main import app, create_app

__all__ = ["app", "create_app"]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
