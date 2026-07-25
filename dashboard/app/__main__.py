"""`python -m dashboard.app` 入口(替代旧的 dashboard/app.py shim)。"""
import uvicorn

from dashboard.app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=15050)
