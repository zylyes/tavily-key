# Changelog

本项目所有重要变更均记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.1] - 2026-08-05

### Fixed

- **Windows 打包版退出弹窗**：修复关闭窗口时弹出 `Failed to remove temporary directory: %TEMP%\_MEIxxx` 对话框（PyInstaller onefile 退出时临时目录删除失败）；打包版退出改为静默终止，并在下次启动时自动清理残留临时目录

### Changed

- **Windows 打包改为 onedir**：`Tavily.spec` 增加 `COLLECT`，产物从单文件 `Tavily.exe` 变为 `out\dist\Tavily\` 应用文件夹（`Tavily.exe` + `_internal\` + `data\`）。不再解压 `%TEMP%\_MEI*` 临时目录，彻底消除该弹窗且启动更快；`build_win.bat` 改用 `Tavily.spec` 构建，重建时自动保留 `data\` 运行数据

## [0.2.0] - 2026-08-05

### Added

- **额度耗尽（432）自动切换**：MCP 工具命中 432/433 自动把 key 标记为 `exhausted` 并移出轮询，请求自动重试到下一个可用 key；`/usage` 同步检测到 usage 归零（新计费周期）自动恢复
- **Key 异常识别**：结合本地 `request_log` 与官方 `/usage` 对账，自动识别 6 类异常（额度耗尽/近耗尽、疑似泄露、高错误率、静默失效、延迟异常）；面板标记与筛选、`tavily_pool_status` 带异常摘要、CLI 新增 `audit` 子命令
- **按 key 令牌桶限流**：`rate_limit_rpm`（默认 90）控制单 key 请求速率，受限自动切换其他 key，全部受限时短暂等待
- **聚合容量视图**：`/api/usage/aggregate` 与 `get_aggregate()` 统计剩余总积分/已用/可用 key 数；面板顶栏展示「剩余积分」
- **`/usage` 同步 TTL 缓存**：`usage_cache_ttl`（默认 60s）避免重复拉取浪费各账户限流预算；落库保存 `plan / plan_usage / plan_limit`
- **Search 新参数**：`auto_parameters`、`fast/ultra-fast` 深度、`include_answer`/`include_raw_content` 支持字符串枚举
- **Research 异步化**：`tavily_research` 新增 `wait` 参数（`wait=false` 提交即返回 request_id），新增 `tavily_research_status` 工具查询任务状态；提交阶段 quota/auth 错误自动切换 key 重试
- **MCP 默认参数**：`mcp_default_parameters` 配置对 search 类请求注入默认值（对齐官方 `DEFAULT_PARAMETERS`），显式传值优先
- **可观测性**：`request_log` 新增 `request_id` 字段（旧库自动迁移），面板日志展示请求 ID
- **CLI**：新增 `usage`（查看/同步官方用量）与 `audit`（列出异常 key）子命令
- **配置项**：`rate_limit_rpm`、`rate_limit_max_wait`、`usage_cache_ttl`、`anomaly_thresholds`、`mcp_default_parameters`、`mcp_human_id`

### Changed

- MCP 工具统一走 `_run_with_retry`：失败记录含 request_id，quota/auth 类错误自动切换 key 重试
- `_classify_error` 扩展为 auth / quota / rate / other 四类
- 数据库 schema 新增 `is_exhausted`、`plan`、`plan_usage`、`plan_limit`、`usage_synced_at`、`request_id` 列（自动迁移兼容旧库）

## [0.3.0] - 2026-08-05

### Added

- **运行时数据统一目录 `data/`**：新增 `app/paths.py`，所有可写数据（`config.json`、`tavily_keys.db`、`*.log`、`.tavily-secret.key`）集中到 `data/`（打包版为 exe 同目录 `data\`，开发版为项目根 `data/`）；首次运行自动把旧位置文件迁移到 `data/`（config.json 键合并源优先、迁移失败保留双份不丢数据、幂等且线程安全）
- **Research 流式回退**：`tavily_research` 提交遇到官方要求流式响应（`research_stream_required`）时自动以 `stream=True` 重跑并组装报告，兼容官方 SDK 新版行为
- **MCP 子进程管理加固**：进程归属追踪（stop 只清理本程序拉起的子进程，不误杀第三方占用）、netstat PID 查询 TTL 缓存、启动后存活探测与日志尾部回读（面板不再误报「已启动」）

### Changed

- **DNS rebinding 防护**：MCP 网络模式显式关闭 DNS rebinding 保护（默认开启会让局域网 IP 访问 `/sse` 返回 421）；以局域网/内网可用性优先，公网部署请保持反向代理 + 访问令牌鉴权
- **Linux 部署脚本与文档**：`install.sh` 及部署文档统一改为生成/读写 `data/config.json`

[0.3.0]: https://github.com/zylyes/tavily-key/releases/tag/v0.3.0
[0.2.1]: https://github.com/zylyes/tavily-key/releases/tag/v0.2.1
[0.2.0]: https://github.com/zylyes/tavily-key/releases/tag/v0.2.0

## [0.1.0] - 2026-08-05

### Added

- **Key 池管理**：批量导入（CLI 或 Web UI 粘贴）、自动轮询分发（round-robin / least-used）、健康检查自动停用失效 Key
- **用量追踪**：请求数、错误数、积分消耗统计与请求日志
- **Web 控制台（Dashboard）**：Key 列表、请求日志、健康检查、用量统计、部署设置，基于 FastAPI
- **MCP 服务一体化管理**：面板内置 MCP 服务启停与设置（sse / streamable-http / stdio 三种传输方式），支持局域网访问，服务地址自动复制
- **两套部署形态**：同一代码库通过 `config.json` 切换
  - Linux Server 版：云服务器 + 域名对外服务（systemd + Nginx 反向代理）
  - Windows Local 版：PyInstaller 单文件 `Tavily.exe`，面板 + MCP 服务一体，默认局域网可用
- **访问鉴权**：可设置访问令牌（`X-Auth-Token`），保护 `/api/*` 接口，适配公网部署

[0.1.0]: https://github.com/zylyes/tavily-key/releases/tag/v0.1.0
