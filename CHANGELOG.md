# Changelog

本项目所有重要变更均记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
