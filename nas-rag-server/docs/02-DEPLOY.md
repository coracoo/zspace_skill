# 02 - DEPLOY

NAS docker 部署步骤(N150,Intel 极空间 Z4Pro,Linux 5.19)。

## 0. 前置

- NAS 上已装 docker(极空间自带,或手动装 docker.io)
- NAS 上能 SSH(用户名同 .env 的 NAS_USER)
- 本机已下 bge 模型(`~/.cache/fastembed/`,~100MB)
- NAS 有 `~/.cache/` 或 `/zspace/zsrp/` 写入权限

## 1. 同步模型 cache(免 NAS 重新下)

```bash
# 从本机 ~/.cache/fastembed 同步到 NAS
sshpass -p "$KEY_SSH" scp -r ~/.cache/fastembed/ \
    user@nas:/zspace/zsrp/fastembed/

# 验证
sshpass -p "$KEY_SSH" ssh user@nas \
    "ls /zspace/zsrp/fastembed/models--BAAI--bge-small-zh-v1.5/"
```

## 2. 拷贝项目到 NAS

```bash
# 整个 nas-rag-server/ 目录(自包含,不依赖主项目其他代码)
scp -r nas-rag-server/ user@nas:/zspace/zsrp/nas-rag-server/

# SSH 进去验证结构
ssh user@nas
cd /zspace/zsrp/nas-rag-server
ls
# 应该有 app/ Dockerfile docker-compose.yml requirements.txt docs/
```

## 3. Docker 构建 + 启动

```bash
cd /zspace/zsrp/nas-rag-server
docker compose build
docker compose up -d

# 查看日志
docker compose logs -f
# 应该看到:
# INFO:     Started server process [1]
# INFO:     Uvicorn running on http://0.0.0.0:8000
# INFO:loaded embedder model BAAI/bge-small-zh-v1.5 (first call downloads ~100MB to ~/.cache/fastembed/)
```

`docker-compose.yml` 关键配置:
```yaml
services:
  nas-rag:
    build: .
    container_name: nas-rag
    restart: unless-stopped
    volumes:
      - /zspace/zsrp/rag.db:/app/data/rag.db   # 索引数据持久化
      - /zspace/zsrp/fastembed:/root/.cache/fastembed   # 模型 cache(从本机 sync)
    ports:
      - "127.0.0.1:8000:8000"   # 只 loopback 监听(用 openresty 反代)
    environment:
      - RAG_DB_PATH=/app/data/rag.db
      - RAG_MODEL_CACHE=/root/.cache/fastembed
```

## 4. openresty 反代(让本机 MCP 能 HTTP 调)

NAS 上 `/usr/openresty/nginx/conf/vhost/` 加 `8100_zspace_rag.conf`:

```nginx
server {
    listen 8100;
    listen [::]:8100;
    access_log /zspace/applications/logs/openresty/access_zspace_rag.log main;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # 30s timeout(embed 可能慢)
        proxy_read_timeout 30s;
    }
}
```

reload openresty:
```bash
/usr/openresty/nginx/sbin/nginx -s reload
# 或者 docker 重启 openresty 容器(取决于极空间部署方式)
```

## 5. 验证

```bash
# 测 daemon 直接(不经过 openresty)
curl -s http://127.0.0.1:8000/status

# 测 openresty 反代(从本机或 NAS 上)
curl -s http://nas:8100/status

# 试 search
curl -s -X POST http://nas:8100/search \
    -H "Content-Type: application/json" \
    -d '{"query": "一年级教材", "scope": "files", "top_k": 5}'
```

## 6. systemd 管理(可选,推荐)

NAS 端 systemd 文件 `/etc/systemd/system/zspace-rag.service`:

```ini
[Unit]
Description=nas-rag-server (bge-small-zh-v1.5 RAG)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/zspace/zsrp/nas-rag-server
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

启用:
```bash
systemctl daemon-reload
systemctl enable zspace-rag.service
systemctl start zspace-rag.service
# 开机自启,跟随 docker
```

## 7. 触发首次 reindex

```bash
# 阻塞等完成(几十分钟,看 NAS 文件数)
curl -X POST http://nas:8100/reindex \
    -H "Content-Type: application/json" \
    -d '{"scope": "files", "full": true}'

# 或后台任务(返回 task_id)
curl -X POST http://nas:8100/reindex \
    -H "Content-Type: application/json" \
    -d '{"scope": "files", "full": true, "async": true}'
# 后续 GET /reindex/status/{task_id} 查进度
```

## 8. NAS 文件挂载与扫描路径

`/zspace/zsrp/nas-rag-server/app/config.py`:
```python
DEFAULT_SCAN_ROOTS = [
    "/sata14/my/data/",
    "/sata15/my/data/",
    "/sata16/my/data/",
    "/sdb1/my/data/",
]
```

## 9. 备份

NAS 端 cron 每天备份 sqlite:
```cron
# /etc/cron.d/zspace-rag-backup
0 3 * * * root cp /zspace/zsrp/rag.db /zspace/zsrp/rag.db.bak.\$(date +\%Y\%m\%d)
```

保留最近 7 天,删老的。

## 10. 迁移到其他 NAS

```bash
# 在新 NAS 上:
# 1. 复制 nas-rag-server/ + rag.db + fastembed/ 三个东西
# 2. docker compose up
# 3. 改 openresty 反代
```

主项目侧只需改 `NAS_RAG_URL` env(从极空间 IP 改 Synology IP)。

## 已知风险

- **bge 模型 100MB**,镜像内没带(从本机 sync),若 sync 失败 daemon 启动会卡在下载
- **N150 内存 4-8GB**,docker 限制 `--memory 1G`,OOM 时 daemon 挂
- **openresty 重启** 会断开服务,daemon 不受影响
- **sqlite 单文件** 无并发保护,daemon 写时别同时手动 sqlite 操作