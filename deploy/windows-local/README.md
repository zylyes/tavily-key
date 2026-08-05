# Tavily Key Pool — Windows 本地版

面向 **Windows 本机/局域网**提供服务，默认监听 `0.0.0.0`，局域网内设备可直接访问。适合个人开发机或内网环境。

提供两种使用形态：**源码运行**（开发/调试）或 **打包成 onedir 应用文件夹**（免安装分发）。

## 一、打包成 Windows 应用（推荐，免安装）

在项目根目录执行一键打包（需联网安装 PyInstaller）：

```bat
scripts\build_win.bat
```

产物位于 `out\dist\Tavily\` 目录（onedir 应用文件夹）：

| 路径 | 用途 |
| --- | --- |
| `out\dist\Tavily\Tavily.exe` | **主程序**：双击运行打开控制台面板，内置 MCP 服务开关/设置，MCP 服务默认监听局域网 `0.0.0.0:8001` |
| `out\dist\Tavily\_internal\` | 打包的库与静态资源（必须与 exe 放在一起，勿删） |
| `out\dist\Tavily\data\` | 运行时数据（首次运行自动生成） |

**特点**：

- onedir 文件夹免安装，无需本机装 Python/依赖即可运行（启动不再解压临时目录，退出不会弹 `_MEI` 删除失败对话框）；
- 首次运行会在 **exe 同目录的 `data\` 文件夹**自动生成 `config.json`（默认局域网模式）与 `tavily_keys.db`；
- 面板「MCP 服务」页可启动/停止 MCP 服务，**服务地址自动复制到剪贴板**；
- 可通过 Web 设置页或直接编辑 `data\config.json` 修改端口/域名/访问令牌/MCP 配置；
- 分发时**整体拷贝 `out\dist\Tavily\` 文件夹**（或压缩为 zip）到目标机器即可。

> 打包注意事项：MCP Server 依赖 `mcp` 包（**必须为 1.x**，2.x 已移除 FastMCP API，见 `requirements.txt` 版本约束）。打包脚本优先使用 `.venv` 环境执行。MCP 服务日志写入 exe 同目录 `data\mcp_server.log`。

## 二、源码运行

```bat
:: 启动 Dashboard（Web 界面，同时管理 MCP 服务）
deploy\windows-local\start_dashboard.bat
```

启动后浏览器访问 `http://127.0.0.1:8000`（端口以 `data/config.json` 为准，可在设置页修改）。
MCP 服务在面板「MCP 服务」页启动/停止（SSE / Streamable HTTP，默认 `0.0.0.0:8001`，局域网可用）。

## 首次使用

```powershell
# 安装依赖（项目根目录执行）
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 生成 local 模式配置（首次启动也会自动生成 data/config.json）
New-Item -ItemType Directory -Force data | Out-Null
Copy-Item deploy\windows-local\config.local.json data\config.json
```

## 开机自启（任务计划）

以**管理员**身份打开 PowerShell：

```powershell
.\deploy\windows-local\install_service.ps1          # 安装并立即启动
.\deploy\windows-local\install_service.ps1 -Uninstall  # 卸载
```

使用 Windows「任务计划程序」注册 `TavilyDashboard` 任务，开机自动启动、崩溃自动重启，无需安装额外工具。

### 可选：注册为真正的 Windows 服务（NSSM）

```powershell
# 下载 NSSM (https://nssm.cc)，然后：
nssm install TavilyDashboard "<项目根目录>\.venv\Scripts\python.exe" "app\dashboard.py"
nssm set TavilyDashboard AppDirectory "<项目根目录>"
nssm start TavilyDashboard
```

## 局域网访问（默认开启）

默认监听 `0.0.0.0`，局域网内其他设备可直接访问：

1. 控制台地址：`http://<本机IP>:8000`（设置页自动显示局域网地址）；
2. MCP 服务地址：`http://<本机IP>:8001/sse`（或 `/mcp`，面板自动复制）；
3. 在 Windows 防火墙中放行对应端口（如 8000、8001）。

如需仅本机访问，将 `host`/`mcp_host` 改为 `127.0.0.1` 后重启。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `start_dashboard.bat` | Dashboard 启动脚本（管理面板 + MCP 服务） |
| `install_service.ps1` | 开机自启安装/卸载脚本（任务计划） |
| `config.local.json` | local 模式配置模板 |

## 安全建议

- 局域网共享时请在设置页设置**访问令牌**，保护 `/api/*` 接口。
- 修改 `data/config.json`（或设置页的 Host/端口/MCP 配置）后需重启服务（或重新启动 MCP 服务）生效。
- MCP 服务由面板管理；关闭软件时自动停止 MCP 服务。
