# Changelog

本项目所有重要变更均记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.7.0] - 2026-08-06

### Added

- **统一配置热刷新**：`settings.get_settings_fresh(ttl=1s)`——stat
  `config.json` 的 mtime+size 指纹，文件变化才 `reload()`（比无条件 TTL
  reload 更省）。MCP 子进程与搜索代理统一复用：
  - MCP 的 `mcp_token`（Bearer 鉴权中间件每次请求热读，面板设置/修改/清空
    令牌**无需重启 MCP** 即生效）、`mcp_default_parameters`、
    `mcp_human_id` / `mcp_project_id` 全部走热刷新；
  - `key_pool._endpoint_rpm` 走热刷新，`_bucket` 检测配置速率变化时自动
    重建令牌桶（限流参数热生效）；
  - 搜索代理 `_fresh_settings` 迁移复用统一实现。
  - 仅 `mcp_transport` / `mcp_host` / `mcp_port` 变更仍需重启（无法热绑定端口）。
- **搜索代理 `/usage` 端点移除**：代理不再转发官方 `/usage`（用户确认不需要），
  官方用量展示走面板统计页（`sync_usage` 对每个 Key 单独查询聚合）；同时移除
  代理 `/usage` 路由、鉴权路径与对应测试（依赖 `GET /usage` 的客户端将收到 404，
  官方用量请改用面板统计页或 `usage --sync`）。
- **备份恢复后内存态重置**：`/api/restore` 成功后调用
  `pool.reset_runtime_state()`（清空限流桶 / `/usage` 用量缓存 / 异常与趋势
  缓存，并广播跨进程失效信号），避免恢复后旧限流状态残留。
- **面板 MCP 默认参数配置**：MCP 设置页新增「默认参数（JSON）」输入框 +
  「推荐预设」按钮（一键填入官方建议 `search_depth=advanced /
  chunks_per_source=3 / max_results=5`）；提示 `auto_parameters` 可能自动
  升级 `search_depth=advanced`（每次 2 积分）的成本风险。
- **用量趋势按来源拆分**：`get_usage_trend(days, source=)` 支持按
  `request_log.source`（mcp / proxy / cli）筛选；`/api/usage/trend?source=`
  透传；面板统计页新增来源下拉。
- **CLI `audit` 扩展**：输出近 24h 请求按接口 / 按来源统计，以及
  Research 任务看板概览（进行中 / 完成 / 失败）。

### Changed

- `/api/proxy/status` 的 `proxy_token` 改为**脱敏**返回（前 4 后 4 位 +
  `token_set` 标志），完整密钥仅由 `/api/settings` 提供（面板状态卡展示时
  自动从设置接口读取），避免状态接口明文带出密钥。
- `_save_research_key` 裁剪超过 1000 条映射时记录 warning 日志（原静默丢弃）。

### Fixed

- **🔴 streamable-http 模式下所有 MCP 请求 500**：`_wrap_bearer_auth` 原先用
  外层 Starlette + Mount 包裹 MCP app，而 Starlette 的 Mount **不会代理子 app
  的 lifespan**——mcp 的 `streamable_http_app()` 依赖 lifespan 调用 session
  manager 的 `run()` 初始化 task_group，lifespan 被阻断后 `_task_group` 恒为
  `None`，每个 `POST /mcp`（含 `mcp:list-tools`）都返回 500
  `"Task group is not initialized. Make sure to use run()."`。sse 模式每次连接
  自行 `run()`、不依赖 lifespan，故此前未暴露。修复：`_wrap_bearer_auth` 改为
  **纯 ASGI 中间件**（不新建 Starlette、不干预 lifespan scope，直接透传），
  内层 app 的 lifespan 正常触发；新增回归测试
  `test_bearer_auth_passes_through_lifespan`，并用真实 streamable-http 客户端
  验证 `initialize` / `tools/list` 均 200。
- **🔴 备份恢复在 Windows 上报「文件被占用」**：`KeyPool._get_conn` 的
  sqlite 连接默认 `check_same_thread=True`，`close_all_connections()` 从其他
  线程（如 restore 的 API 工作线程）`close()` 会静默抛 `ProgrammingError`，
  导致文件句柄不释放、`restore_from` 重命名 `tavily_keys.db` 失败
  （WinError 32）。修复：连接改 `check_same_thread=False`（每线程仍各自持有
  连接，仅允许跨线程关闭），且 `_get_conn` 检测本地连接已被关闭时自动重建，
  避免「Cannot operate on a closed database」。

## [0.6.0] - 2026-08-06

### Added

- **数据备份与恢复**：新模块 `app/backup.py`——
  - 备份集白名单：`config.json`、`tavily_keys.db`(+wal)、`research_keys.json`、`research_tasks_cache.json`、`.tavily-secret.key`（缺它恢复后无法解密 Key，必须备份；不含日志与 -shm 共享索引）；
  - 恢复安全策略：必需文件校验（配置/数据库/密钥缺一直接拒绝，防半成品破坏现有数据）、仅解压白名单条目（防路径穿越/zip 炸弹）、恢复前现有文件改名 `.pre-restore-<ts>` 绝不静默覆盖、清理旧 WAL 共享索引；
  - CLI 新增 `backup [path]`（默认系统临时目录）/ `restore <zip>` 子命令；面板「设置」页新增「数据备份与恢复」卡片（下载备份 / 上传恢复，恢复自动停止 MCP 与搜索代理子进程并释放数据库连接）；新 API `POST /api/backup`、`POST /api/restore`；`Tavily.spec` 打包 `backup`。
- **按端点分组限流**：新配置 `endpoint_rpm`（默认 `search`/`extract`/`crawl`/`map`=90、`research`=18，未覆盖端点回退 `rate_limit_rpm`）；每 key × 每 endpoint 独立令牌桶；research「创建任务」独立 18 RPM 桶（官方上限 20 留 10% 余量，可按需调整），状态轮询/看板查询走默认桶。
- **搜索代理支持 Research**：`POST /research`（提交返回 request_id，参数白名单含 output_length / output_schema / max_sources / max_subsources）+ `GET /research/{id}`（状态查询，跨进程共享 `research_keys.json` 固定同一 key）；`/research` 纳入代理鉴权路径。
- **请求来源标记**：`request_log` 新增 `source` 列（`mcp`/`proxy`/`cli`，自动迁移）；`/api/logs` 与 CSV 导出新增 `source` 筛选；面板日志页新增「来源」下拉。
- **X-Session-Id**：MCP 进程级会话 ID（进程启动自动生成，经 SDK `session_id` 参数转发，对齐官方 per-process session 行为）。

### Changed

- `rate_limit_rpm` 语义调整为「按端点限流兜底」（默认值 90 不变；未配置 `endpoint_rpm` 时行为与旧版完全一致）。

## [0.5.0] - 2026-08-06

### Added

- **网络搜索代理（Tavily 兼容 REST 服务）**：把 Key 池暴露为官方 Tavily API 形态，
  供 Cherry Studio 等 AI 客户端通过「自定义 API 地址」直接对接——
  - 新模块 `app/tavily_proxy.py`：实现 `POST /search`、`POST /extract`、`POST /crawl`、
    `POST /map`、`GET /usage` 全部官方端点，内部走 Key 池轮询/限流/异常切换
    （复用 `mcp_server._run_with_retry`），额度与日志自动落账；四个转发端点均强制
    `include_usage=True` 保证本地积分记录；错误按类别映射为 Tavily 风格
    `{"detail":{"error":...}}`（auth→401、quota→432、rate→429、池空→503）。
  - 新模块 `app/proxy_manager.py`：独立子进程管理（`--proxy` 角色），默认监听
    `0.0.0.0:8002`，日志 `data/proxy_server.log`，安全停止只清理自己拉起的进程。
  - 新配置项：`proxy_auto_start`（随软件自启）、`proxy_host`（默认 `0.0.0.0`）、
    `proxy_port`（默认 8002）、`proxy_token`（独立代理密钥，**留空则不鉴权=开放**）。
  - 面板新增「**搜索代理**」页签（第 7 个）：运行状态/启停、API 地址三形式
    （局域网 IP / 主机名 / 本机，各带复制）、API 密钥（复制 + 「生成随机密钥」，
    对应 `POST /api/proxy/token/generate`）、随软件启动/监听地址/端口/密钥设置；
    `proxy_token` 为空时提示对外开放风险。
  - 新 API：`GET /api/proxy/status`、`POST /api/proxy/start`、`POST /api/proxy/stop`、
    `POST /api/proxy/token/generate`。
  - CLI 新增 `proxy` 子命令（展示状态/地址/密钥）；`Tavily.spec` 打包 `tavily_proxy`。
  - 对接方式：客户端（如 Cherry Studio 网络搜索 → Tavily 提供商）「API 地址」填
    `http://<主机>:8002`、「API 密钥」填 `proxy_token`，点「检测」即验证连通。
- **通用 TTL 缓存基础设施**：新增 `app/cache.py`（`TTLCache` + `ttl_cached` 装饰器，单调时钟、线程安全、跨进程失效信号 `data/cache_invalidate.sig`）；KeyPool 重计算类接口（异常识别、用量趋势）与 Dashboard API 端点（`/api/logs`、MCP/代理状态）接入短 TTL 缓存，写操作自动失效，`cache_ttls` 配置可调（0 = 关闭）。
- **Research 任务看板持久化**：终态任务摘要落盘 `data/research_tasks_cache.json`（7 天 TTL、写盘节流、原子替换、启动恢复），重启后不再重调官方 API；查询改线程池并发 + 单次 10s 超时。

### Changed

- **高错误率识别忽略客户端请求错误**：`high_error_rate` 判定只统计服务器/Key 侧错误
  （401/403/429/432/433/5xx/超时/网络等），排除客户端请求错误（HTTP 400/参数校验失败，
  SDK 抛 `BadRequestError`），避免客户端参数错误虚增错误率导致误报；`request_log` 新增
  `is_client_error` 列（记录失败时按异常类型判定），错误率分子分母均排除客户端错误。
  - `_classify_error` 新增 `bad_request` 类别；代理层把 400 类错误正确映射为 HTTP 400
    （此前落到 500）。

### Fixed

- **搜索代理密钥不生效**：代理为独立子进程，`settings.get_settings()` 是进程内缓存，
  启动后不感知 `config.json` 的变更（如面板生成/修改 `proxy_token`），导致设置了密钥
  仍不鉴权。修复：鉴权前按 TTL（1s）调用 `settings.reload()` 刷新缓存，密钥/限流等
  配置修改后**无需重启代理进程**即生效；新增回归测试
  `test_token_change_picked_up_without_restart`。
- **SVG 图表悬停明细不显示**：统计页图表 `<title>` 移入 rect 元素内，修复悬停 tooltip 失效。

## [0.4.0] - 2026-08-05

### Added

- **MCP 地址多形式展示**：面板 MCP 服务地址区同时显示三种地址，各带复制按钮——
  - **局域网 IP 地址**（随网络切换变化，如 `http://192.168.1.20:8001/sse`）；
  - **主机名地址**（`http://<主机名>.local:8001/sse`，mDNS，**切换网络不变**，客户端需支持 `.local` 解析；Windows 设备也可用裸主机名 `http://<主机名>:8001/sse`）；
  - **本机地址**（`http://127.0.0.1:8001/sse`，仅本机、不依赖网络）。
  - `settings.mcp_urls()` 生成地址集合（ip / hostname / hostname_local / local），`mcp_manager.status()` 与 `/api/mcp/status` 新增 `urls` 字段（`url` 字段保持兼容）；监听仅 `127.0.0.1` 时面板提示「仅本机，局域网设备无法访问」，监听 `0.0.0.0` 时提示建议客户端使用主机名地址。
- **Research 流式三段式超时加固**（对齐官方 tavily-mcp）：
  - **整体 deadline**：流式消费受 `timeout` 总时长约束（默认按 model 区分：`mini`/`auto` 300s、`pro` 900s，显式传参可覆盖），杜绝滴灌流无限等待；
  - **头部/连接超时**：30s 内未建立连接视为失败（`requests timeout=(connect, read)` 元组的 connect 段）；
  - **idle 容忍**：单 chunk 读超时放宽到 300s（read 段），容忍官方报告生成阶段数分钟的静默期，不再误杀本可成功的 research；
  - **超时不回退**：流已开始后的中断（整体超时/读异常）返回 `{"status":"timeout"|"error", content: 部分内容}` 并显式关闭连接，**不再回退「提交+轮询」**（服务端任务可能仍在运行，回退会造成重复任务双倍消耗）；仅提交阶段失败才回退。
- **尊重 429 `retry-after`**：wrap SDK 错误处理把响应 `retry-after` 头附着到异常上（SDK 原生异常不带 headers），`_run_with_retry` 与 research 提交循环按值处理——`<5s` 同 key 等待重试（不白白消耗别的 key 限流预算）、`≥5s` 切换其他 key；无头 429 保持不重试。
- **`mcp_human_id` 接线**：此前配置存在但从未传入 TavilyClient（死配置），现统一经 `_client_for()` 构造（`TavilyClient(key, human_id=...)` + 默认超时 + retry-after patch），`X-Human-Id` 头真正生效。
- **request_log 保留策略**：新增 `log_retention_days` 配置（默认 90 天，`0`=不清理），`KeyPool.prune_request_log()` 每插入 500 条请求日志周期清理超期记录，防止 `request_log` 长期运行无界增长。
- **Research 新参数**：`tavily_research` 新增 `output_length`（`short`/`standard`/`long`，枚举忽略大小写）、`output_schema`（传入 JSON Schema 后 `content` 直接返回结构化对象，AI Agent 无需再解析 Markdown；流式路径 `delta.content` 为对象时直接收集，字符串片段整体尝试 JSON 解析）、`max_sources`（3–10）/ `max_subsources`（1–8）来源数上限；非法值本地返回友好错误、不触达 API。
- **`X-Project-ID` 支持**：新增 `mcp_project_id` 配置，经 SDK 原生 `project_id` 参数转发 `X-Project-ID` 头，按项目归类用量；面板「MCP 服务」设置页新增「项目归属 ID」「会话归属 ID」（`mcp_human_id`，此前仅配置文件可改）输入框。
- **用量趋势图 / 统计页**：新增 `/api/usage/trend?days=7|30`（按本地时区按天聚合请求数/成功/失败/积分/按 endpoint 拆分，自动补齐无请求日期）；面板新增独立「**统计**」页签——顶部关键指标卡（总请求/近 24h 成功失败与成功率/总积分/剩余积分/活跃 Key）+ 用量趋势图（轻量 SVG 堆叠柱状图，无外部 CDN 依赖，离线可用，悬停查看明细）+ 接口分布（近 24h 各接口成功/失败横向条形）+ 积分消耗按天柱状图，均支持近 7/30 天切换。
- **异常通知**：新增 `notify.py` 模块 + 后台周期检测（首次约 30s、此后按 `notify_interval_minutes`，默认 5 分钟）——
  - **Webhook**（`notify_webhook` 配置，POST JSON，兼容钉钉/企业微信/Server酱）；
  - **Windows 托盘气泡**（`notify_tray` 配置，`TrayIcon.notify()` 新增 `NIF_INFO` 气球提示，任意线程可调）；
  - **去重/节流**：同一 (key, flag) 一小时只通知一次；新增**池空告警**（存在 key 但全部不可用）半小时去重。
- **Research 任务看板**：新增 `/api/research/tasks` 与 `mcp_server.list_research_tasks()`（复用按 key 隔离的 status 查询 + TTL 缓存：非终态 30s / 终态 300s + 单次刷新最多查 15 个，避免烧官方限流）；面板新增「Research 任务」页签展示 request_id / 归属 key / 状态 / 结果摘要。
- **请求日志筛选与导出**：新增 `/api/logs`（endpoint / key 掩码 / 成功失败 / 时间范围 + 分页）与 `/api/logs/export.csv`（同筛选导出 CSV）；面板日志页新增筛选栏、分页与「导出 CSV」按钮（前端 fetch 带鉴权头下载）。

### Changed

- `tavily_research` 的 `timeout` 默认值由 `300.0` 改为 `None`（按 model 计算：`mini`/`auto` 300s、`pro` 900s），显式传入仍优先。
- 面板页签由 4 个增至 6 个（新增「统计」「Research 任务」）；请求日志从 `/api/stats` 内嵌改为独立的 `/api/logs` 分页查询。
- **Research 任务页头部美化**：改为卡片式头部——左侧紫色图标块 + 标题 + 状态徽章（彩色圆点 + 「共 N · 已结束 N」，全部结束时圆点为绿色），副标题以 `<code>` 样式展示 `wait=false`，右侧主色「刷新」按钮（含图标）。

### Fixed

- **Research 任务看板显示不全 / 长时间空白**：
  - 看板响应不再全量返回研究报告 `content`（全文可达数百 KB，导致响应巨大、页面长时间停留在空白/「加载中」），后端截断为 200 字符摘要（`_research_content_preview`）；
  - 任务表格改 `table-layout:fixed` 并分配列宽，「详情」列不再因 `white-space:nowrap` 把表格横向撑出可视区；
  - 加载中 / 加载失败现在有明确反馈（「加载中…」/「加载失败：原因」），不再出现无提示的空白表格；
  - `_load_research_keys()` 改用 `utf-8-sig` 读取：带 BOM 的 UTF-8 文件（某些编辑器/工具会写入）此前会令 `json.loads` 抛异常、整份映射静默回退为空，导致看板永远显示「暂无任务」——现已与 `settings.load()` 的 BOM 兼容策略一致。

### Tests

- 新增 31 个用例（93 → 124 全绿）：流式整体超时返回部分内容并关闭连接、流读取中断返回部分内容、流超时**不回退**提交+轮询、默认超时按 model、429 短 retry-after 同 key 重试 / 长 retry-after 切 key、错误处理 patch 幂等与附着、`mcp_human_id` 接线与空值处理、request_log 保留清理与配置关闭、`output_length` 透传/非法值拦截、`max_sources`/`max_subsources` 校验与透传、`output_schema` 结构化流式返回/非 dict 拦截、`mcp_project_id` 接线与 settings 字段校验、趋势聚合、日志筛选/分页、看板缓存与查询上限、四个新 API 端点、notify 去重/托盘/池空/无渠道、BOM 兼容读取 `research_keys.json`。修复 research 映射测试对真实 `data/research_keys.json` 的污染（autouse 隔离 fixture）。

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

[0.7.0]: https://github.com/zylyes/tavily-key/releases/tag/v0.7.0
[0.6.0]: https://github.com/zylyes/tavily-key/releases/tag/v0.6.0
[0.5.0]: https://github.com/zylyes/tavily-key/releases/tag/v0.5.0
[0.4.0]: https://github.com/zylyes/tavily-key/releases/tag/v0.4.0
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
