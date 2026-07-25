# bge-small-zh-v1.5 RAG server
# 镜像大小目标 ~500MB(python slim + fastembed + sqlite-vec + onnxruntime)
FROM python:3.13-slim

WORKDIR /app

# 系统依赖(sqlite-vec 需要 libsqlite3,slim 默认有)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先 COPY requirements,利用 docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# COPY 代码(把 docs/ tests/ scripts/ 用 .dockerignore 排除)
COPY app/ ./app/

# 数据卷挂载点(由 docker-compose.yml 指定)
VOLUME ["/app/data", "/root/.cache/fastembed"]

EXPOSE 8000

# Healthcheck(只对 daemon 自己生效)
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -fsS http://localhost:8000/status || exit 1

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
