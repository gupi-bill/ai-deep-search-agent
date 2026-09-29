#!/usr/bin/env bash
# AI 深度搜索助手 —— macOS / Linux 启动脚本（图形窗口版）
set -e
cd "$(dirname "$0")"

PORT=""

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
  echo
  echo "  macOS:   brew install python@3.12   或  https://www.python.org/downloads/"
  echo "  Debian:  sudo apt install python3 python3-venv"
  echo "  Fedora:  sudo dnf install python3"
  echo
  exit 1
}

# ---- 优先用项目里的虚拟环境 ----
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
fi

# ---- 首次运行自动装依赖 ----
if ! "$PY" -c "import webview" >/dev/null 2>&1; then
  echo "  第一次运行，正在建虚拟环境并装依赖（需要几分钟）..."
  [ -d ".venv" ] || "$PY" -m venv .venv
  ./.venv/bin/python -m pip install -q --upgrade pip
  ./.venv/bin/python -m pip install -q -r requirements.txt
  PY=".venv/bin/python"
  echo "  依赖装好了。"
  echo
fi

echo "  用的是： $PY"
echo
echo "  macOS 首次可能需要授予「辅助功能」权限："
echo "    系统设置 → 隐私与安全性 → 辅助功能 → 把你的终端加进去"
echo
exec "$PY" visual_agent.py
