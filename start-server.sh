#!/usr/bin/env bash
# AI 深度搜索助手 —— macOS / Linux 启动脚本（本地服务版，浏览器里用）
set -e
cd "$(dirname "$0")"

# ---- 找 Python 3.10+ ----
find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}

PY=$(find_python) || {
  echo "  [x] 没找到 Python 3.10 或更高版本。"
  echo "  macOS: brew install python@3.12   /   Debian: sudo apt install python3 python3-venv"
  exit 1
}

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
fi

# ---- 首次运行自动装依赖 ----
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
  echo "  第一次运行，正在建虚拟环境并装依赖（需要几分钟）..."
  [ -d ".venv" ] || "$PY" -m venv .venv
  ./.venv/bin/python -m pip install -q --upgrade pip
  ./.venv/bin/python -m pip install -q -r requirements.txt
  PY=".venv/bin/python"
  echo "  依赖装好了。"
  echo
fi

PORT="${PORT:-8080}"
echo "  正在启动本地服务 →  http://localhost:$PORT"
echo "  用完关掉这个窗口（Ctrl+C）就停了。"
echo

# 4 秒后开浏览器
( sleep 4
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true
  elif command -v open      >/dev/null 2>&1; then open      "http://localhost:$PORT" >/dev/null 2>&1 || true
  fi ) &

exec "$PY" server.py
