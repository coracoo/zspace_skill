"""iPhone Shortcut 同步入口(POST /shortcut/notepad)+ PWA(GET /n)。

搬迁自 app.py:788-1301。这两个端点独立于 dashboard 登录,自己有鉴权或开放模式。
Shortcut 用 service-account 风格的 NAS client(env NAS_USER/NAS_PASSWORD),不是 web session。
"""
import logging
import os
import re
import time
from typing import Dict
from urllib.parse import parse_qs, unquote_plus

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.cocoa import cocoa_html_to_clean
from app.nas_helpers import nas_post
from app.shortcut_client import (
    get_shortcut_nas_client,
    reset_shortcut_nas_client,
    title_eq,
)

log = logging.getLogger("zspace-poc")
router = APIRouter()


@router.post("/shortcut/notepad")
async def shortcut_notepad(request: Request):
    """iPhone Shortcut 同步入口(单向 iPhone 备忘录 → NAS 记事本)。

    Headers:
      X-Shortcut-Key: <env SHORTCUT_KEY>  ← 静态预共享密钥,防 LAN 上任意写入

    Body (JSON):
      title (str,必填)
      body  (str,HTML 或纯文本,自动加 <h1>{title}</h1> 前缀)
      classify_id (int,可选,默认 0 = 未分类,必须是叶子分类 id)

    行为:
      - title 已存在(精确匹配已存在笔记标题) → 200, exists=true, 不覆盖
      - 否则 → 调 NAS /v2/file/notepad/new,返回 id

    请求体两种格式都支持:
      - text/plain 或没声明 Content-Type:整段文本就是笔记内容,服务端从第一行抽 title
      - application/json:{"body": "...", "title": "...(可选)", "classify_id": N(可选)}

    安全模式(默认):
      .env 里设 SHORTCUT_KEY=<随机密钥> → 必须带 X-Shortcut-Key 头
      .env 里 SHORTCUT_KEY 留空 → 默认拒绝;设 ALLOW_OPEN_SHORTCUT=1 才开放(仅信任 LAN)

    文档:
      docs/iphone-shortcut.md(iPhone 快捷指令配置步骤)
    """
    # 鉴权:env 设了 SHORTCUT_KEY 必须带正确密钥;留空时默认拒绝,
    # 除非显式设 ALLOW_OPEN_SHORTCUT=1(开放模式,仅信任 LAN)。
    expected = os.environ.get("SHORTCUT_KEY", "").strip()
    got = request.headers.get("X-Shortcut-Key", "").strip()
    if expected:
        if got != expected:
            log.warning("SHORTCUT: invalid/missing key from %s", request.client.host if request.client else "?")
            return JSONResponse({"error": "invalid X-Shortcut-Key"}, status_code=401)
    elif os.environ.get("ALLOW_OPEN_SHORTCUT", "").strip() not in ("1", "true", "yes"):
        log.warning("SHORTCUT: rejected open-mode request (set SHORTCUT_KEY or ALLOW_OPEN_SHORTCUT=1) from %s",
                    request.client.host if request.client else "?")
        return JSONResponse(
            {"error": "shortcut endpoint requires SHORTCUT_KEY or ALLOW_OPEN_SHORTCUT=1"},
            status_code=403,
        )

    # body 解析:JSON / text/plain / 无 Content-Type 都支持
    content_type = request.headers.get("Content-Type", "").lower()
    body = ""
    classify_id = 0
    title = ""
    # 调试:把每次请求的原始内容记到文件(默认关,设 SHORTCUT_DEBUG=1 开启)
    if os.environ.get("SHORTCUT_DEBUG"):
        try:
            _raw_dbg = await request.body()
            _decoded_dbg = _raw_dbg.decode("utf-8", errors="replace")
            # form-urlencoded 形式(=XXXXX),剥前缀 = 解码 URL → 原始 body
            if content_type.startswith("application/x-www-form-urlencoded"):
                _parsed = parse_qs(_decoded_dbg, keep_blank_values=True)
                # 找第一个非空 value 当作真实 body
                _body_dbg = ""
                for k in ("body", "text", ""):
                    if _parsed.get(k) and _parsed[k][0]:
                        _body_dbg = _parsed[k][0]
                        break
                if not _body_dbg:
                    _body_dbg = unquote_plus(_decoded_dbg.lstrip("="))
            else:
                _body_dbg = _decoded_dbg
            with open("/tmp/shortcut_debug.log", "a") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                f.write(f"client: {request.client.host if request.client else '?'}\n")
                f.write(f"Content-Type: {content_type!r}\n")
                f.write(f"raw body: {len(_raw_dbg)} bytes, decoded body: {len(_body_dbg)} chars\n")
                f.write(f"--- DECODED body (full) ---\n")
                f.write(_body_dbg)
                f.write("\n--- END ---\n")
        except Exception as _e:
            with open("/tmp/shortcut_debug.log", "a") as f:
                f.write(f"debug log failed: {_e}\n")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        body = payload.get("body", "") or payload.get("text", "") or ""
        title = (payload.get("title") or "").strip()
        classify_id = int(payload.get("classify_id") or 0)
    elif "application/x-www-form-urlencoded" in content_type or content_type == "application/x-www-form-urlencoded":
        # iOS Shortcut 「获取 URL 内容」+ 请求正文=文本 实际发的是 form-urlencoded
        # 格式通常是 =<整段文本>(空 key 的单字段)
        raw = await request.body()
        body_text = raw.decode("utf-8", errors="replace")
        parsed = parse_qs(body_text, keep_blank_values=True)
        # 优先 body/text 命名字段;否则取第一个 value;最后兜底用整段解码文本
        body = ""
        for k in ("body", "text", ""):
            if parsed.get(k):
                body = parsed[k][0]
                break
        if not body:
            body = body_text
        title = (request.query_params.get("title") or "").strip()
        try:
            classify_id = int(request.query_params.get("classify_id") or 0)
        except ValueError:
            classify_id = 0
    else:
        # text/plain 或其他:整段就是笔记内容
        raw = await request.body()
        body = raw.decode("utf-8", errors="replace")
        # 可选 query 参数 ?title=...&classify_id=N
        title = (request.query_params.get("title") or "").strip()
        try:
            classify_id = int(request.query_params.get("classify_id") or 0)
        except ValueError:
            classify_id = 0

    # ----- Cocoa HTML 检测 + 转干净 HTML -----
    # iOS Shortcut 「用多信息文本制作 HTML」输出 Cocoa HTML Writer 风格的完整 HTML 文档,
    # 极空间记事本只认简单标签(不认 .AppleSystemUIFont / class="p1/s1"/ inline CSS),
    # 原样存会导致 emoji 字体缺失、表格无边框、标题样式失效。
    # 检测到特征就转成 <h1>/<h2>/<h3>/<p>/<table border="1"> 的干净 HTML。
    if "Cocoa HTML Writer" in body or ".AppleSystemUIFont" in body:
        body = cocoa_html_to_clean(body)

    # ----- UTF-8 emoji → 数字 HTML entity(确保 app 详情能渲染)-----
    # ZSpace app 列表摘要(in_brief)能正确显示 UTF-8 emoji,但详情 body 渲染对
    # UTF-8 emoji 字体回退失败导致不显示;反之 app 详情能正确渲染 &#数字; 形式的 entity。
    # 服务端落 NAS 前只把 emoji 范围字符转成 &#数字; entity,中文/标点不动。
    # emoji Unicode 范围: 主要在 U+1F300-U+1FAFF(补充符号 + 表情)+ U+2600-U+27BF(杂项符号)
    # + U+1F000-U+1F1FF(麻将/扑克等)+ U+1F900-U+1F9FF(补充符号和象形文字)
    def _encode_entity(m: "re.Match[str]") -> str:
        return f"&#{ord(m.group(0))};"
    _emoji_pattern = (
        r"[\U0001F000-\U0001F02F"          # 麻将牌
        r"\U0001F0A0-\U0001F0FF"          # 扑克牌
        r"\U0001F100-\U0001F1FF"          # 封闭字母数字补充
        r"\U0001F200-\U0001F2FF"          # 封闭表意文字补充
        r"\U0001F300-\U0001F5FF"          # 符号和象形文字
        r"\U0001F600-\U0001F64F"          # 表情
        r"\U0001F680-\U0001F6FF"          # 交通和地图
        r"\U0001F700-\U0001F77F"          # 炼金术
        r"\U0001F780-\U0001F7FF"          # 几何形状扩展
        r"\U0001F800-\U0001F8FF"          # 补充箭头-C
        r"\U0001F900-\U0001F9FF"          # 补充符号和象形文字
        r"\U0001FA00-\U0001FA6F"          # 棋盘符号
        r"\U0001FA70-\U0001FAFF]"         # 符号和象形文字扩展-A
    )
    body = re.sub(_emoji_pattern, _encode_entity, body)

    # title 为空时,优先从 HTML body 的 <h1> 抽;否则从第一非空行抽(纯文本路径)。
    # 同时把抽出来的"标题源"从 body 里去掉,避免 <h1> 标题 + body 首行重复。
    if not title:
        stripped_body = body.strip()
        if stripped_body.startswith("<"):
            # 富文本路径:iOS Shortcut 「用多信息文本制作 HTML」送来的 HTML
            m = re.search(
                r"<h1[^>]*>(.*?)</h1>", stripped_body, re.DOTALL | re.IGNORECASE
            )
            if m:
                # 抽 <h1> 内部纯文本(去嵌套标签)做 title
                inner = re.sub(r"<[^>]+>", "", m.group(1))
                title = inner.strip()[:200]
                # 把这个 <h1> 整段从 body 里删掉(避免和服务端自动加的 h1 重复)
                body = (stripped_body[:m.start()] + stripped_body[m.end():]).strip()
            else:
                # 没 <h1>:取全部可见文本前 200 字符
                text = re.sub(r"<[^>]+>", " ", stripped_body)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    title = text[:200]
        else:
            # 纯文本路径(原有逻辑)
            first_line = ""
            rest_lines = []
            found = False
            for line in body.splitlines():
                stripped = line.strip()
                if not found and stripped:
                    first_line = stripped[:200]
                    found = True
                    continue  # 跳过第一行,不进 rest_lines
                rest_lines.append(line)
            if first_line:
                title = first_line
                body = "\n".join(rest_lines).lstrip("\n")
        if not title:
            return JSONResponse({"error": "title required (and body has no first line to derive from)"}, status_code=400)
    if len(title) > 200:
        return JSONResponse({"error": "title too long (max 200 chars)"}, status_code=400)
    if len(body) > 500_000:
        return JSONResponse({"error": "body too long (max 500KB)"}, status_code=413)

    client = await get_shortcut_nas_client()
    if client is None:
        return JSONResponse(
            {"error": "NAS login failed (check NAS_USER/NAS_PASSWORD env on host)"},
            status_code=502,
        )

    # 1) 同名查重(精确匹配 title)
    # NAS /v2/file/notepad/searchnotepad 返回 data.list(嵌套 dict),title 会被裹 "..." 标记
    search_resp = await nas_post(client, "/v2/file/notepad/searchnotepad", {
        "keyword": title, "num": 10, "location": 2,
    })
    # token 失效(N001208):重置缓存 client 重登一次,再重试 search
    if str(search_resp.get("code")) == "N001208":
        await reset_shortcut_nas_client()
        client = await get_shortcut_nas_client()
        if client is not None:
            search_resp = await nas_post(client, "/v2/file/notepad/searchnotepad", {
                "keyword": title, "num": 10, "location": 2,
            })
    if str(search_resp.get("code")) == "200":
        data = search_resp.get("data") or {}
        notes = data.get("list") if isinstance(data, dict) else data
        for note in notes or []:
            if not isinstance(note, dict):
                continue
            raw_title = (note.get("title") or "")
            # 去 NAS 标记 ...(实测可能是开头 + 收尾两个字符)
            stripped = raw_title.replace("\x01", "").replace("\x02", "").strip()
            if title_eq(stripped, title):
                return JSONResponse({
                    "ok": True,
                    "exists": True,
                    "id": note.get("id"),
                    "skipped_reason": "title already exists (overwrite disabled by user policy)",
                })

    # 2) 自动加 h1 前缀(防 body 对但 NAS 存空的坑)
    # 已含 <h1>(title 完全一致 OR iOS 富文本路径送来的 HTML,容忍带属性)就不重复加
    body_starts = body.lstrip()
    low = body_starts[:4].lower()
    after = body_starts[4:5]  # <h1 后面第 5 个字符
    # 匹配 <h1> 或 <h1 ...>(容忍 class/style 等属性)
    already_has_h1 = low == "<h1>" or (low == "<h1" and after in (" ", "\t", "\n", ">"))
    exact_h1_match = body_starts.startswith(f"<h1>{title}</h1>")
    if not already_has_h1 and not exact_h1_match:
        body = f"<h1>{title}</h1>\n{body}"

    # 3) 写入
    new_resp = await nas_post(client, "/v2/file/notepad/new", {
        "title": title, "body": body, "classify_id": classify_id, "location": 2,
    })
    if str(new_resp.get("code")) != "200":
        return JSONResponse({
            "ok": False,
            "exists": False,
            "error": new_resp.get("msg") or "NAS rejected",
            "nas_response": new_resp,
        }, status_code=502)
    new_data = new_resp.get("data") or {}
    new_id = new_data.get("id") if isinstance(new_data, dict) else None

    # 4) "激活"渲染:ZSpace app 只在用户手动编辑保存后才正确渲染 emoji;
    # 服务端在新建后立刻用相同 body 再调一次 modify,模拟 app 保存动作,
    # 触发 NAS 后端的 emoji 渲染初始化。
    if new_id:
        try:
            await nas_post(client, "/v2/file/notepad/modify", {
                "id": new_id, "title": title, "body": body,
                "classify_id": classify_id, "location": 2,
            })
            log.info("SHORTCUT: activated render for id=%s", new_id)
        except Exception as _e:
            log.warning("SHORTCUT: activate render failed for id=%s: %s", new_id, _e)

    return JSONResponse({
        "ok": True, "exists": False, "id": new_id,
    })


_PWA_NOTEPAD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="推 NAS 笔记">
<title>推 NAS 笔记</title>
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang SC", sans-serif;
    background: #f2f2f7; color: #000;
    -webkit-text-size-adjust: 100%;
  }
  .wrap { max-width: 600px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 22px; margin: 4px 0 16px; }
  .card {
    background: #fff; border-radius: 12px;
    padding: 14px 16px; margin-bottom: 12px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }
  label { display: block; font-size: 13px; color: #6c6c70; margin-bottom: 6px; }
  input[type=text], textarea {
    width: 100%; padding: 10px 12px;
    font-size: 16px; font-family: inherit;
    border: 1px solid #d1d1d6; border-radius: 8px;
    background: #fff; color: #000; outline: none;
  }
  input[type=text]:focus, textarea:focus { border-color: #007aff; }
  textarea { min-height: 140px; resize: vertical; }
  .row { display: flex; gap: 8px; margin-top: 8px; }
  button {
    flex: 1; padding: 12px 16px;
    font-size: 16px; font-weight: 600;
    border: none; border-radius: 10px;
    background: #007aff; color: #fff;
    -webkit-tap-highlight-color: transparent;
  }
  button:active { background: #0062cc; }
  button.secondary { background: #e5e5ea; color: #007aff; }
  button.secondary:active { background: #d1d1d6; }
  #status {
    margin-top: 12px; padding: 12px 14px;
    border-radius: 10px; font-size: 14px;
    display: none; word-break: break-all;
  }
  #status.ok    { background: #d4f7dc; color: #1f6f2b; display: block; }
  #status.skip  { background: #fff3cd; color: #7a5d00; display: block; }
  #status.err   { background: #ffd6d6; color: #8b0000; display: block; }
  .hint { font-size: 12px; color: #8e8e93; margin-top: 6px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>推送到 NAS 记事本</h1>

  <div class="card">
    <label>标题 title</label>
    <input id="title" type="text" placeholder="例如:周报 2026-07-01" autocomplete="off">
  </div>

  <div class="card">
    <label>正文 body(自动加 &lt;h1&gt;title&lt;/h1&gt; 前缀)</label>
    <textarea id="body" placeholder="随便写,支持 HTML"></textarea>
  </div>

  <div class="row">
    <button onclick="submitNote()">推送</button>
  </div>

  <div id="status"></div>

  <div class="hint" style="margin-top:24px">
    同名标题会自动跳过(不覆盖)。把页面加到主屏幕,从桌面图标打开更顺手。
  </div>
</div>

<script>
  function setStatus(kind, msg) {
    const el = document.getElementById("status");
    el.className = kind;
    el.textContent = msg;
  }

  async function submitNote() {
    const title = document.getElementById("title").value.trim();
    const body  = document.getElementById("body").value;
    if (!title) { setStatus("err", "标题必填"); return; }

    setStatus("ok", "推送中…");
    try {
      const resp = await fetch("/shortcut/notepad", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, body }),
      });
      const text = await resp.text();
      let j = {};
      try { j = JSON.parse(text); } catch (_) {}
      if (resp.status === 200 && j.ok && !j.exists) {
        setStatus("ok", "✅ 已推送 id=" + j.id);
      } else if (resp.status === 200 && j.exists) {
        setStatus("skip", "⏭️ 跳过(同名已存在) id=" + j.id);
      } else if (resp.status === 413) {
        setStatus("err", "❌ body 超 500KB");
      } else {
        setStatus("err", "❌ " + (j.error || resp.status) + (j.nas_response ? " " + JSON.stringify(j.nas_response) : ""));
      }
    } catch (e) {
      setStatus("err", "❌ 网络错误 " + e);
    }
  }

  // 启动时:title 框粘贴事件,自动从剪贴板抓首行
  document.getElementById("title").addEventListener("paste", (e) => {
    setTimeout(() => {
      const t = document.getElementById("title");
      if (t.value.trim()) return;
      const pasted = (e.clipboardData || window.clipboardData).getData("text");
      const firstLine = (pasted || "").split(/\\r?\\n/)[0].slice(0, 80);
      if (firstLine) t.value = firstLine;
    }, 0);
  });
</script>
</body>
</html>
"""


@router.get("/n", response_class=HTMLResponse)
async def notepad_pwa():
    """iPhone Safari 上的 PWA 记事本推送表单(不需要装 Shortcuts,不需要密钥)。

    用法:
      1. iPhone Safari 打开 http://<nas_ip>:15050/n
      2. 分享 → 添加到主屏幕,以后桌面图标直接进(全屏,跟 app 一样)
      3. 填 title + body,点推送(开放模式,服务端自己抽 title 去重)
    """
    return HTMLResponse(_PWA_NOTEPAD_HTML)
