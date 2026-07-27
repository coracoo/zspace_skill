# Contributing to zspace-mcp-poc

## 开发环境

```bash
git clone <repo>
cp .env.example .env && vi .env  # 填 NAS 连接信息
./start.sh deps                  # pip install -r requirements.txt
```

## 项目结构

参见 [README.md](./README.md) 的「文件路由」节。

## 开发流程

### 加新的 MCP Tool

1. 找到对应域的文件:`zspace/mcp_server/tools/<domain>.py`
2. 加 `@mcp.tool()` 装饰的 async 函数
3. 调 `_main.nas.post("/nas/endpoint", {...})`
4. 用 `_to_json()` 序列化返回
5. 提交前验证:`./start.sh mcp` 启动看 `X tools registered` 数字 +1

```python
# zspace/mcp_server/tools/example.py
from zspace.mcp_server import main as _main
from zspace.mcp_server.main import mcp
from zspace.mcp_server.perf import _to_json

@mcp.tool()
async def my_new_tool(param: str) -> str:
    """Tool 描述(给 LLM 看,决定何时调)。"""
    return _to_json(await _main.nas.post("/your/endpoint", {"key": param}))
```

### 加新的 Skill

1. 创建 `skills/<name>/SKILL.md`(frontmatter 含 `name` + `description` 触发词)
2. (可选)加 Python 脚本做机械活,写操作统一走 MCP tool
3. 参考 `label-manager` 或 `media-organizer` 的格式

### 加新的 NAS API(逆向)

1. SSH 进 NAS,找 openresty 配置:`/zspace/applications/services/openresty/nginx/conf/vhost/`
2. scp pcweb JS bundle 到本地,grep 端点路径
3. 用 `NasClient` 探活:POST + form body,调 `/new/endpoint`
4. 记录到 `API.md` 对应节

## Commit 规范

- `feat:` 新功能(MCP tool / skill / API)
- `fix:` 修 bug
- `refactor:` 重构(不动行为)
- `docs:` 文档
- `chore:` 工具/配置

## 测试

本机冒烟测试(需要 `.env` 有效):

```bash
./start.sh mcp-cfg  # 验证 MCP server 能启动
python -c "from zspace.mcp_server import mcp; print(len(mcp._tool_manager._tools))"  # 应为 90
curl http://localhost:15050/healthz  # 验证 dashboard
```

## 提 PR 前

- [ ] 新增 tool 有 docstring(LLM 根据它决定何时调)
- [ ] 写 tool 有安全门(fail-closed)
- [ ] 没有硬编码敏感信息(device_id/手机号/密码)
- [ ] 新 API 端点已记录到 `API.md`
- [ ] `./start.sh mcp` 启动无报错

## 许可

MIT License。贡献即表示你同意在此许可证下授权你的代码。
