# 贡献指南

感谢你愿意为 Tavily API Key Pool 贡献代码！在提交贡献之前，请阅读本指南。

本项目是一个管理多个 Tavily API key、自动轮询分发并追踪用量的工具，包含 Web 控制台（Dashboard）与 MCP Server 两种形态。本项目所有文档均为中文。

## 目录

- [开发环境搭建](#开发环境搭建)
- [运行测试](#运行测试)
- [代码风格](#代码风格)
- [提交变更](#提交变更)
- [发起 Pull Request](#发起-pull-request)

## 开发环境搭建

要求：Python 3（建议 3.10+）。

```bash
# 1. 克隆仓库并进入项目根目录
git clone <仓库地址>
cd <项目根目录>

# 2. 创建并激活虚拟环境
python -m venv .venv
# Linux / macOS：
source .venv/bin/activate
# Windows：
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt
```

首次启动会自动生成 `config.json`（运行配置）与 `tavily_keys.db`（SQLite 数据库），无需手动初始化。

## 运行测试

项目使用 pytest，测试位于 `tests/` 目录。提交代码前请确保测试全部通过：

```bash
python -m pytest
```

如需查看详细的覆盖率或调试单个用例，可指定测试文件或用例名：

```bash
python -m pytest tests/test_key_pool.py
python -m pytest -k <用例名关键字>
```

## 代码风格

- 使用 **Python 3** 语法，保持与现有代码一致的风格
- 业务代码遵循现有模块结构，放在 `app/` 下：
  - `app/cli.py` — CLI 入口
  - `app/dashboard.py` — Web 控制台（FastAPI）
  - `app/mcp_server.py` / `app/mcp_manager.py` — MCP 服务本体与子进程管理
  - `app/key_pool.py` / `app/settings.py` / `app/security.py` 等 — 核心逻辑、配置读写、鉴权
- 新功能建议附带对应测试（参考 `tests/` 下的现有测试）
- 文档类内容使用中文；代码中的标识符、注释语言保持与周边代码一致

### 本地验证命令（改动手动验证）

修改后除了跑测试，可按需手动验证：

```bash
python app/cli.py list                    # 列出所有 key
python app/cli.py stats                   # 用量统计
python app/cli.py health                  # 健康检查，自动停用失效 key
python app/cli.py recent -n 20            # 最近 20 条请求日志
```

启动 Web 控制台：

```bash
# Linux / macOS
./scripts/run_dashboard.sh
# Windows
scripts\run_dashboard.bat
```

## 提交变更

- 提交信息建议使用如下前缀（Conventional Commits 风格），主题用中文描述：

  | 前缀 | 用途 |
  | --- | --- |
  | `feat:` | 新功能 |
  | `fix:` | 修复 Bug |
  | `docs:` | 文档变更 |
  | `refactor:` | 重构（不改变行为） |
  | `test:` | 测试相关 |
  | `chore:` | 构建、依赖等杂项 |

  示例：`feat: 支持 least-used 轮询策略`、`fix: 修复健康检查误停用活跃 key`

- 提交前检查：**不要**提交 `config.json`、`keys.txt`、`tavily_keys.db`、日志文件以及任何包含真实 API key 或令牌的内容（参见 [SECURITY.md](SECURITY.md)）

## 发起 Pull Request

1. **Fork** 本仓库到你的账号
2. 从主分支创建**功能分支**：`git checkout -b feature/<你的变更描述>`
3. 在分支上完成修改并补充测试
4. 推送分支到你的 Fork：`git push origin feature/<你的变更描述>`
5. 向本仓库提交 **Pull Request**，并填写 PR 模板（变更摘要、动机、测试验证、检查清单）
6. 等待维护者 review；如有修改意见，直接在分支上继续提交即可

> 建议在 PR 描述中关联相关 issue（如 `Closes #123`）。

## 其他

- 遇到问题可以查阅 [README.md](README.md) 与 `docs/` 目录
- 更多行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- 安全问题请勿直接提 issue，参见 [SECURITY.md](SECURITY.md) 中的漏洞报告方式
