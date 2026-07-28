# 在 Hermes Agent 中接入 zspace-nas MCP

Hermes Agent（https://github.com/NousResearch/hermes-agent）原生支持 MCP，
与 Claude Code / Cursor 一样可以直接挂载本项目。

## 1. 安装

```bash
git clone https://github.com/coracoo/zspace_skill.git
cd zspace_skill
uv venv .venv && uv pip install -p .venv/bin/python -e .
cp zspace/.env.example .env   # 编辑填入 NAS_HOST / NAS_USER / NAS_PASSWORD
```

## 2. 推荐：wrapper 脚本（密码不落 Hermes 配置）

MCP server 只读环境变量、自己不加载 .env。与其用 `hermes mcp add --env NAS_PASSWORD=...`
把密码写进 Hermes 配置，不如包一层 wrapper：

```bash
# run-mcp.sh（放在仓库根目录，chmod +x）
#!/bin/bash
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/python -m zspace.mcp_server
```

注册（密码只存在于仓库根目录的 .env，权限建议 600）：

```bash
hermes mcp add zspace-nas --command /path/to/zspace_skill/run-mcp.sh
```

## 3. 首次登录：新设备验证（N001414）

第一次 login 极空间会要求验证新设备，两条路任选：

- **免短信（推荐）**：浏览器打开 `http://<NAS_IP>:5055` 登录，
  F12 → Application → Cookies → 复制 `device_id` 的值，填进 `.env` 的 `NAS_DEVICE_ID=`。
  该 device_id 已是可信设备，MCP 直接复用，无需再验证。
- **短信**：浏览器登录时完成短信验证（把这台"设备"加入可信列表）后重试。

验证完成后无需重启 Hermes，tools 是 lazy-login，下次调用自动成功。

## 4. 验证

```bash
hermes mcp list          # 应看到 zspace-nas 及 90 个 tools
```

或直接对话："看一下 NAS 的存储池状态"（触发 `storage_*` 只读工具）。

## 5. 注意

- 读工具（存储/极影视/记事本/状态）开箱即用；写工具（46 个）建议先在
  Hermes 的工具审批里保持确认，熟悉后再放开。
- `perf_snapshot` 需要 SSH 凭据：推荐 `NAS_SSH_KEY=私钥路径`
  （密码方式 `KEY_SSH` 依赖 sshpass 且密码会进进程列表）。
- RAG 三个工具需要另行部署 nas-rag-server（见 README）。
