# Changelog

本项目所有重要变更均记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.2] - 2026-08-05

### Fixed

- **请求日志大量「零消耗」误导**：`request_log` 新增 `usage_source` 字段区分积分来源——
  - `response`：接口响应含 `usage`（search/extract/crawl/map 正常记录实际积分）；
  - `unknown`：接口响应**不含** `usage`（官方 Research API `POST /research` 与 `GET /research/{id}` 均无该字段），本地无法得知实际消耗，面板显示「—」并提示以官方用量为准，不再误导为「消耗 0」；
  - `none`：失败请求（不消耗积分）。
- **suspected_leak 误报**：`_local_credits()` 本地对账排除 `usage_source='unknown'` 的 research 记录，且官方 `/usage` 的 `research_usage` 落库（`api_keys.research_usage` 列，自动迁移）；对账公式为 `官方总用量 − 官方 research − 本地可对账积分`，research 消耗（一次 4~250 积分，官方只在 `/usage` 报告）不再导致误报「疑似泄露」，真实泄露仍可检出。
- **extract/crawl/map 零消耗说明**：官方批量计费（extract 每 5 成功 URL、map 每 10 页、crawl 共享下限）在未达下限前 `usage.credits` 合法返回 0，属官方规则，按 `response`（真 0）显示。
- **`country` 参数兼容池内多 Key 行为不一致**：官方 `/search` 的 `country` 枚举为**完整国家名**（如 `united states`），但池内部分 Key 走旧版接口只认**两位 ISO 代码**（如 `us`），单一格式无法通吃导致 `Invalid country. Must be a valid country ...` 失败。
  - `country` 现在接受两种格式并自动归一化为官方完整国家名：两位 ISO 码（`us`/`cn`/`jp`）、完整国家名（`United States`/`china`），另支持常见别名（`usa`/`uk`/`uae`）；映射表覆盖官方全部枚举。
  - 若目标 Key 拒绝当前格式（`Invalid country` 错误），自动在「完整名 ↔ 两位码」间切换并换 key 重试，客户端无需关心池内差异。

### Changed

- 面板请求日志「积分」列：失败显示 `-`；`unknown` 显示 `—`（悬停提示）；其余显示实际积分。

## [0.3.1] - 2026-08-05

### Fixed

- **Research 任务状态查询 404**：Tavily 任务按 key 隔离，`tavily_research_status` 优先使用提交任务时的同一 key 查询（key 失效/耗尽才回退轮询）；key 映射持久化到 `data/research_keys.json`（上限 1000 条），重启 MCP 服务后仍可查询
- **MCP 服务事件循环阻塞**：全部 MCP 工具改为异步执行（线程池），同步调用不再阻塞其他请求（此前可能触发 180s 超时）；`tavily_research` 同步逻辑提取为独立实现
- **requests 无限等待挂起**：给 TavilyClient 会话注入默认 60s 超时（显式传参不受影响），修复 `get_research` 未设超时导致整个服务卡死
- **research 流式路径积分漏记**：积分记录从固定 0 改为实际用量
- **tavily_pool_status 异常中断**：异常时返回错误信息而非抛错中断会话
- **参数触达 API 报 400**：`include_answer`/`include_raw_content` 支持字符串布尔（true/false/1/0/yes/no/on/off）；`search_depth` 枚举忽略大小写；`country` 校验两位 ISO 3166-1 alpha-2，非法值返回本地中文错误、不触达 API

### Changed

- `tavily_crawl` / `tavily_map` 的 `timeout` 默认值 150→60s（保持在 MCP 客户端请求超时之内，显式传参不受影响）
- `tavily_research` 的 `wait=true` 主路径改为原生流式优先，失败才回退「提交 + 轮询」，减少一次无效请求

## [0.3.0] - 2026-08-05

### Added

- **运行时数据统一目录 `data/`**：新增 `app/paths.py`，所有可写数据（`config.json`、`tavily_keys.db`、`*.log`、`.tavily-secret.key`）集中到 `data/`（打包版为 exe 同目录 `data\`，开发版为项目根 `data/`）；首次运行自动把旧位置文件迁移到 `data/`（config.json 键合并源优先、迁移失败保留双份不丢数据、幂等且线程安全）
- **Research 流式回退**：`tavily_research` 提交遇到官方要求流式响应（`research_stream_required`）时自动以 `stream=True` 重跑并组装报告，兼容官方 SDK 新版行为
- **MCP 子进程管理加固**：进程归属追踪（stop 只清理本程序拉起的子进程，不误杀第三方占用）、netstat PID 查询 TTL 缓存、启动后存活探测与日志尾部回读（面板不再误报「已启动」）

### Changed

- **DNS rebinding 防护**：MCP 网络模式显式关闭 DNS rebinding 保护（默认开启会让局域网 IP 访问 `/sse` 返回 421）；以局域网/内网可用性优先，公网部署请保持反向代理 + 访问令牌鉴权
- **Linux 部署脚本与文档**：`install.sh` 及部署文档统一改为生成/读写 `data/config.json`

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

[0.3.2]: https://github.com/zylyes/tavily-key/releases/tag/v0.3.2
[0.3.1]: https://github.com/zylyes/tavily-key/releases/tag/v0.3.1
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
