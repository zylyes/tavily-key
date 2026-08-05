# Tavily Key Pool — Linux Server 版

面向 **Linux 云服务器**，通过**域名**对外提供服务。适合部署在公网 VPS，供多人/多 Agent 访问。

## 部署前准备

1. 一台 Linux 服务器（Ubuntu / Debian / CentOS 均可），安装 `python3`、`python3-venv`、`pip`。
2. 一个已备案/可访问的域名，将其 **A 记录**解析到服务器公网 IP。
3. （推荐）安装 Nginx：`sudo apt install nginx`。

## 一键部署

```bash
cd deploy/linux-server
sudo ./install.sh --domain api.example.com --port 8000 --token 你的访问令牌
```

脚本会自动完成：

| 步骤 | 说明 |
| --- | --- |
| 复制项目 | 复制到 `/opt/tavily`（可用 `--dir` 修改） |
| 安装依赖 | 创建 `.venv` 并安装 `requirements.txt` |
| 生成配置 | 生成 `data/config.json`（mode=server，绑定域名，监听 0.0.0.0） |
| 安装服务 | 注册并启动 `tavily-dashboard` systemd 服务，注册 `tavily-mcp` |
| Nginx | 生成 `/etc/nginx/conf.d/tavily.conf` 反向代理绑定域名 |

## 手动部署（不跑一键脚本）

```bash
# 1. 安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 生成 server 模式配置
mkdir -p data
cp deploy/linux-server/config.server.json data/config.json
# 编辑 data/config.json: 填写 domain、auth_token，host 保持 0.0.0.0

# 3. 安装 systemd 服务
sudo cp deploy/linux-server/tavily-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tavily-dashboard

# 4. 配置 Nginx 反向代理（绑定域名）
sudo cp deploy/linux-server/nginx.conf.example /etc/nginx/conf.d/tavily.conf
# 编辑: server_name 换成你的域名，proxy_pass 端口与 data/config.json 一致
sudo nginx -t && sudo systemctl reload nginx
```

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `install.sh` | 一键部署脚本 |
| `config.server.json` | server 模式配置模板 |
| `tavily-dashboard.service` | Dashboard systemd 服务（读 data/config.json 启动） |
| `tavily-mcp.service` | MCP Server systemd 服务（默认不启动，需时手动启动） |
| `nginx.conf.example` | Nginx 反向代理 + 域名绑定模板（含 HTTPS 示例） |

## 常用运维命令

```bash
systemctl status tavily-dashboard      # 查看状态
systemctl restart tavily-dashboard     # 重启
journalctl -u tavily-dashboard -n 100  # 查看日志
systemctl start tavily-mcp             # 启动 MCP
```

## 安全建议

- 务必在**设置页**设置**访问令牌**，防止公网裸奔。
- 如需 HTTPS，推荐 `certbot --nginx -d api.example.com` 一键签发并自动续期。
- 修改 `data/config.json` 后需 `systemctl restart tavily-dashboard` 生效。
