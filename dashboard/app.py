"""Shim — real impl in dashboard/app/ package."""
from dashboard.app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=15050)
