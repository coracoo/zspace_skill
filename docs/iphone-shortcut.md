# iPhone 快捷指令 → ZSpace NAS 记事本 同步

单向同步:iPhone 备忘录 → NAS 记事本(LAN 上,不上云)。

## 架构

```
[备忘录 app]
  ↓ 主动查询路线(4 动作 Shortcut)
  ├─ 查找备忘录
  ├─ 从输入获取多信息文本
  ├─ 用多信息文本制作 HTML  → Cocoa HTML Writer 风格完整 HTML
  └─ 获取 URL 内容 POST
       ↓ HTTP POST  http://<nas_ip>:15050/shortcut/notepad
       ↓ Body:      application/x-www-form-urlencoded,值是完整 HTML 字符串
[宿主机 dashboard/app/__main__.py :15050]
  ↓ 1. 检测 Cocoa HTML("Cocoa HTML Writer" / ".AppleSystemUIFont")
  ↓    _cocoa_html_to_clean() 结构化解析 + 渲染成干净 HTML
  │    span class s1/s2/s3/s4 → h1/h2/h3/p
  │    <table class="t1">     → <table border="1">
  ↓ 2. emoji 范围字符 → &#数字; entity
  │    (ZSpace app 详情渲染对 UTF-8 emoji 字体回退失败,
  │     entity 形式能正确显示;中文 / ASCII 不动)
  │    🐶 (U+1F436)         → &#128054;
  │    你好 (U+4F60 U+597D) → 保持原样
  ↓ 3. title 从 <h1> 抽 / 同名查重 / 加 <h1> 前缀
  ↓ 4. POST /v2/file/notepad/new 创建
  ↓ 5. 立刻 POST /v2/file/notepad/modify(body 不变)再写一遍
  │    模拟 app 保存动作,触发 NAS 后端 emoji 渲染初始化,
  │    否则 app 详情第一次打开看不到 emoji,需要手动保存一次
[NAS API :5055]
  ↓ 落到 /sata14/my/notepad/...
[NAS 记事本]
  ↓ 极空间 app 详情正确渲染(标题样式 + 表格带边框 + emoji 彩色字体)
```

iPhone Shortcuts 不能直接调 NAS API(NAS 用 RSA 加密 + cookie session,Shortcuts 撑不住),所以走 dashboard(FastAPI 已经在 <nas_ip>:15050 上跑)做代理。

**设计原则**:iPhone 只负责推富文本 HTML,**服务端自动剥样式 / 转干净 HTML / 抽 title / 查重 / 落盘**。iOS 端 0 密钥 0 配置。

**为什么需要服务端转 HTML**:iOS "用多信息文本制作 HTML" 产出的 HTML 含 iOS 私有样式(AppleSystemUIFont 等)。极空间记事本不认这些样式,服务端 `_cocoa_html_to_clean()` 自动清洗。

---

## 一、准备工作(只做一次)

### 1. 宿主机 dashboard 在跑

```bash
cd zspace-mcp-poc
./start.sh dashboard
```

`.env` 里 `SHORTCUT_KEY` 留空 = 开放模式(LAN 内任何设备可推)。如果要加密钥,填一串后 iOS Shortcut 带 `X-Shortcut-Key` 头。

### 2. 验证端点能通

在 LAN 上任何机器(包括 iPhone 在同一 WiFi 后):

```bash
curl -X POST http://<nas_ip>:15050/shortcut/notepad \
  -H "Content-Type: text/plain; charset=utf-8" \
  --data "smoke test from laptop
hello from iPhone"
```

正常返回 `{"ok":true,"exists":false,"id":N}`。

### 3. ⚠️ 必做:iOS 18 默认禁明文 HTTP

iPhone **设置** → **快捷指令** → **高级** → 打开 **`允许访问不安全的网站`** / `允许 HTTP`(版本不同叫法略不同,大致在这个层级,实在找不到就搜你 iOS 版本)。不开这个 Shortcut POST 会被 iOS 静默拦截,看不到错误。

---

## 二、建 1 个手动推 NAS Shortcut(只做一次,4 个动作)

⚠️ **iPhone 备忘录不能直接"分享到快捷指令"**(iOS 16+ 改成了"文件"分享模型),所以这个 Shortcut 是**主动查询路线**:**「查找备忘录」→「从输入获取多信息文本」→「用多信息文本制作 HTML」→「获取 URL 内容」**。这样 emoji 和表格才能保住(`从输入中获取文本` 会拍平成纯文本,丢格式)。

iPhone **快捷指令** app:

1. 底部 tab **我的快捷指令**
2. 右上角 **蓝色 `+` 加号** → 起名 `推 NAS`(随便起)→ 点 **添加操作**
3. 不用开"共享表单接收",Shortcut 跑的时候自己拉备忘录

### 动作 1:查找备忘录

4. 添加操作 → 搜 **`查找备忘录`** / **`Find Notes`** → 选它
5. 配置:
   - **排序(Sort by)**: `创建日期` / `Creation Date`
   - **顺序(Order)**: `最新优先` / `Most Recent First`
   - **限制(Limit)**: `1`(只推最新一条)
   - **筛选(Filter)**: 不设(全量)

### 动作 2:从输入获取多信息文本

6. 添加操作 → 搜 **`多信息文本`** 或 **`Rich Text`** → 选 **`从输入获取多信息文本`** / `Get Rich Text from Input`
   - **输入**: 默认接动作 1 的输出(Shortcuts 自动,不用手动选)

⚠️ 这一步决定 emoji 和表格能不能保住。**千万别用 `从输入中获取文本`**(那个会拍平成纯文本,丢一切格式)。

### 动作 3:用多信息文本制作 HTML

7. 添加操作 → 搜 **`HTML`** → 选 **`用多信息文本制作 HTML`** / `Make HTML from Rich Text`
   - **输入**: 默认接上一个动作的输出(多信息文本)

### 动作 4:获取 URL 内容

8. 添加操作 → 搜 **`URL`** → 选 **`获取 URL 内容`**(地球图标)
9. 配置:
   - **URL**: `http://<nas_ip>:15050/shortcut/notepad`(公网访问换成你映射的域名)
   - **方法**: `POST`
   - **请求头(Headers)**: 不填(开放模式不需要密钥)
   - **请求正文(Request Body)**: 类型选 **文本** → 在文本框里点一下,键盘上方变量栏出现 **`多信息文本`**(上一个动作的 HTML 输出)→ 选它

10. 顶部 **完成** 保存

### (可选)兜底:从剪贴板也能跑

11. 点开 Shortcut 顶部 `ⓘ` → 翻到底找 **运行时输入** 或 **在没有输入的情况下运行**
12. 选 **从剪贴板获取输入**(`Get Clipboard`)

这样:**有选中输入时跑 = 把输入富文本推上去**;无输入时跑 = 把剪贴板内容推上去(剪贴板是纯文本,也能用,只是丢格式)。

---

## 三、使用方法

### 从备忘录分享

1. iPhone **备忘录** app,打开任一笔记
2. 右上角 **分享按钮(方块+上箭头)**
3. 在弹出的 app 列表里点 **`推 NAS`**(第一次需要在分享菜单里点 **更多** 把 `推 NAS` 勾出来)
4. 几秒后屏幕顶部系统通知显示 `id=N`(成功)或 `skipped`(已在,跳过)

### 从桌面图标

1. 长按 `推 NAS` 这条 Shortcut → **添加到主屏幕** → 桌面出现图标
2. 先复制一段文本 → 点桌面图标即推

---

## 四、行为规则(服务端负责,iOS 不用管)

| 规则 | 说明 |
|------|------|
| **title 从 `<h1>` 抽(HTML 路径)** | 走"用多信息文本制作 HTML"路径时,服务端从 `<h1>{title}</h1>` 标签抽 title,并把这块从 body 里删掉(避免双 h1) |
| **title 从首行抽(纯文本路径)** | 走"获取文本"或剪贴板路径时,服务端从 body 第一非空行(≤200 字符)抽 title,并从 body 删掉这一行 |
| **同名跳过(不覆盖)** | 已存在同名笔记就跳过,返回 `exists=true, id=已有id`。同名判定是精确匹配 |
| **h1 自动保证** | HTML 路径:你送来的 HTML 自带 `<h1>`;纯文本路径:服务端补 `<h1>{title}</h1>` 前缀。两种路径最终 NAS 里都有且只有 1 个 h1 |
| **emoji / 表格保留** | 走 HTML 路径才保留。走纯文本路径 emoji 通常能保住,但 `<table>` 等结构化格式会丢 |
| **body 限制 500 KB** | 超了返回 413 |

---

## 五、API 速查

### `POST /shortcut/notepad`

**Headers**(可选):
- `X-Shortcut-Key`:仅在 `.env` 设了 `SHORTCUT_KEY` 时强制校验
- `Content-Type`:支持 `application/json` / `text/plain` / 不填

**Body**(两种格式二选一):

**A. 纯文本(text/plain 或不填)** — 剪贴板/纯文本 Shortcut 走这个

整段请求体就是笔记内容。服务端从第一行抽 title。

```
推送标题
正文第一行
正文第二行
```

**B. 富文本 HTML(`application/x-www-form-urlencoded`,实际由"用多信息文本制作 HTML"送来)**

iOS Shortcut 走 HTML 路径时,`<h1>` 已在 body 里。服务端会从 `<h1>` 抽 title,把这一段从 body 删掉,**不重复加 h1**。

```html
<h1>推送标题</h1>
<p>正文带 emoji 😊 和 🎉</p>
<table border="1"><tr><td>列1</td><td>列2</td></tr></table>
```

⚠️ **iOS Shortcut 实际发送的 Content-Type 是 `application/x-www-form-urlencoded`(不是 text/plain)**。服务端两个分支都支持。

可选 query 参数覆盖:
- `?title=...` 显式指定标题
- `?classify_id=N` 叶子分类 ID(默认 0 = 未分类)

**B. JSON(application/json)** — 显式控制

```json
{
  "title": "可选,省略时从 body 第一行抽",
  "body": "笔记正文",
  "classify_id": 0
}
```

**返回**:

| 状态 | Body | 含义 |
|------|------|------|
| 200 | `{"ok":true,"exists":false,"id":N}` | 新建成功 |
| 200 | `{"ok":true,"exists":true,"id":N,"skipped_reason":"..."}` | 同名已存在,跳过 |
| 400 | `{"error":"title required (and body has no first line to derive from)"}` | body 完全空 |
| 401 | `{"error":"invalid X-Shortcut-Key"}` | 服务端启用了密钥但请求没带/带错 |
| 413 | `{"error":"body too long (max 500KB)"}` | body 超限 |
| 502 | `{"error":"NAS login failed (check NAS_USER/NAS_PASSWORD env on host)"}` | NAS 登录失败 |
| 502 | `{"error":"...","nas_response":{...}}` | NAS 拒绝写入 |

---

## 六、限制 / 已知 gap

- **单向**:iPhone → NAS;NAS → iPhone 不在范围
- **同名跳过**:title 完全相同才跳过(包括 iOS 自动加的 "(2)" 后缀不算)
- **body 限制 500 KB**:超了 413。iPhone 单条笔记一般没问题
- **h1 前缀**:HTML 路径不重复加(自带);纯文本路径服务端自动加 `<h1>{title}</h1>\n{body}`
- **格式保留**:emoji 永远保留;`<table>` / `<img>` 等 HTML 标签**只在 HTML 路径下保留**,纯文本路径会被 bleach 清理掉
- **session 过期**:NAS cookie 失效时 Shortcut 会 502,重启 `uvicorn` 即可恢复
- **LAN 信任**:开放模式任何 LAN 上的人都能推,要锁就把 `SHORTCUT_KEY` 填上
- **iOS 18 HTTP 限制**:必须开"允许访问不安全的网站"才能 POST 明文 HTTP

---

## 七、批量 / 全量同步

适合"全量备份备忘录到 NAS"。

### 思路

`查找备忘录` 拿的是一组 Note 引用,每条需要单独走"获取富文本 → 转 HTML → POST"的链路,所以**用 `对每个项目重复` 包住 4 动作链**。每轮循环独立调一次 `/shortcut/notepad`,服务端同名跳过不会重复创建。

### 创建"批量推 NAS" Shortcut(一次做)

1. 快捷指令 app → 我的快捷指令 → `+` 新建,起名 **`批量推 NAS 备份`**(或随意)
2. 添加动作 → 搜 **`查找备忘录`** → 选它
   - 排序:创建日期
   - 顺序:最新优先
   - **限制:留空**(全量;如果备忘录太多可以填 500 分批跑)
   - 筛选:不设
3. 添加动作 → 搜 **`重复`** → 选 **对每个项目重复**(`Repeat with Each`)
4. 在循环**内部**添加以下 4 个动作:
   1. **从输入获取多信息文本**(`Get Rich Text from Input`)
      - 输入:选 `重复项目`
   2. **用多信息文本制作 HTML**(`Make HTML from Rich Text`)
      - 输入:接上一步的多信息文本
   3. **获取 URL 内容**(`Get Contents of URL`)
      - URL: `http://<nas_ip>:15050/shortcut/notepad`
      - 方法: `POST`
      - 请求头:留空(开放模式)
      - 请求正文 → 类型 **文本** → 在变量栏选上一步的 **HTML 输出**
5. (可选)在循环里加一个 **等待**(`Wait`),设 0.3 秒。
6. 顶部 **完成**

### 手动跑一次

- 快捷指令 app → 我的快捷指令 → 点 **`批量推 NAS 备份`**
- 第一次跑会让你批准访问备忘录,批准后跑完全部
- 进度在 Shortcut 编辑器或通知栏看

### 定时全量跑(可选)

把上面的 **`批量推 NAS 备份`** 包进一个 Personal Automation:

1. 快捷指令 app → 底部 **自动化** → `+` → 创建个人自动化
2. 触发器:**特定时间**(如每天早上 6:00)
3. **关闭** 运行前询问 → 下一步
4. 添加动作 → 搜 `运行快捷指令` → 选 **`批量推 NAS 备份`**
5. 下一步,关闭运行前询问,完成

### 行为

- **同名跳过**:服务端按 title 精确查重,已存在不覆盖(改了 NAS 端不会盖原笔记,需要重传改名)
- **emoji/格式保留**:走 4 动作链(不是 2 动作的 `获取文本`),emoji、表格、标题样式都保留
- **失败重试**:Shortcut 跑某一条失败不会中断整批,但失败的也不会补;可以再跑一遍
- **耗时**:100 条约 1-2 分钟;全量建议分批

### 注意

- **第一次跑会很慢**:服务端冷启动 + 第一次 cookie 没缓存要登录
- **iOS 18 默认禁明文 HTTP**:同第二节,设置 → 快捷指令 → 高级 → 允许访问不安全的网站
- **iOS 后台被杀**:定时自动化会漏跑,在 `设置 → 快捷指令 → 高级 → 在后台运行内容` 里允许
- **存储**:500 条笔记 body 平均 1-5KB,NAS 记事本空间够用

---

## 八、故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 分享菜单里没"推 NAS" | 没开"在共享表单中显示",或没把 `[任意]` 改成 `[文本]` | 回指令编辑页 → 底部 `ⓘ` → 看开关;确认顶部蓝字是"接收 [文本]" |
| 点了没反应,没弹通知 | iOS 18 静默拦截 HTTP | 设置 → 快捷指令 → 高级 → 允许访问不安全的网站 |
| 通知:`NAS login failed` | 宿主机 `.env` 里 `NAS_USER`/`NAS_PASSWORD` 错 | 检查 `.env`,重启 uvicorn |
| 通知:`invalid X-Shortcut-Key` | 服务端启用了密钥,但请求没带/带错 | 要么 `.env` 里把 `SHORTCUT_KEY` 清空,要么 Shortcut 加 `X-Shortcut-Key` 头 |
| 标题怪怪的 | 服务端自动从第一行抽 | 在备忘录里把想要的标题写在第一行 |