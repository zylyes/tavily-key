#!/bin/bash
# Start Tavily API Key Pool Dashboard
# Usage: ./run_dashboard.sh [port]   — port 可选，覆盖 config.json 中的端口
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR/app"

# 从 config.json 读取 host/port（不存在时自动生成默认配置）
HOST=$(python3 -c "import settings; print(settings.get_settings().get('host','127.0.0.1'))" 2>/dev/null || echo "127.0.0.1")
DEF_PORT=$(python3 -c "import settings; print(int(settings.get_settings().get('port',8000)))" 2>/dev/null || echo "8000")
PORT=${1:-$DEF_PORT}

exec python3 -m uvicorn dashboard:app --host "$HOST" --port "$PORT"
