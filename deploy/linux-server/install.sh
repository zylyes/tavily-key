#!/usr/bin/env bash
# Tavily Key Pool — Linux Server 版一键部署脚本
# 用法:
#   sudo ./install.sh --domain api.example.com [--port 8000] [--token 访问令牌] [--dir /opt/tavily]
#
# 功能:
#   1. 安装 Python 依赖
#   2. 构建前端（web/dist，v0.8.0 起 Dashboard 必需）
#   3. 生成 server 模式 data/config.json（绑定域名、0.0.0.0 监听）
#   4. 安装 systemd 服务（dashboard + mcp）
#   5. 生成 Nginx 反向代理配置（绑定域名）
set -euo pipefail

DOMAIN=""
PORT="8000"
TOKEN=""
APP_DIR="/opt/tavily"
SRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --port)   PORT="$2";   shift 2 ;;
    --token)  TOKEN="$2";  shift 2 ;;
    --dir)    APP_DIR="$2"; shift 2 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

if [[ -z "$DOMAIN" ]]; then
  echo "错误: 请通过 --domain 指定域名，例如: sudo ./install.sh --domain api.example.com"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "错误: 请使用 sudo 运行（需要写入 /etc/systemd/system 与 /etc/nginx）。"
  exit 1
fi

echo "==> 1/6 复制项目到 $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR"/. "$APP_DIR"/
cd "$APP_DIR"

echo "==> 2/6 安装 Python 依赖"
if [[ ! -x .venv/bin/python3 ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "==> 3/6 构建前端（web/dist，v0.8.0 起 Dashboard 必需）"
if command -v npm >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  cd web
  npm ci -q || { echo "错误: npm ci 失败，可尝试镜像: npm config set registry https://registry.npmmirror.com"; exit 1; }
  npm run build -q || { echo "错误: 前端构建失败，请检查上方 npm 输出"; exit 1; }
  cd "$APP_DIR"
  if [[ ! -f web/dist/index.html ]]; then
    echo "错误: 前端构建未产出 web/dist/index.html"
    exit 1
  fi
  echo "    前端构建完成: web/dist"
else
  echo "错误: 未检测到 Node.js/npm——v0.8.0 起 Dashboard 前端（Vue 3）需要 Node.js LTS 构建，否则服务无法启动。"
  echo "请先安装 Node.js LTS（https://nodejs.org/）后重新运行本脚本。"
  exit 1
fi

echo "==> 4/6 生成 data/config.json（server 模式）"
mkdir -p data
cat > data/config.json <<EOF
{
  "mode": "server",
  "domain": "$DOMAIN",
  "host": "0.0.0.0",
  "port": $PORT,
  "auth_token": "$TOKEN"
}
EOF

echo "==> 5/6 安装 systemd 服务"
sed -e "s|/opt/tavily|$APP_DIR|g" \
    deploy/linux-server/tavily-dashboard.service > /etc/systemd/system/tavily-dashboard.service
cp deploy/linux-server/tavily-mcp.service /etc/systemd/system/tavily-mcp.service
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$APP_DIR|" /etc/systemd/system/tavily-mcp.service
systemctl daemon-reload
systemctl enable --now tavily-dashboard
systemctl enable tavily-mcp || true
echo "    Dashboard 服务已启动: systemctl status tavily-dashboard"
echo "    MCP 服务已注册(默认不启动): systemctl start tavily-mcp"

echo "==> 6/6 生成 Nginx 反向代理配置"
if command -v nginx >/dev/null 2>&1; then
  sed -e "s/your-domain.example.com/$DOMAIN/g" \
      -e "s|127.0.0.1:8000|127.0.0.1:$PORT|g" \
      deploy/linux-server/nginx.conf.example > /etc/nginx/conf.d/tavily.conf
  if nginx -t 2>/dev/null; then
    systemctl reload nginx || true
    echo "    Nginx 已配置并重载: http://$DOMAIN"
  else
    echo "    Nginx 配置已写入 /etc/nginx/conf.d/tavily.conf，但 nginx -t 校验未通过，请手动检查后 reload。"
  fi
else
  echo "    未检测到 Nginx，跳过。可稍后手动部署 deploy/linux-server/nginx.conf.example"
fi

echo ""
echo "部署完成！"
echo "  - 访问地址: http://$DOMAIN"
echo "  - 配置文件: $APP_DIR/data/config.json"
echo "  - 管理命令: systemctl {status|restart|stop} tavily-dashboard"
