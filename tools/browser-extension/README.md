# ZSpace Remote Access 浏览器扩展

把 zos 给 NAS 分配的 `https://remote-access-{port}.zconnect.cn/` 代理 URL
变成浏览器侧一键可达的入口。

## 功能

- **Popup**:点击工具栏图标,弹出白名单列表,每条点击跳转对应公网 URL
- **Omnibox**:地址栏输入 `zra <端口>`,回车跳转,例如 `zra 33335`
- **Options**:可视化编辑白名单(端口 + 内网 host + 标签)

## 安装

1. 打开 `chrome://extensions/`(Edge 用 `edge://extensions/`)
2. 右上角打开 **开发者模式**
3. 点 **加载已解压的扩展程序**,选这个目录 `browser-extension/`

> 图标我没生成(避免引入二进制文件),先不加也能跑。`manifest.json` 里 `icons` 字段可注释掉。

## 白名单数据源

当前是 **本地硬编码 + 用户手动编辑**(chrome.storage.local)。
不是从 NAS 实时拉,因为 `/zrps/api/remoteaccess/list` 在 NAS 上是 dead route。

要保证准确,得去 pcweb UI 的"远程访问"页查看当前白名单,复制过来。

## 跟 zspace-mcp-poc 的关系

| 工具 | 作用 |
|------|------|
| `zspace/mcp_server/tools/proxy.py` 的 `proxy_url_for_port(port)` | 程序化生成 URL(MCP/Claude Code 用) |
| 这个扩展 | 浏览器侧一键跳转(人用) |
| `ZENITH_COOKIE` env | mcp_server 跟 zos 云代理通讯用的 session cookie |

三个一起用 = 同一个白名单在 MCP、浏览器、URL 三处都能用。

## 已知 gap

- 没图标(避免二进制进 git)
- 白名单不会自动同步 NAS,得手动维护
- omnibox keyword `zra` 跟别的扩展可能冲突(可改)