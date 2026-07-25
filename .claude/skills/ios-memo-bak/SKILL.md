---
name: ios-memo-bak
description: Use when 用户想把 iPhone 备忘录自动备份到 NAS 记事本 — "帮我设置 iPhone 备忘录同步"、"重新告诉我一下怎么配"、"换一个新的备份密钥"、"我同事也要用这个功能"。
  触发词:iPhone 备忘录同步、ios 备忘录 bak、备忘录备份到 NAS、备忘 → NAS、iPhone notepad sync、ios 备忘录、备忘录同步、iphone shortcut 备份、iPhone 推笔记、给我配 iPhone 备份、帮我把备忘录同步到 NAS。
  不适用:不是直接写 NAS 的 skill — 实际写由 iPhone Shortcut 发起;此 skill 只设置基础设施(生成密钥 + 重启 dashboard + 给配置步骤)。
---

# iOS Memo Backup — iPhone 备忘录 → NAS 记事本 同步

## 概述

**前置**:先调 `nas-setup` skill 验证 .env 配置 + NAS 登录。没有 SHORTCUT_KEY→setup.py 自动生成。

通过 iOS「快捷指令」(Shortcuts) + NAS dashboard 的 `/shortcut/notepad` 端点,实现备忘录实时备份到 NAS 独立记事本(`location=2`,不污染系统默认记事本)。

**Skill 只做基础设施配置**(生成密钥 + 重启 dashboard + 验证 + 给配置步骤);实际写由 iPhone Shortcut 在用户每次"分享→推"时发起。

## 工作流

### 场景 1:首次配置(用户触发词:"帮我设置 iPhone 备忘录备份")

1. **检查现有 `SHORTCUT_KEY`**:`python scripts/setup.py` 自动处理
   - 有 key → 问要不要重新生成
   - 没 key → 生成 32 hex 随机密钥
2. **写进 `.env`**(覆盖或追加)
3. **重启 dashboard**:`./start.sh dashboard`(自动 source .env)
4. **验证端点**:模拟 iPhone curl,确认 200 OK + 测试笔记已建
5. **直接给出 iPhone Shortcut 配置步骤**(Agent 必须返回这些信息给用户):
   - POST URL: `http://<NAS_LAN_IP>:<port>/shortcut/notepad`
   - Header: `X-Shortcut-Key: <key>`(完整密钥,不遮蔽)
   - Header: `Content-Type: application/json`
   - Body: `{"title": "...", "body": "<html>"}`
   - 动作链:6 步(共享表单输入 → 字典 → JSON → 获取 URL 内容 → 显示通知)

### 场景 2:已配置(用户触发词:"再告诉我一下 iPhone Shortcut 怎么配"或"我同事也要用")

1. 读 `.env` 拿现有 key(直接显示,完整密钥不遮蔽 —— 因为 iPhone Shortcut 端需要)
2. 拿端口(从 `app/main.py` uvicorn 启动行读,默认 8000)
3. 给 iPhone Shortcut 配置步骤

### 场景 3:重新生成 key(用户触发词:"换一个新密钥")

1. 跑 `python scripts/setup.py --force`(强制重新生成)
2. 用户需要更新所有 iPhone Shortcut 的 `X-Shortcut-Key` header

## 服务端代码位置(供排查)

- `app/routes/shortcut.py:shortcut_notepad` — 接收端点
- `app/cocoa.py:_cocoa_html_to_clean` — Cocoa HTML 解析
- 鉴权逻辑(`SHORTCUT_KEY` / `ALLOW_OPEN_SHORTCUT=1` / `X-Shortcut-Key` header)

## 关键约束(必读)

1. **密钥必须从 .env 读,不能猜** — `.env` 是 SOT
2. **端口必须从 app/main.py 读** — 默认 8000,但用户可能改了
3. **NAS_LAN_IP 从 .env NAS_HOST 读** — 占位符(极空间 IP)要替换
4. **重启 dashboard 必须用 `./start.sh`** — 它会 `source .env`,手动 `uvicorn` 启动读不到 env
5. **密钥直接完整显示给用户** — 不遮蔽(因为配置 Shortcut 必须要完整密钥)

## 已知 gap

- LAN-only(无 HTTPS,密钥明文传输)— 不在公网用
- iOS 12+(备忘录 share sheet 支持 Shortcut)
- 一次性设置:换路由器 / NAS IP 变化需要重新跑
- 多用户场景:每人有独立 iPhone 配同一个 key → 安全可接受(LAN + 预共享密钥)

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| iPhone POST 一直 403 | Shortcut 没配 `X-Shortcut-Key` header,或 `.env` 里 key 是空的 | 重跑 setup,确认 key 已写进 .env 且 dashboard 已重启 |
| iPhone POST 401 invalid key | Shortcut 配的 key 跟 .env 不匹配(可能改了 .env 没重启) | 重启 dashboard |
| 验证 curl 返回 "Connection refused" | dashboard 没起,看 PID 文件 | `./start.sh dashboard` |
| 验证 curl 返回 "Connection refused" 且刚才说有重启 | uvicorn 没真正加载 env(用 start.sh 不用手动 uvicorn) | 重启 |
| 笔记内容样式全没了 | body 不是 HTML 是纯文本 | Shortcut 用"获取备忘录 URL"或"分享表单"而非"输入纯文本" |