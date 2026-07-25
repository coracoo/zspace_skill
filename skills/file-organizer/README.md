# File Organizer Skill

NAS 文件只读诊断:找重复文件 + 孤儿文件。

详细见 `SKILL.md`。脚本入口:

```bash
python file_organizer.py audit-duplicates --output /tmp/dups.json
python file_organizer.py audit-orphans --output /tmp/orphans.json
python file_organizer.py audit-all --output /tmp/full-report.json
```

复用 media-organizer 的 lib/nas_client.py 桥接模式,通过 `from mcp_server import NasClient` 复用登录逻辑。

## 关键特性

- **只读诊断**: 不删除/移动/修改任何文件,只生成报告
- **弱指纹去重**: 由于 NAS 不返回 file_hash,使用 (size, ext) 作为弱指纹,误报率需人工核对
- **孤儿检测**: 找出既不在影视源目录下,也没打标签的"野生"文件
- **性能友好**: 单线程扫描,每页 200 条,每次请求后 sleep 0.1s(~10 req/s)

## 触发词

- 重复文件、孤儿文件、文件整理诊断
- 哪些文件重复了、哪些文件没归类
- 找重复电影、找重复照片

## 注意

⚠️ 由于使用弱指纹策略,`audit-duplicates` 的结果有较高误报率,需人工核对后再决定删除操作。
