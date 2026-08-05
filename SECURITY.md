# 安全说明

## 项目性质

Tavily API Key Pool 用于**集中管理多个 Tavily API key**，涉及密钥存取、轮询分发与用量统计，属于**敏感工具**。任何部署和贡献行为都应遵循本文件的安全要求。

## 漏洞报告

如果你发现了安全漏洞（如鉴权绕过、密钥泄露、任意代码执行等），**请不要创建公开 issue**，按以下方式报告：

1. **首选：GitHub Private Vulnerability Reporting** —— 在仓库页面的 "Security" 标签页中使用 "Report a vulnerability" 私密提交，我们会尽快响应。
2. **备选：直接开 issue 并标注敏感** —— 创建 issue 时标题以 `[SECURITY]` 开头，并在正文中**避免出现任何真实密钥、令牌或完整的本地路径**，仅描述问题类型、影响范围与复现思路。

请勿在公开渠道（issue、PR、讨论区）中粘贴真实的 API key、`auth_token` 或 `config.json` 内容。

## 安全使用建议

### 必须

- **设置访问令牌**：Web 控制台「设置」页配置 `auth_token`，或直接编辑 `data/config.json` 中的 `auth_token` 字段。设置后所有 `/api/*` 请求必须携带 `X-Auth-Token` 请求头。
- **公网部署务必启用鉴权**：将服务部署到公网（Linux Server 形态，域名对外服务）之前，**必须**设置 `auth_token`；同时建议配合 Nginx 反向代理限制访问来源。未设置令牌的实例暴露在公网时，任何能访问到端口的人都可以读取、新增、删除你的 API key。

### 密钥与配置文件管理

- **不要提交 `config.json` 与 `keys.txt` 到版本库**：两者包含敏感配置与 API key。请确保它们被 `.gitignore` 排除，且不要在 issue、PR、截图或日志中泄露其内容。
- **API key 脱敏**：在日志、issue、文档示例中展示 key 时，只保留前 4 位与后 4 位，中间用星号代替（如 `tvly-xxxx****yyyy`）。
- **数据库文件**：`tavily_keys.db` 包含全部 key 与请求日志，等同机密文件。备份、迁移时注意保管，不要提交到版本库，也不要通过不安全的通道传输。

### 部署环境

- **Linux Server 部署**：
  - 注意 `.tavily-secret.key`（密钥加密文件）的权限，仅允许运行服务的系统用户可读（如 `chmod 600`），避免被其他用户读取。
  - 遵循 `deploy/linux-server/README.md` 中的指引，使用 systemd 服务 + Nginx 反向代理，并启用 HTTPS。
  - 及时更新系统与依赖，避免已知漏洞。
- **Windows Local 部署**：默认监听 `0.0.0.0:8001` 供局域网访问，请确认局域网环境可信；如仅本机使用，可将监听地址改为 `127.0.0.1`。

### 其他

- 妥善保管 `auth_token`，不要在浏览器书签、聊天记录等地方明文留存。
- 定期通过健康检查与用量统计核对 key 使用情况，发现异常访问及时更换 key 与令牌。
