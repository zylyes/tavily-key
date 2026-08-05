# Wiki 导航

## CLI使用

- [CLI 使用](CLI使用/CLI使用.md) — CLI 工具的命令列表、通用参数及使用示例，作为快速索引。
- [Key 管理命令](CLI使用/Key管理命令.md) — add、list、activate、deactivate、remove 等管理命令的详细用法。
- [统计与健康命令](CLI使用/统计与健康命令.md) — stats、health、recent 子命令的用法，用于查看用量、执行健康检查、查询最近请求日志。

## Web控制台

- [Web Dashboard](Web控制台/Web控制台.md) — Dashboard 的整体介绍：功能、启动方式、访问地址和页面组成。
- [界面使用](Web控制台/界面使用.md) — Dashboard 各区域的交互细节，包括添加 Key、查看日志、执行健康检查等操作。
- [API 接口](Web控制台/API接口.md) — Dashboard 后端暴露的 HTTP 端点及其请求/响应格式。

## 快速开始

- [快速开始](快速开始/快速开始.md) — 从零开始安装依赖、导入 API Key、启动 MCP Server 和 Dashboard 的完整步骤。
- [安装与配置](快速开始/安装与配置.md) — 详细说明依赖安装、Python 环境准备，以及 key 的导入与验证配置。
- [启动服务](快速开始/启动服务.md) — 启动 MCP Server 和 Dashboard 的方式，含端口指定和后台运行技巧。

## 架构设计

- [架构设计](架构设计/架构设计.md) — 系统整体架构：key_pool 为核心，CLI、Dashboard、MCP Server 三端共用。
- [数据模型](架构设计/数据模型.md) — SQLite 数据库两张表的字段、类型、索引及业务含义。
- [模块划分](架构设计/模块划分.md) — key_pool、cli、dashboard、mcp_server 各模块的功能边界与相互调用关系。

## 核心功能

- [核心功能](核心功能/核心功能.md) — 概述项目的核心能力：Key 自动轮询、失效自动停用、用量统计、请求日志，以及对外提供的 MCP 工具集。
- [Key 轮询与健康检查](核心功能/Key轮询与健康检查.md) — 解释 KeyPool 如何自动轮询选择可用 key，以及健康检查如何识别失效 Key 并自动停用。
- [用量追踪与日志](核心功能/用量追踪与日志.md) — 介绍 SQLite 中的 api_keys 和 request_log 表，以及 CLI/Dashboard 中查看统计数据的方法。
- [MCP 工具集](核心功能/MCP工具集.md) — 列出 MCP Server 对外暴露的搜索、提取、爬取、地图和研究等工具及其参数。

## 部署运维

- [部署运维](部署运维/部署运维.md) — 生产环境部署要点：启动脚本、systemd 服务、依赖管理、数据库重置等。
- [systemd 自启](部署运维/systemd自启.md) — 配置 Dashboard 开机自启的系统服务文件及常用 systemctl 命令。
- [故障排查](部署运维/故障排查.md) — 常见问题与解决方法：端口占用、数据库异常、Key 失效、venv 路径问题等。

## 项目概述

- [项目概述](项目概述/项目概述.md) — Tavily API Key Pool 的核心介绍：集中管理多个 Tavily API key，实现自动轮询分发、用量统计与健康检查。
