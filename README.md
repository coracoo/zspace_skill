# ZSpace NAS 方案 B PoC

验证:本地代理登录 NAS 后能否拿到数据。

## 跑起来

```bash
cd /home/cc/workspace/zspace-mcp-poc
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://192.168.0.123:8000`,用 NAS 账号登录。

## 验证什么

- 登录链路:RSA 加密 → `/auth/login` → 拿到 token
- 系统信息:`/system/versions`、`/system/diskusage3`、`/system/status`
- 文件列表:`/v2/file/list`(POST,会试几种 body 找到对的格式)

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 登录失败 `N001200 账号格式不对` | RSA 加密用的是错公钥(可能用了 1024-bit `server_pubkey` 而非 2048-bit `pubkey`) | 用 `/zspace/system/private/pubkey` 解码后的 PEM |
| `N001xxx device_id` | device_id 长度不对 | 必须 32 字符,代码已用 md5 |
| `/v2/file/list` `N001208 token已失效` | cookie 没传到后端 | 看 dashboard 上 cookie key 列表是否含 `token` |
| 完全打不通 | NAS 不在 LAN | 检查 192.168.0.135:5055 可达 |
