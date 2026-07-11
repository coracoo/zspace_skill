"""FastAPI app 包。

兼容旧 import:`uvicorn app:app` 仍 work(`app.py` shim 也保留)。
"""
from app.main import app, create_app

__all__ = ["app", "create_app"]
