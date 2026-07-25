#!/bin/sh
# 从魔搭社区(ModelScope)下载 bge-small-zh-v1.5 模型
# 用法: ./download-model.sh [输出目录,默认 ./fastembed-cache]
set -e
CACHE_DIR="${1:-./fastembed-cache}"
mkdir -p "$CACHE_DIR"

LOCAL="$HOME/.cache/modelscope/hub/Qdrant/bge-small-zh-v1.5"
if [ -d "$LOCAL" ]; then
    echo "→ 从本机魔搭 cache 复制: $LOCAL"
    mkdir -p "$CACHE_DIR/models--Qdrant--bge-small-zh-v1.5/snapshots"
    cp -rL "$LOCAL" "$CACHE_DIR/models--Qdrant--bge-small-zh-v1.5/snapshots/local"
else
    echo "→ 从魔搭社区下载(~100MB,国内高速)..."
    python3 -c "
import os, shutil
os.environ['MODELSCOPE_CACHE'] = '$CACHE_DIR'
from modelscope import snapshot_download
model_dir = snapshot_download('Qdrant/bge-small-zh-v1.5')
# modelscope 下载后,转换为 fastembed 能读的 HF cache 格式
hf_dir = '$CACHE_DIR/models--Qdrant--bge-small-zh-v1.5/snapshots/local'
os.makedirs(hf_dir, exist_ok=True)
for f in os.listdir(model_dir):
    src = os.path.join(model_dir, f)
    dst = os.path.join(hf_dir, f)
    if os.path.isfile(src) and not os.path.exists(dst):
        shutil.copy2(src, dst)
print(f'模型已下载: {hf_dir}')
"
fi

echo "✓ $CACHE_DIR"
echo "  挂载到容器: -v $(realpath "$CACHE_DIR"):/root/.cache/huggingface"
SCRIPT
chmod +x rag-server/scripts/download-model.sh

echo ""
echo "=== 验证脚本 ==="
cd ~/godness/zspace-mcp-poc
bash -n rag-server/scripts/download-model.sh && echo "✓ 语法正确"

echo ""
echo "=== 加 modelscope 到 requirements ==="
grep -q "modelscope" rag-server/requirements.txt || echo "modelscope" >> rag-server/requirements.txt
cat rag-server/requirements.txt

echo ""
echo "=== 提交 ==="
cd ~/godness/zspace-mcp-poc
git add rag-server/
git commit -m "$(cat<<'EOF'
feat(rag-server): 模型下载改为魔搭社区(ModelScope)

download-model.sh: huggingface → modelscope(snapshot_download)
- 本机有 modelscope cache 时直接复制
- 没有则从魔搭下载(国内高速)
- 下载后自动转换为 fastembed 能读的 HF cache 格式
- requirements.txt 加 modelscope

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"