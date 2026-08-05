---
name: Bug 报告
about: 报告一个 Bug，帮助我们改进
title: "[Bug] 简短描述问题"
labels: bug
assignees: ''

---

**描述问题**

请清晰、简洁地描述这个 Bug 是什么，以及它造成的影响。

**复现步骤**

1. 执行 `...`
2. 点击 `...`
3. 看到错误 `...`

**期望行为**

描述你期望发生什么。

**实际行为**

描述实际发生了什么（可附错误信息、截图）。

**环境信息**

- 操作系统：Windows / Linux / macOS（请注明版本）
- Python 版本（源码运行时）：如 3.12
- 部署形态：源码运行 / Linux Server（systemd + Nginx）/ Windows Local（Tavily.exe）
- 项目版本：如 v0.3.0（可通过 `git describe` 或发布版本号确认）
- 浏览器（如与 Web 控制台相关）：如 Chrome 126

**日志与配置（注意脱敏）**

- 相关日志位置（按部署形态）：
  - 源码运行：项目根目录下 `dashboard_server.log`、`mcp_server.log`、`tavily.log`
  - Windows Local：`Tavily.exe` 同目录下的日志文件
  - Linux Server：`journalctl -u tavily-dashboard`、`journalctl -u tavily-mcp`
- 如有必要可粘贴日志片段，但请**务必脱敏**：
  - API key 只保留前 4 位和后 4 位（如 `tvly-xxxx****yyyy`）
  - 不要粘贴 `config.json` 中 `auth_token` 的真实值（可替换为 `<已脱敏>`）
  - 不要粘贴完整 `config.json` 与 `keys.txt` 内容
