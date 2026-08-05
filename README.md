# Tavily API Key Pool

管理多个 Tavily API key，自动轮询分发，追踪用量。提供 Web 控制台（Dashboard）与 MCP Server 两种使用方式。

## 功能特性

- **Key 池管理**：批量导入、自动轮询（round-robin / least-used）、健康检查自动停用失效 Key
- **用量追踪**：请求数、错误数、积分消耗、日志
- **Web 控制台**：Key 列表、请求日志、健康检查、用量统计、部署设置
- **MCP 服务一体化管理**：面板内置 MCP 服务开关/设置，支持局域网访问，地址自动复制
- **访问鉴权**：可设置访问令牌，保护公网部署下的 `/api/*` 接口
- **两套部署形态**：同一代码库，通过 `config.json` 切换
  - **Linux Server 版**：云服务器 + 域名对外服务（systemd + Nginx 反向代理）
  - **Windows Local 版**：单一软件（Tavily.exe），面板 + MCP 服务一体，默认局域网可用

## 安装

```bash
pip install -r requirements.txt
```

依赖：tavily-python、mcp、fastapi、uvicorn。

## 目录结构

```
tavily/
├── app/               # Python 源码 + 前端资源（cli / dashboard / mcp / 核心逻辑）
├── assets/            # 图标等静态资源
├── scripts/           # 构建与启动脚本（build_win.bat / run_*.sh / run_dashboard.bat）
├── deploy/            # 两套部署包（linux-server / windows-local）
├── docs/              # 项目文档（wiki）
├── dist/              # 打包产物（Tavily.exe）
├── Tavily.spec        # PyInstaller 构建配置（Windows 打包）
├── config.json        # 运行配置（首次启动自动生成）
├── tavily_keys.db     # SQLite 数据库（自动创建）
└── README.md

# 以上为示意结构，未列出全部文件
```

## 导入 key

一行一个 key，放入 `keys.txt`（或任意文本文件）：

```
tvly-xxxxxxxxxxxxx
tvly-yyyyyyyyyyyyy
```

CLI 导入：

```bash
python app/cli.py add --from-file keys.txt
```

Web UI 导入：启动 dashboard 后，点击 "Add API Keys" 粘贴 key。



## 启动 Dashboard（Web 界面）

启动方式自动读取根目录 `config.json`（首次启动自动生成默认配置）。

```bash
# Linux / macOS
./scripts/run_dashboard.sh            # 按 config.json 启动
./scripts/run_dashboard.sh 8080       # 覆盖端口

# Windows
scripts\run_dashboard.bat             # 或 deploy\windows-local\start_dashboard.bat
```

> **Windows 打包版（推荐）**：双击 `Tavily.exe` 直接打开**原生应用窗口**（WebView2 网页套壳，
> 不再跳转系统浏览器），关闭窗口即自动停止服务并退出；
> 如需无界面纯服务模式（供局域网/远程访问），运行 `Tavily.exe --server`。

或手动启动（host/port 覆盖配置）：

```bash
(cd app && uvicorn dashboard:app --host 0.0.0.0 --port 8000)
```

界面显示：key 列表、请求日志、健康检查、用量统计、**MCP 服务**、**设置**。

## MCP 服务管理（供 AI Agent 调用）

对外提供 tavily-search/extract/crawl/map/research 等工具，自动轮询 key。

**Windows 单一软件（推荐）**：双击 `Tavily.exe` 打开**原生应用窗口**（内置面板，WebView2 网页套壳）→ 顶部「**MCP 服务**」标签页：

- **启动 / 停止开关**：一键启停 MCP 服务（SSE / Streamable HTTP 网络模式）
- **地址自动复制**：服务启动后自动复制并显示服务地址（局域网内其他设备用同一地址连接）
- **随软件启动**：开启后，软件启动时自动拉起 MCP 服务；关闭软件时自动停止
- **可设置项**：传输方式（sse / streamable-http / stdio）、监听地址、端口
- 默认监听 `0.0.0.0:8001`，**局域网内设备可直接访问**

**Linux / macOS 独立进程**：

```bash
./scripts/run_mcp.sh                # Linux / macOS（stdio，供 AI 客户端直连）
```

**AI 客户端配置示例**（把 MCP 命令指向网络地址）：

```
类型: SSE                地址: http://<本机IP>:8001/sse
类型: Streamable HTTP    地址: http://<本机IP>:8001/mcp
```

> stdio 模式由 AI 客户端直接拉起进程（本机直连），无法由面板启停；如需给 AI 客户端配置本机 stdio 直连，将 MCP 命令设为 `Tavily.exe --mcp` 且 `mcp_transport` 设为 `"stdio"`。

## 设置（域名绑定 / 部署模式 / 鉴权 / MCP）

Web 控制台右上角「**设置**」标签页可配置：

| 配置项 | 说明 |
| --- | --- |
| 部署模式 | `server`（Linux 域名对外服务）/ `local`（Windows 本地） |
| 绑定域名 | 仅界面展示访问地址与部署指引用途 |
| 监听地址 Host | `127.0.0.1` 仅本机 / `0.0.0.0` 所有网卡 |
| 端口 | 服务监听端口 |
| 访问令牌 | 设置后所有 `/api/*` 请求需携带 `X-Auth-Token` 头 |

「**MCP 服务**」标签页额外可配置：

| 配置项 | 说明 |
| --- | --- |
| 随软件启动 | 开启后软件启动自动拉起 MCP 服务，关闭软件自动停止 |
| 传输方式 | `sse` / `streamable-http`（局域网，可由面板启停）/ `stdio`（本机直连） |
| 监听地址 | `0.0.0.0`（局域网） / `127.0.0.1`（仅本机） |
| 端口 | MCP 服务端口（默认 8001） |

等价地可手动编辑 `config.json`：

```json
{
  "mode": "local",
  "domain": "",
  "host": "0.0.0.0",
  "port": 8000,
  "auth_token": "",
  "mcp_auto_start": false,
  "mcp_transport": "sse",
  "mcp_host": "0.0.0.0",
  "mcp_port": 8001
}
```

> 修改 `host`/`port` 需重启服务生效；`mode`/`domain`/`auth_token` 即时生效。
> MCP 服务的传输方式/端口修改后，需在「MCP 服务」页点击「启动服务」（或重启软件）生效。

## 两套部署版本

- **Linux Server 版（域名对外服务）** → [`deploy/linux-server/`](deploy/linux-server/README.md)，一键脚本 `install.sh` 部署 systemd 服务 + Nginx 反向代理绑定域名。
- **Windows Local 版（本机服务）** → [`deploy/windows-local/`](deploy/windows-local/README.md)，启动脚本 + 任务计划开机自启。

详见 [`deploy/README.md`](deploy/README.md)。

## CLI 常用命令

```bash
python app/cli.py list                    # 列出所有 key
python app/cli.py list --active           # 仅活跃 key
python app/cli.py stats                   # 用量统计
python app/cli.py health                  # 健康检查，自动停用失效 key
python app/cli.py recent -n 20            # 最近 20 条请求日志
python app/cli.py activate tvly-xxx****yyy  # 启用 key
python app/cli.py deactivate tvly-xxx****yyy  # 停用 key
python app/cli.py remove tvly-xxx****yyy      # 删除 key
```

## 数据库

SQLite 文件 `tavily_keys.db`，自动创建。含两张表：

- `api_keys` — key 列表、用量、状态
- `request_log` — 请求日志

删除该文件不影响功能，下次启动自动重建空库。

## 开机自启

- **Linux（域名对外服务）**：使用 [`deploy/linux-server/`](deploy/linux-server/README.md) 中的 `tavily-dashboard.service`（systemd）与 `install.sh` 一键部署。
- **Windows**：以管理员运行 [`deploy/windows-local/install_service.ps1`](deploy/windows-local/README.md) 注册任务计划开机自启。

## 配置文件

| 文件 | 用途 |
| --- | --- |
| `config.json` | 部署 + MCP 设置（mode/domain/host/port/auth_token/mcp_*），首次启动自动生成 |
| `app/settings.py` | 配置读写模块（含局域网地址推导） |
| `app/mcp_manager.py` | MCP 服务子进程管理（面板启停、端口检测） |
| `app/mcp_server.py` | MCP 服务本体（sse / streamable-http / stdio） |
| `deploy/linux-server/` | Linux Server 版部署包（systemd + Nginx） |
| `deploy/windows-local/` | Windows Local 版部署包（启动脚本 + 开机自启） |
