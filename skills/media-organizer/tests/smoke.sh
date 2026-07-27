#!/bin/bash
# media-organizer skill 烟雾测试
# 用法: bash tests/smoke.sh
# 退出码:0=全过,1=有失败

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

# 从 SKILL_DIR 往上找项目根(找到含 start.sh 的目录)
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

PY="$PROJECT_ROOT/.venv/bin/python"

echo "PROJECT_ROOT=$PROJECT_ROOT"
echo "=== TEST 1: audit-classifications ==="
OUTPUT=$("$PY" "$SKILL_DIR/media_organizer.py" audit-classifications 2>/dev/null)
if ! echo "$OUTPUT" | grep -q "分类审计"; then
    echo "❌ FAIL: 输出不含'分类审计'"
    exit 1
fi
echo "  ✓ audit-classifications 输出含'分类审计'"

echo "=== TEST 2: audit-sources ==="
OUTPUT=$("$PY" "$SKILL_DIR/media_organizer.py" audit-sources 2>/dev/null)
if ! echo "$OUTPUT" | grep -q "源目录审计"; then
    echo "❌ FAIL: 输出不含'源目录审计'"
    exit 1
fi
echo "  ✓ audit-sources 输出含'源目录审计'"

echo "=== TEST 3: audit-collections --sample 3 ==="
OUTPUT=$("$PY" "$SKILL_DIR/media_organizer.py" audit-collections --sample 3 2>/dev/null)
if ! echo "$OUTPUT" | grep -q "影片抽样审计"; then
    echo "❌ FAIL: 输出不含'影片抽样审计'"
    exit 1
fi
echo "  ✓ audit-collections 输出含'影片抽样审计'"

echo "=== TEST 4: audit-all 头部摘要 ==="
OUTPUT=$("$PY" "$SKILL_DIR/media_organizer.py" audit-all --sample 3 2>/dev/null)
if ! echo "$OUTPUT" | grep -q "Media Organizer — NAS 极影视诊断报告"; then
    echo "❌ FAIL: 输出不含综合报告标题"
    exit 1
fi
echo "  ✓ audit-all 输出含综合报告标题"

echo "=== TEST 5: suggest-moves 头 + 目标分类 ==="
OUTPUT=$("$PY" "$SKILL_DIR/media_organizer.py" suggest-moves --sample 5 2>/dev/null)
if ! echo "$OUTPUT" | grep -q "挪分类建议"; then
    echo "❌ FAIL: 输出不含'挪分类建议'"
    exit 1
fi
if ! echo "$OUTPUT" | grep -q "目标分类"; then
    echo "❌ FAIL: 输出不含'目标分类'"
    exit 1
fi
echo "  ✓ suggest-moves 输出挪分类建议 + 目标分类"

echo "=== TEST 6: audit-all 含 suggest-moves section ==="
OUTPUT=$("$PY" "$SKILL_DIR/media_organizer.py" audit-all --sample 3 --suggest-sample 5 2>/dev/null)
if ! echo "$OUTPUT" | grep -q "挪分类建议"; then
    echo "❌ FAIL: audit-all 缺少 suggest-moves section"
    exit 1
fi
echo "  ✓ audit-all 集成 suggest-moves section"

echo "=== TEST 7: suggest-moves --output 写 JSON ==="
TMP_OUT=$(mktemp /tmp/moves.XXXXXX.json)
"$PY" "$SKILL_DIR/media_organizer.py" suggest-moves --sample 3 --output "$TMP_OUT" 2>/dev/null > /dev/null
if [ ! -f "$TMP_OUT" ]; then
    echo "❌ FAIL: --output 没写文件"
    exit 1
fi
if ! grep -q "system_targets\|suggestions\|abnormal_classifications" "$TMP_OUT"; then
    echo "❌ FAIL: JSON 缺关键字段"
    exit 1
fi
echo "  ✓ suggest-moves --output 写入合法 JSON"
rm "$TMP_OUT"

echo "=== TEST 8: SKILL.md frontmatter 合法 ==="
python3 -c "
import yaml, re, sys
content = open('$SKILL_DIR/SKILL.md').read()
m = re.match(r'^---\n(.+?)\n---', content, re.DOTALL)
if not m:
    print('❌ frontmatter 缺失')
    sys.exit(1)
fm = yaml.safe_load(m.group(1))
if fm.get('name') != 'media-organizer':
    print(f'❌ name 错: {fm.get(\"name\")}')
    sys.exit(1)
print('  ✓ SKILL.md 合法,name=media-organizer')
"

echo "=== TEST 9: lib/nas_client.py 可 import ==="
"$PY" -c "
import sys
sys.path.insert(0, '$SKILL_DIR')
from lib.nas_client import NasClient, PROJECT_ROOT
assert PROJECT_ROOT.name == 'zspace-mcp-poc', f'PROJECT_ROOT={PROJECT_ROOT}'
print('  ✓ lib/nas_client.py 可 import,PROJECT_ROOT 正确')
" 2>&1 | grep "✓" || { echo "❌ import 失败"; exit 1; }

echo ""
echo "=== migrate (新增) ==="

echo "=== TEST M1: migration-rules.yaml.example 可加载 ==="
"$PY" -c "
import sys; sys.path.insert(0, '$SKILL_DIR')
from lib.migration_rules import load_config
cfg = load_config('$SKILL_DIR/migration-rules.yaml.example')
assert len(cfg.libraries) >= 1
assert len(cfg.move_rules) >= 1
print('  ✓ example config loads')
" 2>&1 | grep "✓" || { echo "❌ M1 FAIL"; exit 1; }

echo "=== TEST M2: match_rule 启发式匹配 ==="
"$PY" -c "
import sys; sys.path.insert(0, '$SKILL_DIR')
from lib.migration_rules import load_config, match_rule
cfg = load_config('$SKILL_DIR/migration-rules.yaml.example')
assert match_rule('Friends.S01E01.mkv', cfg.move_rules).target == '电视剧'
assert match_rule('Inception.2010.mkv', cfg.move_rules) is None
assert match_rule('song.mp3', cfg.move_rules).target == '音乐'
print('  ✓ match_rule SxxExx + mp3 patterns')
" 2>&1 | grep "✓" || { echo "❌ M2 FAIL"; exit 1; }

echo "=== TEST M3: infer_current_library 路径前缀匹配 ==="
"$PY" -c "
import sys; sys.path.insert(0, '$SKILL_DIR')
from lib.migration_rules import load_config, infer_current_library
cfg = load_config('$SKILL_DIR/migration-rules.yaml.example')
assert infer_current_library('/sata14/my/data/movies/x.mkv', cfg.libraries).name == '电影'
assert infer_current_library('/sata14/my/data/series/y.mkv', cfg.libraries).name == '电视剧'
assert infer_current_library('/random/path/z.mkv', cfg.libraries) is None
print('  ✓ infer_current_library')
" 2>&1 | grep "✓" || { echo "❌ M3 FAIL"; exit 1; }

echo "=== TEST M4: build_plan 合成候选 ==="
"$PY" -c "
import sys; sys.path.insert(0, '$SKILL_DIR')
from media_organizer import Migrator
from lib.migration_rules import load_config
cfg = load_config('$SKILL_DIR/migration-rules.yaml.example')
m = Migrator(cfg)
files = {
    '电影': ['/sata14/my/data/movies/Inception.2010.mkv',
             '/sata14/my/data/movies/Friends.S01E01.mkv',
             '/sata14/my/data/movies/song.mp3'],
    '电视剧': ['/sata14/my/data/series/Breaking.Bad.S01E01.mkv'],
    '音乐': ['/sata14/my/data/music/song1.mp3'],
}
plan = m.build_plan(files)
assert len(plan) == 2, f'expected 2, got {len(plan)}'
assert plan[0]['target_lib'] == '电视剧'
assert plan[1]['target_lib'] == '音乐'
print('  ✓ build_plan (S01E01 + .mp3 都识别为错放)')
" 2>&1 | grep "✓" || { echo "❌ M4 FAIL"; exit 1; }

echo "=== TEST M5: migrate --help 暴露 ==="
if "$PY" "$SKILL_DIR/media_organizer.py" migrate --help 2>&1 | grep -q -- '--apply'; then
    echo "  ✓ --apply flag 暴露"
else
    echo "❌ M5 FAIL: --apply missing"
    exit 1
fi

echo "=== TEST M6: move_rules.target 必须在 libraries.name ==="
"$PY" -c "
import sys; sys.path.insert(0, '$SKILL_DIR')
from lib.migration_rules import load_config
import tempfile, os
with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
    f.write('move_rules:\n  - pattern: \"*.mkv\"\n    target: nonexistent_lib\nlibraries: []\n')
    p = f.name
try:
    try:
        load_config(p)
        print('❌ M6 FAIL: 没报错'); raise SystemExit(1)
    except ValueError:
        print('  ✓ target 不在 libraries → ValueError')
finally:
    os.unlink(p)
" 2>&1 | grep "✓" || { echo "❌ M6 FAIL"; exit 1; }

echo ""
echo "🎉 所有 smoke test 通过"