#!/bin/bash
# label-manager skill 烟雾测试
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

# 从 SKILL_DIR 往上找项目根(找到含 mcp_server.py 的目录)
SEARCH_DIR="$SKILL_DIR"
while [ "$SEARCH_DIR" != "/" ]; do
    if [ -f "$SEARCH_DIR/mcp_server.py" ]; then
        PROJECT_ROOT="$SEARCH_DIR"
        break
    fi
    SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done

if [ -z "$PROJECT_ROOT" ] || [ ! -f "$PROJECT_ROOT/mcp_server.py" ]; then
    echo "❌ 找不到项目根(含 mcp_server.py 的目录)"
    exit 1
fi

PY="$PROJECT_ROOT/.venv/bin/python"

echo "PROJECT_ROOT=$PROJECT_ROOT"

if [ ! -x "$PY" ]; then
    echo "❌ 找不到 $PY"
    exit 1
fi

echo "=== TEST 1: list-labels ==="
RESULT=$("$PY" "$SKILL_DIR/label_manager.py" list-labels 2>/dev/null)
COUNT=$(echo "$RESULT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(len(d.get('labels', [])))")
if [ -z "$COUNT" ] || [ "$COUNT" -lt 1 ]; then
    echo "❌ FAIL: list-labels 返回空"
    exit 1
fi
echo "  ✓ list-labels: $COUNT 个标签"

echo "=== TEST 2: scan --root /sata14/my/data/ --max-depth 2 ==="
RESULT=$("$PY" "$SKILL_DIR/label_manager.py" scan --root /sata14/my/data/ --max-depth 2 --output /tmp/smoke_scan.json 2>/dev/null)
COUNT=$(python3 -c "import json; d=json.load(open('/tmp/smoke_scan.json')); print(d.get('count', 0))")
DIRS=$(python3 -c "import json; d=json.load(open('/tmp/smoke_scan.json')); print(d.get('scanned_dirs', 0))")
echo "  ✓ scan: $DIRS 个目录,$COUNT 个文件"

echo "=== TEST 3: find-by-label --label docker ==="
RESULT=$("$PY" "$SKILL_DIR/label_manager.py" find-by-label --label docker --root /sata14/my/data/ --max-depth 2 --output /tmp/smoke_find.json 2>/dev/null)
MATCHED=$(python3 -c "import json; d=json.load(open('/tmp/smoke_find.json')); print(d.get('matched', 0))")
echo "  ✓ find-by-label: $MATCHED 个匹配"

echo "=== TEST 4: SKILL.md frontmatter 合法 ==="
python3 -c "
import yaml, re, sys
content = open('$SKILL_DIR/SKILL.md').read()
m = re.match(r'^---\n(.+?)\n---', content, re.DOTALL)
if not m:
    print('❌ frontmatter 缺失')
    sys.exit(1)
fm = yaml.safe_load(m.group(1))
if fm.get('name') != 'label-manager':
    print(f'❌ name 错: {fm.get(\"name\")}')
    sys.exit(1)
print('  ✓ SKILL.md 合法,name=label-manager')
"

echo "=== TEST 5: lib/nas_client.py 可 import ==="
"$PY" -c "
import sys
sys.path.insert(0, '$SKILL_DIR')
from lib.nas_client import NasClient, PROJECT_ROOT
assert PROJECT_ROOT.name == 'zspace-mcp-poc', f'PROJECT_ROOT={PROJECT_ROOT}'
print('  ✓ lib/nas_client.py 可 import,PROJECT_ROOT 正确')
" 2>&1 | grep "✓\|AssertionError" || { echo "❌ import 失败"; exit 1; }

echo ""
echo "🎉 所有 smoke test 通过"