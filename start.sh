#!/usr/bin/env bash
# ZSpace NAS PoC + MCP 快速启动脚本
# 用法: ./start.sh <command>
#   dashboard  启动 Web dashboard(默认端口 8000,后台运行,日志到 logs/)
#   mcp        启动 MCP server(stdio 前台运行,等 Claude Code 连接)
#   mcp-cfg    打印 Claude Code 的 mcp.json 配置片段
#   env        显示当前生效的环境变量(密码遮蔽)
#   deps       安装/更新 Python 依赖
#   help       显示帮助

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"
LOG_DIR="$ROOT/logs"

# ---- 颜色 ----
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_OFF=''
fi

info() { printf "%s==>%s %s\n" "$C_BLUE" "$C_OFF" "$*"; }
ok()   { printf "%s ✓%s %s\n" "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf "%s !!%s %s\n" "$C_YELLOW" "$C_OFF" "$*"; }
err()  { printf "%s ✗%s %s\n" "$C_RED" "$C_OFF" "$*" >&2; }

# ---- 加载 .env ----
load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    return 0
  fi
  return 1
}

ensure_env() {
  if load_env; then
    ok "loaded $ENV_FILE"
  else
    if [[ -f "$ENV_EXAMPLE" ]]; then
      err "找不到 $ENV_FILE"
      echo "  复制 $ENV_EXAMPLE 为 .env 并填入密码:"
      echo "    cp $ENV_EXAMPLE $ENV_FILE"
      echo "    vi $ENV_FILE"
      exit 1
    else
      err "找不到 $ENV_FILE 和 $ENV_EXAMPLE"
      exit 1
    fi
  fi

  if [[ -z "${NAS_USER:-}" || -z "${NAS_PASSWORD:-}" || "$NAS_PASSWORD" == "你的NAS密码" ]]; then
    err "NAS_USER / NAS_PASSWORD 未设置,或还在用 .env.example 的占位符"
    echo "  编辑 $ENV_FILE 填入真实密码"
    exit 1
  fi
}

# ---- 检查 venv ----
ensure_venv() {
  if [[ ! -x "$PY" ]]; then
    err "找不到 $PY"
    echo "  先跑:  ./start.sh deps"
    exit 1
  fi
}

# ---- 子命令: dashboard ----
cmd_dashboard() {
  ensure_venv
  ensure_env
  mkdir -p "$LOG_DIR"
  local log_file="$LOG_DIR/dashboard.log"
  local pid_file="$LOG_DIR/dashboard.pid"

  # 已在跑?
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    warn "dashboard 已在跑,PID=$(cat "$pid_file")"
    echo "  停止:  kill \$(cat $pid_file)"
    echo "  日志:  tail -f $log_file"
    return 0
  fi

  info "启动 dashboard(后台)…"
  nohup "$PY" -m uvicorn app:app --host 0.0.0.0 --port 8000 \
    >>"$log_file" 2>&1 &
  echo $! > "$pid_file"
  sleep 1.5
  if kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    ok "dashboard 已启动"
    echo "  URL:     http://localhost:8000  或  http://${NAS_HOST}:8000"
    echo "  PID:     $(cat "$pid_file")"
    echo "  日志:    $log_file  (tail -f 看实时)"
    echo "  停止:    kill \$(cat $pid_file)"
  else
    err "启动失败,看 $log_file"
    tail -20 "$log_file" >&2
    exit 1
  fi
}

# ---- 子命令: mcp ----
cmd_mcp() {
  ensure_venv
  ensure_env
  info "启动 MCP server(stdio),等 Claude Code 连接…"
  echo "  按 Ctrl+C 退出"
  echo "  日志走 stderr,会输出到启动 Claude Code 的终端"
  echo
  exec "$PY" mcp_server.py
}

# ---- 子命令: mcp-cfg ----
cmd_mcp_cfg() {
  ensure_venv
  ensure_env
  echo "${C_BOLD}⚠️  以下输出含明文 NAS_PASSWORD / KEY_SSH,注意终端记录与截图安全!${C_OFF}" >&2
  cat <<EOF
${C_BOLD}Claude Code 配置(~/.config/claude-code/mcp.json):${C_OFF}
{
  "mcpServers": {
    "zspace-nas": {
      "command": "$PY",
      "args": ["$ROOT/mcp_server.py"],
      "env": {
        "NAS_HOST": "$NAS_HOST",
        "NAS_USER": "$NAS_USER",
        "NAS_PASSWORD": "$NAS_PASSWORD",
        "KEY_SSH": "${KEY_SSH:-}",
        "NAS_DEVICE_ID": "${NAS_DEVICE_ID:-<your_device_id_32_hex>}"
      }
    }
  }
}
EOF
  echo
  echo "${C_BOLD}Cursor / Claude Desktop:${C_OFF} 同样格式,字段名 mcpServers / mcp.json。"
}

# ---- 子命令: env ----
cmd_env() {
  ensure_env
  local pw_mask
  pw_mask="${NAS_PASSWORD:0:2}***(${#NAS_PASSWORD} 位)"
  local ssh_mask="${KEY_SSH:+***(${#KEY_SSH} 位)}${KEY_SSH:-<未设置,perf_snapshot 不可用>}"
  cat <<EOF
NAS_HOST        = $NAS_HOST
NAS_USER        = $NAS_USER
NAS_PASSWORD    = $pw_mask
NAS_DEVICE_ID   = ${NAS_DEVICE_ID:-<未设置,用代码默认>}
KEY_SSH         = $ssh_mask
NAS_SSH_PORT    = ${NAS_SSH_PORT:-57922}
EOF
}

# ---- 子命令: deps ----
cmd_deps() {
  if [[ ! -d "$VENV" ]]; then
    info "创建 venv…"
    python3 -m venv "$VENV"
  fi
  info "pip install -r requirements.txt…"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r requirements.txt
  ok "依赖装好"
}

# ---- 子命令: help ----
cmd_help() {
  cat <<EOF
${C_BOLD}ZSpace NAS PoC + MCP 启动脚本${C_OFF}

用法:  ./start.sh <command>

命令:
  ${C_GREEN}dashboard${C_OFF}   启动 Web dashboard(后台,http://localhost:8000)
  ${C_GREEN}mcp${C_OFF}         启动 MCP server(stdio 前台,给 Claude Code 用)
  ${C_GREEN}mcp-cfg${C_OFF}     打印 Claude Code 的 mcp.json 配置片段
  ${C_GREEN}env${C_OFF}         查看当前生效的环境变量(密码遮蔽)
  ${C_GREEN}deps${C_OFF}        安装/更新 Python 依赖
  ${C_GREEN}help${C_OFF}        显示本帮助

首次使用:
  1. cp .env.example .env && 编辑填入 NAS_PASSWORD / KEY_SSH
  2. ./start.sh deps
  3. ./start.sh dashboard   # 看 Web 页面
  4. ./start.sh mcp-cfg     # 把输出粘到 ~/.config/claude-code/mcp.json

EOF
}

# ---- 入口 ----
cmd="${1:-help}"
shift || true

case "$cmd" in
  dashboard)  cmd_dashboard "$@" ;;
  mcp)        cmd_mcp "$@" ;;
  mcp-cfg)    cmd_mcp_cfg "$@" ;;
  env)        cmd_env "$@" ;;
  deps)       cmd_deps "$@" ;;
  help|-h|--help) cmd_help ;;
  *)
    err "未知命令: $cmd"
    cmd_help
    exit 1
    ;;
esac
