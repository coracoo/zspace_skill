# NAS Setup Skill

前置环境检查:验证 .env / NAS 登录 / device_id / RAG daemon 状态。

## 快速使用

```bash
python skills/nas-setup/scripts/check.py
```

所有其他 NAS skill 的第一步都应该先跑此 skill。脚本会自动检查:
1. `.env` 配置完整(NAS_HOST / NAS_USER / NAS_PASSWORD)
2. Python 依赖(httpx / cryptography)
3. NAS 登录(鉴权链是否通)
4. RAG daemon 在线(可选)

## 依赖

- Python: `httpx`, `cryptography`
- 复用顶层 `nas` 包的 `NasClient`
- 复用 `zspace.mcp_server` 的 `mcp` 实例(检查注册 tool 数)

详见 [`SKILL.md`](SKILL.md)。