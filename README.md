# Tavily API Key Pool

管理多个 Tavily API key，自动轮询分发，追踪用量。提供 Web 控制台（Dashboard）与 MCP Server 两种使用方式。

## 功能特性

- **Key 池管理**：批量导入、自动轮询（round-robin / least-used）、健康检查自动停用失效 Key、额度耗尽自动切换
- **用量追踪**：请求数、错误数、积分消耗、请求日志、官方用量同步对账、用量趋势图
- **Key 异常识别**：自动识别额度耗尽/疑似泄露/高错误率等 6 类异常，Webhook / 托盘气泡通知
- **Web 控制台**：Key 列表、请求日志（筛选/导出）、统计、Research 任务看板、健康检查、部署设置
- **MCP 服务一体化管理**：面板内置 MCP 服务开关/设置（sse / streamable-http / stdio），局域网 IP / 主机名 / 本机三种地址展示与复制
- **网络搜索代理（Tavily 兼容 REST）**：`/search`、`/extract`、`/crawl`、`/map`、`/research`（含 `GET /research/{id}`）官方端点形态，Cherry Studio 等客户端以「自定义 API 地址 + 密钥」直连 Key 池，错误按官方风格映射（401/432/429/503）
- **Research 增强**：异步任务（wait / status）、流式输出、结构化输出（output_schema）、报告长度与来源数控制
- **数据备份与恢复**：CLI 与面板一键备份/恢复 `data/`（配置、Key 池、加密密钥、research 缓存），恢复前自动保留 `.pre-restore` 副本，绝不静默覆盖
- **按端点分组限流**：`endpoint_rpm` 按接口独立限流（research 创建任务独立 18 RPM），未配置接口回退 `rate_limit_rpm`
- **访问鉴权**：可设置访问令牌，保护公网部署下的 `/api/*` 接口
- **两套部署形态**：同一代码库，通过 `data/config.json` 切换
  - **Linux Server 版**：云服务器 + 域名对外服务（systemd + Nginx 反向代理）
  - **Windows Local 版**：onedir 应用文件夹（Tavily.exe + _internal），面板 + MCP 服务一体，默认局域网可用

## 安装

```bash
pip install -r requirements.txt
```

依赖：tavily-python、mcp、fastapi、uvicorn。

> **前端构建（v0.8.0 起必需）**：Dashboard 前端已重构为 Vue 3 工程（`web/`），**源码运行需先构建** `web/dist`（git 克隆不含构建产物）：
>
> ```bash
> cd web && npm ci && npm run build
> ```
>
> 需要 Node.js LTS；`web/dist` 缺失时 dashboard 启动会明确报错并提示构建命令。Windows 打包（`scripts\build_win.bat`）会自动执行前端构建。

## 目录结构

```
tavily/
├── app/               # Python 源码（cli / dashboard / mcp / 核心逻辑）
├── web/               # 前端工程（Vue 3 + Vite + TS，构建产物 web/dist 由 dashboard 托管）
├── assets/            # 图标等静态资源
├── scripts/           # 构建与启动脚本（build_win.bat / run_*.sh / run_dashboard.bat）
├── deploy/            # 两套部署包（linux-server / windows-local）
├── docs/              # 项目文档（wiki）
├── tests/             # 单元测试
├── data/              # ★ 运行时数据（自动生成）：config.json / tavily_keys.db / *.log / 密钥
├── out/               # ★ 构建产物（自动生成）：out/build（中间）+ out/dist/Tavily（onedir 应用文件夹）
├── Tavily.spec        # PyInstaller 构建配置（Windows 打包）
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

启动方式自动读取 `data/config.json`（首次启动自动生成默认配置）。开发模式首次运行前需先构建前端（见[安装](#安装)章节）；前端热开发可用 `cd web && npm run dev`（5173 端口，`/api` 自动代理到本服务）。

```bash
# Linux / macOS
./scripts/run_dashboard.sh            # 按 data/config.json 启动
./scripts/run_dashboard.sh 8080       # 覆盖端口

# Windows
scripts\run_dashboard.bat             # 或 deploy\windows-local\start_dashboard.bat
```

> **Windows 打包版（推荐）**：进入 `out\dist\Tavily\` 后双击 `Tavily.exe` 直接打开**原生应用窗口**
> （WebView2 网页套壳，不再跳转系统浏览器），关闭窗口即自动停止服务并退出；
> 如需无界面纯服务模式（供局域网/远程访问），运行 `Tavily.exe --server`。

或手动启动（host/port 覆盖配置）：

```bash
(cd app && uvicorn dashboard:app --host 0.0.0.0 --port 8000)
```

界面显示：key 列表、请求日志、**统计**、**Research 任务**、MCP 服务、**搜索代理**、**设置**。

## MCP 服务管理（供 AI Agent 调用）

对外提供 tavily-search/extract/crawl/map/research/research_status/pool_status 等工具，自动轮询 key。

**Windows 打包版（推荐）**：双击 `out\dist\Tavily\Tavily.exe` 打开**原生应用窗口**（内置面板，WebView2 网页套壳）→ 顶部「**MCP 服务**」标签页：

- **启动 / 停止开关**：一键启停 MCP 服务（SSE / Streamable HTTP 网络模式）
- **地址自动复制**：服务启动后自动复制并显示服务地址（局域网 IP / 主机名 / 本机三种地址，切换网络后主机名地址不变）
- **随软件启动**：开启后，软件启动时自动拉起 MCP 服务；关闭软件时自动停止
- **可设置项**：传输方式（sse / streamable-http / stdio）、监听地址、端口、项目/会话归属 ID
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

## 搜索代理（Tavily 兼容 REST 服务）

把 Key 池暴露为官方 Tavily API 形态（`POST /search`、`POST /extract`、`POST /crawl`、`POST /map`、`POST /research`、`GET /research/{id}`），供 Cherry Studio 等 AI 客户端通过「自定义 API 地址」直接对接；内部走 Key 池轮询/限流/异常切换，额度与日志自动落账。

> 不提供 `GET /usage`：官方用量展示请使用面板统计页「更新用量」（内部对每个 Key 单独查官方 `/usage` 聚合），代理不转发用量查询。

**启动方式**：

- 面板「**搜索代理**」页签一键启停（或开启「随软件启动」）；默认监听 `0.0.0.0:8002`
- 命令行：`python app/dashboard.py --proxy`（打包版 `Tavily.exe --proxy`）

**对接步骤**（以 Cherry Studio 网络搜索 → Tavily 提供商为例）：

1. 面板「搜索代理」页复制 API 地址（局域网 IP / 主机名 / 本机三形式），填入客户端「API 地址」（如 `http://<主机>:8002`）
2. 复制代理密钥填入「API 密钥」（密钥为空时对外开放，面板会提示风险）
3. 点「检测」验证连通即可

**鉴权与错误映射**：客户端带 `Authorization: Bearer <proxy_token>`（或 body `api_key` 字段）；错误按类别映射为 Tavily 风格 `{"detail":{"error":...}}`——auth→401、quota→432、rate→429、参数错误→400、池空→503。

> ⚠️ **安全提示**：`proxy_token` 留空时搜索代理不鉴权，任何可达设备都可消耗 Key 池额度；公网/局域网共享部署必须设置强密钥。

## 设置（域名绑定 / 部署模式 / 鉴权 / MCP）

Web 控制台右上角「**设置**」标签页可配置：

| 配置项 | 说明 |
| --- | --- |
| 部署模式 | `server`（Linux 域名对外服务）/ `local`（Windows 本地） |
| 绑定域名 | 仅界面展示访问地址与部署指引用途 |
| 监听地址 Host | `127.0.0.1` 仅本机 / `0.0.0.0` 所有网卡 |
| 端口 | 服务监听端口 |
| 访问令牌 | 设置后所有 `/api/*` 请求需携带 `X-Auth-Token` 头 |
| 界面主题 | `theme_mode`：`system`（跟随 Windows 主题）/ `light` / `dark`，WebView 开屏背景同步 |

「**MCP 服务**」标签页额外可配置：

| 配置项 | 说明 |
| --- | --- |
| 随软件启动 | 开启后软件启动自动拉起 MCP 服务，关闭软件自动停止 |
| 传输方式 | `sse` / `streamable-http`（局域网，可由面板启停）/ `stdio`（本机直连） |
| 监听地址 | `0.0.0.0`（局域网） / `127.0.0.1`（仅本机） |
| 端口 | MCP 服务端口（默认 8001） |
| 项目/会话归属 ID | 可选，转发 `X-Project-ID` / `X-Human-Id` 头，便于 Tavily 侧按项目归类用量与会话分析 |
| 默认参数（JSON） | 对 search 类请求注入默认参数（如 `search_depth=advanced`），客户端显式传值优先；「推荐预设」一键填入官方建议值；注意 `auto_parameters` 可能自动升级深度至 advanced（每次 2 积分） |

「**搜索代理**」标签页可配置：

| 配置项 | 说明 |
| --- | --- |
| 随软件启动 | 开启后软件启动自动拉起搜索代理，关闭软件自动停止 |
| 监听地址 | `0.0.0.0`（局域网） / `127.0.0.1`（仅本机） |
| 端口 | 代理服务端口（默认 8002） |
| 代理密钥 | `proxy_token`，客户端 Bearer 鉴权；**留空则不鉴权（对外开放，面板会提示风险）** |

等价地可手动编辑 `data/config.json`：

```json
{
  "mode": "local",
  "domain": "",
  "host": "0.0.0.0",
  "port": 8000,
  "auth_token": "",
  "theme_mode": "system",
  "mcp_auto_start": false,
  "mcp_transport": "sse",
  "mcp_host": "0.0.0.0",
  "mcp_port": 8001,
  "mcp_human_id": "",
  "mcp_project_id": "",
  "log_retention_days": 90,
  "notify_webhook": "",
  "notify_tray": true,
  "notify_interval_minutes": 5,
  "proxy_auto_start": false,
  "proxy_host": "0.0.0.0",
  "proxy_port": 8002,
  "proxy_token": "",
  "endpoint_rpm": {"search": 90, "extract": 90, "crawl": 90, "map": 90, "research": 18}
}
```

> 首次启动自动生成完整默认配置；以上为常用项示例，更多配置（限流、异常阈值、MCP 默认参数等）见 `data/config.json` 自动生成内容。
> `endpoint_rpm` 为每 key × 每接口的限流（research 创建任务官方上限 20 RPM，默认留 10% 余量）；未配置的接口回退 `rate_limit_rpm`（默认 90）。
> **配置热刷新**：`mcp_token`、`mcp_default_parameters`、`mcp_human_id`/`mcp_project_id`、`endpoint_rpm`、`proxy_token` 修改后**无需重启 MCP / 搜索代理即生效**（子进程每请求热读 config.json）；仅 `mcp_transport`/`mcp_host`/`mcp_port`、`proxy_host`/`proxy_port` 变更需重启（无法热绑定端口）。
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
python app/cli.py usage --sync            # 同步/查看官方用量
python app/cli.py audit                   # 异常 key + 近24h接口/来源统计 + Research 任务概览
python app/cli.py proxy                   # 搜索代理状态/地址/密钥
python app/cli.py backup                  # 备份 data/ 为 zip（默认系统临时目录）
python app/cli.py restore <备份.zip>      # 从备份恢复 data/
python app/cli.py health                  # 健康检查，自动停用失效 key
python app/cli.py recent -n 20            # 最近 20 条请求日志
python app/cli.py activate tvly-xxx****yyy  # 启用 key
python app/cli.py deactivate tvly-xxx****yyy  # 停用 key
python app/cli.py remove tvly-xxx****yyy      # 删除 key
```

## 数据库

SQLite 文件 `data/tavily_keys.db`，自动创建。含两张表：

- `api_keys` — key 列表、用量、状态（含官方套餐/用量同步、耗尽标记、异常计数）
- `request_log` — 请求日志（含 request_id、积分来源 usage_source、客户端错误标记 is_client_error、请求来源 source：mcp/proxy/cli）

删除该文件不影响功能，下次启动自动重建空库。

## 数据备份与恢复

`data/` 目录一键备份/恢复为 zip：

- **备份内容**：`config.json`、`tavily_keys.db`(+wal)、`research_keys.json`、`research_tasks_cache.json`、`.tavily-secret.key`（存在时才备份——Windows DPAPI / 无加密后端下不生成，属正常；不含日志）
- **CLI**：`python app/cli.py backup [路径]`（默认系统临时目录）、`python app/cli.py restore <备份.zip>`
- **面板**：「设置」页「数据备份与恢复」卡片——下载备份 / 上传恢复（恢复自动停止 MCP 与搜索代理子进程并释放数据库连接）

> 注意事项：恢复前建议先停止 MCP / 搜索代理服务（面板恢复会自动停，CLI 需手动停）；恢复时现有文件会保留为 `.pre-restore-<时间戳>` 副本，绝不静默覆盖；备份 zip 含加密密钥与访问令牌等敏感配置，请加密保管。

## 开机自启

- **Linux（域名对外服务）**：使用 [`deploy/linux-server/`](deploy/linux-server/README.md) 中的 `tavily-dashboard.service`（systemd）与 `install.sh` 一键部署。
- **Windows**：以管理员运行 [`deploy/windows-local/install_service.ps1`](deploy/windows-local/README.md) 注册任务计划开机自启。

## 配置文件

| 文件 | 用途 |
| --- | --- |
| `data/config.json` | 部署 + MCP 设置（mode/domain/host/port/auth_token/mcp_*），首次启动自动生成 |
| `web/` | 前端工程（Vue 3 + Vite，`npm run build` 产出 `web/dist` 供 dashboard 托管） |
| `app/settings.py` | 配置读写模块（含局域网地址推导） |
| `app/notify.py` | 异常通知（Webhook / Windows 托盘气泡，去重节流） |
| `app/cache.py` | 通用 TTL 缓存基础设施（KeyPool/Dashboard 接口缓存，跨进程失效） |
| `app/backup.py` | data/ 备份与恢复（CLI backup/restore + 面板接口） |
| `app/tavily_proxy.py` | 搜索代理本体（Tavily 兼容 REST 服务） |
| `app/proxy_manager.py` | 搜索代理子进程管理（面板启停、端口检测） |
| `app/mcp_manager.py` | MCP 服务子进程管理（面板启停、端口检测） |
| `app/mcp_server.py` | MCP 服务本体（sse / streamable-http / stdio） |
| `deploy/linux-server/` | Linux Server 版部署包（systemd + Nginx） |
| `deploy/windows-local/` | Windows Local 版部署包（启动脚本 + 开机自启） |
