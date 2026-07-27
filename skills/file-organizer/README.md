# File Organizer Skill

NAS 文件只读诊断:找重复文件 + 孤儿文件。

详细见 `SKILL.md`。脚本入口:

```bash
python file_organizer.py audit-duplicates --output /tmp/dups.json
python file_organizer.py audit-orphans --output /tmp/orphans.json
python file_organizer.py audit-all --output /tmp/full-report.json
```

复用 media-organizer 的 lib/nas_client.py 桥接模式,通过 `from nas import NasClient` 复用登录逻辑。

触发词从 SKILL.md frontmatter 加载,无需在 README 重复。

## 注意

⚠️ 由于使用弱指纹策略,`audit-duplicates` 的结果有较高误报率,需人工核对后再决定删除操作。
