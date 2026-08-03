#!/bin/bash
# media-naming skill 烟雾测试
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

SEARCH_DIR="$SKILL_DIR"
while [ "$SEARCH_DIR" != "/" ]; do
    if [ -f "$SEARCH_DIR/start.sh" ]; then
        PROJECT_ROOT="$SEARCH_DIR"
        break
    fi
    SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done

if [ -z "$PROJECT_ROOT" ] || [ ! -f "$PROJECT_ROOT/start.sh" ]; then
    echo "❌ 找不到项目根(含 start.sh 的目录)"
    exit 1
fi

echo "PROJECT_ROOT=$PROJECT_ROOT"

echo "=== TEST 1: SKILL.md frontmatter 合法 ==="
python3 -c "
import re, sys
content = open('$SKILL_DIR/SKILL.md').read()
m = re.match(r'^---\n(.+?)\n---', content, re.DOTALL)
if not m:
    print('❌ frontmatter 缺失')
    sys.exit(1)
block = m.group(1)
name = None
for line in block.splitlines():
    if line.startswith('name:'):
        name = line.split(':', 1)[1].strip()
        break
if name != 'media-naming':
    print(f'❌ name 错: {name!r}')
    sys.exit(1)
print('  ✓ SKILL.md 合法,name=media-naming')
"

echo "=== TEST 2: 模块可语法检查 ==="
python3 -m py_compile "$SKILL_DIR/media_naming.py" "$SKILL_DIR/lib/nas_client.py"
echo "  ✓ py_compile 通过"

echo "=== TEST 3: validate 纯函数(不连 NAS) ==="
export MEDIA_NAMING_SKILL_DIR="$SKILL_DIR"
export MEDIA_NAMING_PROJECT_ROOT="$PROJECT_ROOT"
python3 <<'PY'
import sys
import types
from pathlib import Path

skill = Path(__import__("os").environ["MEDIA_NAMING_SKILL_DIR"])
project = Path(__import__("os").environ["MEDIA_NAMING_PROJECT_ROOT"])
sys.path.insert(0, str(skill))

nas_mod = types.ModuleType("nas")

class NasClient:
    pass

nas_mod.NasClient = NasClient
sys.modules["nas"] = nas_mod

lib = types.ModuleType("lib")
lib_nc = types.ModuleType("lib.nas_client")
lib_nc.NasClient = NasClient
lib_nc.PROJECT_ROOT = project
lib_nc._load_env = lambda: None
sys.modules["lib"] = lib
sys.modules["lib.nas_client"] = lib_nc

import media_naming as mn

root = "/sata14/my/data/影视"
ok = mn.validate(
    {"path": f"{root}/电影/好东西 Her Story (2024)", "name": "好东西 Her Story (2024)", "is_dir": True},
    root,
)
assert ok == [], ok

bad = mn.validate(
    {"path": f"{root}/电影/钢铁侠 Iron Man 1-3", "name": "钢铁侠 Iron Man 1-3", "is_dir": True},
    root,
)
assert any("合集" in p for p in bad), bad

junk = mn.validate(
    {"path": f"{root}/电影/foo.torrent", "name": "foo.torrent", "is_dir": False},
    root,
)
assert "垃圾文件" in junk, junk

print("  ✓ validate 用例通过")
PY

echo "=== TEST 4: --help ==="
# help 不触发 nas 登录;但 import 链会读 .env — 若无 .env 用 stub 跑 argparse 已覆盖 validate
# 有 .env 时测真实 CLI
if [ -f "$PROJECT_ROOT/.env" ]; then
    python3 "$SKILL_DIR/media_naming.py" --help >/dev/null
    python3 "$SKILL_DIR/media_naming.py" scan --help >/dev/null
    echo "  ✓ CLI help 可用"
else
    echo "  ✓ SKIP CLI help(无 .env;import nas_client 会退出)"
fi

echo ""
echo "🎉 所有 smoke test 通过"
