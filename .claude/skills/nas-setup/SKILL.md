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

1. **检查 `.env` 配置**(必需,Agent 自己读文件)
   ```
   读 .env,检查以下关键变量是否非空:
     NAS_HOST  NAS_USER  NAS_PASSWORD  NAS_DEVICE_ID
   ```
   - 缺少任何一项 → 告诉用户:"请在 .env 里填 xxx"
   - `.env` 文件不存在 → "请 cp .env.example .env 并填入真实值"

2. **验证 NAS 登录**
   ```
   调 whoami() MCP tool
   ```
   - code=200 → 登录通 ✅
   - N001208 → token 失效,NasClient 会自动重登(正常,忽略)
   - N001414 → device_id 不在 NAS 已登记列表,"把真实 device_id 填入 .env 的 NAS_DEVICE_ID"
   - N001200 → RSA 公钥不对(NAS 固件版本跟公钥不匹配),"更新 nas/auth.py 的公钥"
   - 超时/连不上 → "检查 NAS_HOST 是否正确,网络是否通"

3. **验证 RAG daemon**(可选,需要 RAG 的 skill 触发时才执行)
   ```
   调 index_status() MCP tool
   ```
   - 返回 model/chunks → daemon 在线 ✅
   - 超时/error → "RAG 语义搜索不可用(daemon 没跑)。跑 `cd nas-rag-server && docker compose up -d` 启动"

4. **汇总报告**
   ```
   ✅ NAS 登录:通(用户 xxx)
   ✅ device_id:有效
   ✅ RAG daemon:在线(391 chunks, last_reindex xxx)
   
   或:
   ❌ NAS 登录: N001414 短信验证 — 把真实 device_id 填入 .env
   ⚠️ RAG daemon:未运行 — smart-tagger 降级到文件名匹配
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
| index_status() 超时 | nas-rag docker 没跑: `cd nas-rag-server && docker compose up -d` |