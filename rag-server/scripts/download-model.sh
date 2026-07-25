#!/bin/sh
# 下载 bge-small-zh-v1.5 模型到 fastembed-cache/
set -e
CACHE_DIR="${1:-./fastembed-cache}"
mkdir -p "$CACHE_DIR"
LOCAL="$HOME/.cache/huggingface/hub/models--Qdrant--bge-small-zh-v1.5"
if [ -d "$LOCAL" ]; then
    echo "→ 从本机复制: $LOCAL"
    cp -rL "$LOCAL" "$CACHE_DIR/models--Qdrant--bge-small-zh-v1.5"
else
    echo "→ 从 huggingface 下载(~100MB)..."
    python3 -c "
import os; os.environ['HF_HOME']='$CACHE_DIR'
from fastembed import TextEmbedding
list(TextEmbedding(model_name='BAAI/bge-small-zh-v1.5').embed(['test']))
"
fi
echo "✓ $CACHE_DIR"
