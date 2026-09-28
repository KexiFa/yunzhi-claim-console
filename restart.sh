#!/usr/bin/env bash
# 一键重启云智手机领取控制台（nohup 后台）
# 用法: bash restart.sh

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "[$(date '+%F %T')] 停止旧进程..."
pkill -f "[p]ython web_app.py" 2>/dev/null || true
pkill -f "[p]ython3 web_app.py" 2>/dev/null || true
sleep 1

if [ -f "$DIR/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$DIR/.venv/bin/activate"
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

if [ ! -f "$DIR/config.json" ] && [ -f "$DIR/config.example.json" ]; then
  cp "$DIR/config.example.json" "$DIR/config.json"
  echo "[$(date '+%F %T')] 已生成 config.json，请先改 web.password / secret_key"
fi

echo "[$(date '+%F %T')] 启动 web_app.py ..."
nohup "$PY" web_app.py >> web.log 2>&1 &
sleep 1

if pgrep -af "web_app.py" >/dev/null; then
  echo "[$(date '+%F %T')] 已启动:"
  pgrep -af "web_app.py"
  echo "日志: $DIR/web.log  （查看: tail -f web.log）"
else
  echo "[$(date '+%F %T')] 启动失败，请看日志: tail -50 web.log"
  exit 1
fi
