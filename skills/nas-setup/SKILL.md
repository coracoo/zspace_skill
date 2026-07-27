---
name: nas-setup
description: Use when 用户说"连不上 NAS"、"检查 NAS 连接状态"、"NAS 环境有问题"、任何 MCP tool 返回 N001208/N001414 等鉴权错误、skill 跑之前先确认环境。所有其他 NAS skill 的第一步都应该先调此 skill。
  触发词:检查 NAS、NAS 环境、连不上 NAS、NAS 连接状态、setup nas、NAS 配置检查、check nas。
  不适用:不是解决具体业务问题的 skill — 只做环境校验;具体功能用对应的 skill。
---

# NAS Setup — 环境检查 & 登录验证

## 概述

在任何 NAS 操作之前,**必须先跑这个前置检查**。它验证:
1. `.env` 是否配置完整
2. NAS 能否登录(鉴权链是否通)
3. device_id 是否有效(N001414 短信验证绕过了吗)
4. RAG daemon 是否在线(需要 RAG 的 skill 才检查)

## 工作流

### 前置检查(每次被触发时执行)

**方式 1(推荐)**: exec 自动化脚本,一次性输出全部结果
```
python skills/nas-setup/scripts/check.py
```

**方式 2**: Agent 手动逐项检查(脚本无法运行时备用)
1. **检查 `.env` 配置** — Agent 读 .env,检查 NAS_HOST/NAS_USER/NAS_PASSWORD 非空
2. **验证 NAS 登录** — 调 list_files MCP tool
3. **验证 RAG daemon** — 调 index_status() MCP tool

### 如果 .env 缺变量(Agent 交互修复)

check.py 返回 `❌ .env 配置: 不完整` 时,Agent **主动逐条问用户**,不要等用户去编辑:

```
Agent: "NAS 连接信息缺 2 项:
  1. NAS_HOST — 极空间的 IP 地址是什么？
  2. NAS_PASSWORD — 登录密码是什么？
  请依次告诉我。"
用户: "192.168.1.100"
Agent: "收到 NAS_HOST=192.168.1.100。密码呢？"
用户: "mypassword"
Agent: [编辑 .env 文件,写入 NAS_HOST 和 NAS_PASSWORD]
Agent: [重跑 check.py]
"✅ .env 配置: 完整。剩下 NAS 登录... [继续验证]"
```

**关键规则**:
- 不要只打印"请编辑 .env"就完事 — **必须逐条交互问用户**
- 密码类变量(NAS_PASSWORD/KEY_SSH)不回显
- 写入 .env 后立即重跑 check.py 验证

### 检查结果解读
   ```
   ✅ NAS 登录:通(用户 xxx)
   ✅ device_id:有效
   ✅ RAG daemon:在线(391 chunks, last_reindex xxx)
   
   或:
   ❌ NAS 登录: N001414 短信验证 — 把真实 device_id 填入 .env
   ⚠️ RAG daemon:未运行 — label-manager scan 按文件名匹配
   ```

## 跟其他 skill 的关系

所有其他 skill(SKILL.md)的第一步应引用:

```
**前置**:先调 nas-setup 检查环境。REQUIRED: 调 whoami() 验证登录,调 index_status() 验证 RAG。
```

## 故障排查

| 现象 | 处理 |
|---|---|
| `.env` 找不到 | `cp .env.example .env` |
| `NAS_USER/NAS_PASSWORD` 为空 | 编辑 `.env` 填入 |
| whoami() 返回 N001414 | 把已登记的真实 device_id 填入 `.env` 的 `NAS_DEVICE_ID`(32 字符 hex) |
| whoami() 超时 | 检查 `NAS_HOST` 是否正确,`ping <nas_ip>` |
| index_status() 超时 | nas-rag docker 没跑: `cd rag-server && docker compose up -d` |